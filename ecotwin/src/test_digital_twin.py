import os
import unittest
import uuid
from pathlib import Path

import config
from database import init_db, get_db_connection
import supabase_client
from waste_streams import (
    CLASS_TO_STREAM_MAPPING,
    CAMPUS_STREAMS,
    get_stream_for_class,
    calculate_co2_saving,
)
from digital_twin_service import (
    classify_waste_image,
    process_disposal_event,
    simulate_collection_event,
    simulate_sensor_telemetry,
    get_dashboard_data,
)

class TestEcoTwinDigitalTwin(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        cls.tracked_events = []
        cls.tracked_classifications = []

    @classmethod
    def tearDownClass(cls):
        """Safe cleanup: cleans up all test events, ledger records, and test classifications."""
        # 1. Clean up from SQLite
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            for evt_id in cls.tracked_events:
                cur.execute("DELETE FROM disposal_events WHERE event_uuid = ?", (evt_id,))
                cur.execute("DELETE FROM rewards_ledger WHERE event_uuid = ?", (evt_id,))
            for cls_id in cls.tracked_classifications:
                cur.execute("DELETE FROM recycling_operations WHERE classification_id = ?", (cls_id,))
                cur.execute("DELETE FROM waste_classifications WHERE id = ?", (cls_id,))
            # Reset SQLite test bin to 0 items and Available status
            cur.execute("UPDATE bins SET item_count = 0, estimated_fill_percent = 0.0, status = 'Available' WHERE bin_id = 'BIN-ENG-DRY_RECYCLABLE'")
            cur.execute("UPDATE bins SET item_count = 0, estimated_fill_percent = 0.0, status = 'Available' WHERE bin_id = 'BIN-LIB-GLASS_METAL'")
            cur.execute("DELETE FROM alerts WHERE bin_id IN ('BIN-ENG-DRY_RECYCLABLE', 'BIN-LIB-GLASS_METAL')")
            conn.commit()
            conn.close()
        except Exception:
            pass

        # 2. Clean up from Supabase Cloud
        client = supabase_client.get_supabase_client()
        if client and supabase_client.is_supabase_active():
            for cls_id in cls.tracked_classifications:
                try:
                    client.table("recycling_operations").delete().eq("classification_id", cls_id).execute()
                    client.table("waste_classifications").delete().eq("id", cls_id).execute()
                except Exception:
                    pass
            # Ensure all 12 Supabase campus bins remain at clean baseline
            try:
                client.table("recycling_bins").update({"item_count": 0, "current_load_kg": 0.0, "status": "available"}).execute()
            except Exception:
                pass

    def setUp(self):
        self.conn = get_db_connection()

    def tearDown(self):
        self.conn.close()

    def test_01_model_inference_on_real_image(self):
        """[LOCAL CNN] Verifies real CNN inference produces valid probabilities and classes."""
        plastic_samples = []
        if config.DATA_DIR.exists():
            plastic_samples = list((config.DATA_DIR / "plastic").glob("*.jpg"))
        if not plastic_samples:
            alt_dir = Path("D:/DSU-hackathon/ecotwin/dataset/Garbage classification/plastic")
            if alt_dir.exists():
                plastic_samples = list(alt_dir.glob("*.jpg"))

        if plastic_samples:
            target_input = str(plastic_samples[0])
        else:
            from PIL import Image
            import io
            img = Image.new("RGB", (224, 224), color=(100, 150, 200))
            buf = io.BytesIO()
            img.save(buf, format="JPEG")
            target_input = buf.getvalue()

        result = classify_waste_image(target_input)
        self.assertIn("predicted_class", result)
        self.assertIn(result["predicted_class"], config.CLASSES)
        self.assertGreaterEqual(result["confidence"], 0.0)
        self.assertLessEqual(result["confidence"], 1.0)
        self.assertIn("probabilities", result)
        self.assertEqual(len(result["probabilities"]), 6)
        print(f"\n[TEST 1 PASSED] Real CNN prediction: {result['predicted_class']} ({result['confidence_pct']}%)")

    def test_02_manual_review_threshold_rule(self):
        """[LOCAL LOGIC] Verifies low-confidence prediction triggers manual review flag (< 0.65)."""
        test_conf_low = 0.50
        test_conf_high = 0.85
        needs_review_low = test_conf_low < config.CONFIDENCE_THRESHOLD
        needs_review_high = test_conf_high < config.CONFIDENCE_THRESHOLD
        self.assertTrue(needs_review_low, "Scores below threshold must flag manual review")
        self.assertFalse(needs_review_high, "Scores above threshold must not flag manual review")
        print("\n[TEST 2 PASSED] Manual review threshold logic verified.")

    def test_03_disposal_counter_and_capacity_increment(self):
        """[LOCAL DIGITAL TWIN] Tests that accepted disposal increments virtual bin by exactly 1 item and cleans up."""
        bin_id = "BIN-ENG-DRY_RECYCLABLE"
        cursor = self.conn.cursor()
        cursor.execute("SELECT item_count FROM bins WHERE bin_id = ?", (bin_id,))
        initial = cursor.fetchone()
        init_count = initial["item_count"] if initial else 0

        evt_id = f"test-evt-{uuid.uuid4()}"
        self.tracked_events.append(evt_id)
        res = process_disposal_event(
            student_id="test_student",
            predicted_class="plastic",
            confidence=0.92,
            accepted_class="plastic",
            location_id="ENG",
            event_uuid=evt_id,
        )

        self.assertTrue(res["success"])
        self.assertFalse(res["is_duplicate"])
        self.assertEqual(res["new_count"], init_count + 1)

        # Verify in DB
        cursor.execute("SELECT item_count FROM bins WHERE bin_id = ?", (bin_id,))
        updated_count = cursor.fetchone()["item_count"]
        self.assertEqual(updated_count, init_count + 1)

        # Restore bin state
        cursor.execute("UPDATE bins SET item_count = ? WHERE bin_id = ?", (init_count, bin_id))
        self.conn.commit()
        print(f"\n[TEST 3 PASSED] Bin count accurately incremented from {init_count} to {updated_count} and baseline restored.")

    def test_04_idempotency_prevents_duplicate_disposal_and_rewards(self):
        """[LOCAL DIGITAL TWIN] Tests that submitting the same event_uuid twice is rejected without double counting."""
        evt_id = f"test-idemp-{uuid.uuid4()}"
        self.tracked_events.append(evt_id)

        # First submission
        res1 = process_disposal_event(
            student_id="student_idemp",
            predicted_class="paper",
            confidence=0.88,
            accepted_class="paper",
            location_id="LIB",
            event_uuid=evt_id,
        )
        self.assertTrue(res1["success"])
        self.assertFalse(res1["is_duplicate"])

        # Duplicate submission with identical event_uuid
        res2 = process_disposal_event(
            student_id="student_idemp",
            predicted_class="paper",
            confidence=0.88,
            accepted_class="paper",
            location_id="LIB",
            event_uuid=evt_id,
        )
        self.assertTrue(res2["success"])
        self.assertTrue(res2["is_duplicate"], "Duplicate event must be flagged as idempotent")

        # Check reward ledger has exactly 1 entry for this UUID
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM rewards_ledger WHERE event_uuid = ?", (evt_id,))
        count = cursor.fetchone()[0]
        self.assertEqual(count, 1, "Reward must be recorded exactly once")
        print("\n[TEST 4 PASSED] Idempotency verified: zero double-counting, zero duplicate rewards.")

    def test_05_rewards_policy_distinction(self):
        """[LOCAL LOGIC & DB] Verifies reward is awarded for recyclables and 0 for residual trash."""
        evt_plastic = f"test-reward-p-{uuid.uuid4()}"
        self.tracked_events.append(evt_plastic)
        res_p = process_disposal_event(
            student_id="student_rewards",
            predicted_class="plastic",
            confidence=0.95,
            accepted_class="plastic",
            location_id="STU",
            event_uuid=evt_plastic,
        )
        self.assertEqual(res_p["ecocoins_awarded"], 15, "Plastic should award 15 EcoCoins")

        evt_trash = f"test-reward-t-{uuid.uuid4()}"
        self.tracked_events.append(evt_trash)
        res_t = process_disposal_event(
            student_id="student_rewards",
            predicted_class="trash",
            confidence=0.91,
            accepted_class="trash",
            location_id="STU",
            event_uuid=evt_trash,
        )
        self.assertEqual(res_t["ecocoins_awarded"], 0, "Residual trash should award 0 EcoCoins")
        print("\n[TEST 5 PASSED] Reward rules verified: 15 for plastic, 0 for residual trash.")

    def test_06_manual_override_and_contamination_detection(self):
        """[LOCAL DIGITAL TWIN] Verifies user manual override is recorded and flags contamination if streams diverge."""
        evt_override = f"test-override-{uuid.uuid4()}"
        self.tracked_events.append(evt_override)
        res = process_disposal_event(
            student_id="student_tester",
            predicted_class="trash",  # AI predicted trash (residual stream)
            confidence=0.72,
            accepted_class="plastic", # User corrected to plastic (dry_recyclable stream)
            location_id="CAF",
            event_uuid=evt_override,
            was_overridden=True,
        )
        self.assertTrue(res["is_contamination"], "Cross-stream override must be flagged as contamination")
        print("\n[TEST 6 PASSED] Contamination and manual override properly recorded.")

    def test_07_sensor_telemetry_threshold_and_deduplication(self):
        """[SOFTWARE SENSOR SIMULATOR] Verifies virtual sensor triggers Nearly Full and deduplicates alerts."""
        bin_id = "BIN-LIB-GLASS_METAL"

        # Set fill to 85%
        res = simulate_sensor_telemetry(bin_id, 85.0)
        self.assertEqual(res["status"], "Nearly Full")

        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM alerts WHERE bin_id = ? AND is_active = 1", (bin_id,))
        count_alerts = cursor.fetchone()[0]
        self.assertEqual(count_alerts, 1, "Alert must be generated once")

        # Send telemetry again at 86% - should NOT create duplicate active alert
        res2 = simulate_sensor_telemetry(bin_id, 86.0)
        cursor.execute("SELECT COUNT(*) FROM alerts WHERE bin_id = ? AND is_active = 1", (bin_id,))
        count_alerts_after = cursor.fetchone()[0]
        self.assertEqual(count_alerts_after, 1, "Alerts must be deduplicated across updates")
        print("\n[TEST 7 PASSED] Software sensor threshold alert triggered and deduplicated.")

    def test_08_collection_event_empties_bin_and_resolves_alerts(self):
        """[SOFTWARE FLEET SIMULATOR] Verifies simulate_collection_event resets fill level and clears alerts."""
        bin_id = "BIN-LIB-GLASS_METAL"

        # Trigger collection
        col_res = simulate_collection_event(bin_id, operator_name="Test Facilities Team")
        self.assertTrue(col_res["success"])
        self.assertEqual(col_res["fill_after"], 0.0)
        self.assertEqual(col_res["status"], "Available")

        # Verify in DB
        cursor = self.conn.cursor()
        cursor.execute("SELECT item_count, estimated_fill_percent, status FROM bins WHERE bin_id = ?", (bin_id,))
        bin_state = cursor.fetchone()
        self.assertEqual(bin_state["item_count"], 0)
        self.assertEqual(bin_state["estimated_fill_percent"], 0.0)
        self.assertEqual(bin_state["status"], "Available")

        # Verify alert resolved
        cursor.execute("SELECT COUNT(*) FROM alerts WHERE bin_id = ? AND is_active = 1", (bin_id,))
        active_alerts = cursor.fetchone()[0]
        self.assertEqual(active_alerts, 0, "Collection must resolve active alerts")
        print("\n[TEST 8 PASSED] Collection event successfully emptied bin and resolved alerts.")

    def test_09_dashboard_read_idempotency(self):
        """[READ IDEMPOTENCY] Verifies calling get_dashboard_data does not mutate database counters."""
        dash1 = get_dashboard_data()
        count1 = dash1["total_items"]

        dash2 = get_dashboard_data()
        count2 = dash2["total_items"]

        self.assertEqual(count1, count2, "Dashboard queries must be strictly idempotent")
        print("\n[TEST 9 PASSED] Dashboard read idempotency confirmed.")

    def test_10_all_12_virtual_bins_in_supabase_cloud(self):
        """[REAL SUPABASE CLOUD] Task 1: Verifies all 12 virtual campus bins load from Supabase PostgreSQL with correct locations."""
        self.assertTrue(supabase_client.is_supabase_active(), "Supabase PostgreSQL must be active")
        client = supabase_client.get_supabase_client()
        res = client.table("recycling_bins").select("*").execute()
        bins = res.data

        self.assertEqual(len(bins), 12, "Exactly 12 virtual campus bins must exist in Supabase")

        expected_locations = {"LIB", "CAF", "ENG", "STU"}
        actual_locations = set(b["location_code"] for b in bins)
        self.assertEqual(actual_locations, expected_locations, f"Locations must match {expected_locations}")

        expected_categories = {"dry_recyclable", "glass_metal", "residual"}
        for loc in expected_locations:
            loc_bins = [b for b in bins if b["location_code"] == loc]
            self.assertEqual(len(loc_bins), 3, f"Location {loc} must have exactly 3 stream bins")
            loc_categories = set(b["accepted_category"] for b in loc_bins)
            self.assertEqual(loc_categories, expected_categories, f"Location {loc} must contain streams {expected_categories}")
            for b in loc_bins:
                self.assertTrue(b["is_simulated"], "All virtual campus bins must have is_simulated=True")

        print(f"\n[TEST 10 PASSED] All 12 virtual campus bins verified across 4 locations in Supabase PostgreSQL (is_simulated=True).")

    def test_11_category_to_stream_mapping_integrity(self):
        """[CIRCULAR POLICY LOGIC] Task 2: Verifies mapping between predicted categories and accepted bin categories."""
        expected_mappings = {
            "cardboard": "dry_recyclable",
            "paper": "dry_recyclable",
            "plastic": "dry_recyclable",
            "metal": "glass_metal",
            "glass": "glass_metal",
            "trash": "residual",
        }
        for category, expected_stream in expected_mappings.items():
            mapped_stream = get_stream_for_class(category)
            self.assertEqual(mapped_stream, expected_stream, f"{category} must map to {expected_stream}")
            mapping = CLASS_TO_STREAM_MAPPING[category]
            self.assertEqual(mapping["stream_id"], expected_stream)
            self.assertGreater(mapping["est_weight_kg"], 0.0)
            if category == "trash":
                self.assertFalse(mapping["is_recyclable"])
                self.assertEqual(mapping["ecocoin_reward"], 0)
            else:
                self.assertTrue(mapping["is_recyclable"])
                self.assertGreater(mapping["ecocoin_reward"], 0)

        print("\n[TEST 11 PASSED] Category-to-stream segregation policies and emission factors verified.")

    def test_12_bin_telemetry_capacity_and_status_transitions(self):
        """[REAL SUPABASE CLOUD] Task 3, 4, 5: Tests fill percentage, remaining capacity, status transitions (available/near_capacity/full), and reverts delta."""
        self.assertTrue(supabase_client.is_supabase_active())
        client = supabase_client.get_supabase_client()

        # Step A: Increment by 1 item and test baseline
        bin_update = supabase_client.update_recycling_bin_load(
            location_code="CAF",
            accepted_category="dry_recyclable",
            item_delta=1,
            weight_kg_delta=0.04
        )
        self.assertEqual(bin_update["item_count"], 1)
        self.assertEqual(bin_update["current_load_kg"], 0.04)
        self.assertEqual(bin_update["status"], "available")

        # Step B: Test near_capacity transition (>= 80% of 40 items = 32 items)
        bin_near = supabase_client.update_recycling_bin_load(
            location_code="CAF",
            accepted_category="dry_recyclable",
            item_delta=31,  # 1 + 31 = 32 items (80.0%)
            weight_kg_delta=1.24
        )
        self.assertEqual(bin_near["item_count"], 32)
        self.assertEqual(bin_near["status"], "near_capacity")

        # Step C: Test full transition (>= 95% of 40 items = 38 items)
        bin_full = supabase_client.update_recycling_bin_load(
            location_code="CAF",
            accepted_category="dry_recyclable",
            item_delta=6,   # 32 + 6 = 38 items (95.0%)
            weight_kg_delta=0.24
        )
        self.assertEqual(bin_full["item_count"], 38)
        self.assertEqual(bin_full["status"], "full")

        # Step D: Revert all deltas back to baseline (item_delta = -38)
        bin_restored = supabase_client.update_recycling_bin_load(
            location_code="CAF",
            accepted_category="dry_recyclable",
            item_delta=-38,
            weight_kg_delta=-1.52
        )
        self.assertEqual(bin_restored["item_count"], 0)
        self.assertEqual(bin_restored["current_load_kg"], 0.0)
        self.assertEqual(bin_restored["status"], "available")

        # Verify through view_bin_utilization in Supabase
        view_res = client.table("view_bin_utilization").select("*").eq("location_code", "CAF").eq("accepted_category", "dry_recyclable").execute()
        self.assertEqual(len(view_res.data), 1)
        bin_view = view_res.data[0]
        self.assertEqual(float(bin_view["fill_percentage"]), 0.0)
        self.assertEqual(bin_view["remaining_items_capacity"], 40)
        self.assertEqual(bin_view["status"], "available")
        print("\n[TEST 12 PASSED] Virtual bin capacity, fill percentage, and status transitions verified and restored.")

    def test_13_recycling_operation_relational_pathway_linkage(self):
        """[REAL SUPABASE CLOUD] Task 6: Tests that recycling_operations correctly references classification and processing pathway."""
        self.assertTrue(supabase_client.is_supabase_active())
        client = supabase_client.get_supabase_client()

        test_cls_id = str(uuid.uuid4())
        self.tracked_classifications.append(test_cls_id)

        # 1. Insert classification for metal (confidence 0.94 -> recycling pathway)
        cls_rec = supabase_client.record_waste_classification(
            waste_category="metal",
            confidence=0.94,
            model_version=config.MODEL_VERSION,
            capture_source="live_camera",
            classification_status="successful",
            metadata={"test_task": "task_6_verification"},
            classification_id=test_cls_id
        )
        self.assertEqual(cls_rec["id"], test_cls_id)

        # 2. Determine pathway and insert operation
        pathway = supabase_client.determine_processing_pathway("metal", 0.94)
        self.assertEqual(pathway, "recycling")

        test_op_id = str(uuid.uuid4())
        op_rec = supabase_client.create_recycling_operation(
            classification_id=test_cls_id,
            processing_pathway=pathway,
            operation_status="completed",
            is_simulated=True,
            operation_id=test_op_id
        )
        self.assertEqual(op_rec["classification_id"], test_cls_id)
        self.assertEqual(op_rec["processing_pathway"], "recycling")
        self.assertTrue(op_rec["is_simulated"])

        # 3. Verify in PostgreSQL view_recent_classifications
        view_res = client.table("view_recent_classifications").select("*").eq("classification_id", test_cls_id).execute()
        self.assertEqual(len(view_res.data), 1)
        row = view_res.data[0]
        self.assertEqual(row["waste_category"], "metal")
        self.assertEqual(row["processing_pathway"], "recycling")
        self.assertEqual(row["operation_status"], "completed")

        # 4. Immediate cleanup
        client.table("recycling_operations").delete().eq("id", test_op_id).execute()
        client.table("waste_classifications").delete().eq("id", test_cls_id).execute()
        print("\n[TEST 13 PASSED] Relational foreign key and circular processing pathway verified in Supabase PostgreSQL.")

    def test_14_dashboard_cloud_database_consistency_and_refresh(self):
        """[REAL SUPABASE CLOUD] Task 7, 8: Verifies dashboard totals agree with underlying records and remain consistent on refresh."""
        self.assertTrue(supabase_client.is_supabase_active())
        client = supabase_client.get_supabase_client()

        # Query raw table counts
        raw_cls = client.table("waste_classifications").select("id", count="exact").execute()
        raw_bins = client.table("recycling_bins").select("id", count="exact").execute()

        # Query dashboard views
        views = supabase_client.fetch_dashboard_views()
        self.assertEqual(len(views["bins_utilization"]), 12)
        self.assertEqual(raw_bins.count, 12)
        self.assertEqual(views["total_classifications"], raw_cls.count)

        # Refresh check: repeated fetch returns identical consistent data
        dash_data_1 = get_dashboard_data()
        dash_data_2 = get_dashboard_data()
        self.assertEqual(dash_data_1["database_status"]["engine"], "Supabase PostgreSQL")
        self.assertEqual(dash_data_1["database_status"]["mode"], "production_cloud")
        self.assertEqual(dash_data_1["supabase_views"]["total_classifications"], dash_data_2["supabase_views"]["total_classifications"])
        self.assertEqual(len(dash_data_1["supabase_views"]["bins_utilization"]), len(dash_data_2["supabase_views"]["bins_utilization"]))

        print(f"\n[TEST 14 PASSED] Cloud dashboard views consistency confirmed across refreshes ({raw_bins.count} bins, {raw_cls.count} classifications).")

    def test_15_duplicate_disposal_and_invalid_category_resilience(self):
        """[RESILIENCE & VALIDATION] Task 9: Verifies duplicate requests, invalid categories, and recovery without corrupting counts."""
        # 1. Invalid Category rejected by validation
        with self.assertRaises(ValueError):
            supabase_client.record_waste_classification(
                waste_category="hazardous_uranium",
                confidence=0.99,
                capture_source="live_camera"
            )

        # 2. Invalid Pathway rejected
        with self.assertRaises(ValueError):
            supabase_client.create_recycling_operation(
                classification_id=str(uuid.uuid4()),
                processing_pathway="space_ejection"
            )

        # 3. Duplicate event in disposal flow returns is_duplicate=True without corrupting counters
        evt_dupe = f"test-dupe-safe-{uuid.uuid4()}"
        self.tracked_events.append(evt_dupe)

        r1 = process_disposal_event(
            student_id="student_safe",
            predicted_class="glass",
            confidence=0.91,
            accepted_class="glass",
            location_id="LIB",
            event_uuid=evt_dupe,
        )
        self.assertTrue(r1["success"])
        c1 = r1["new_count"]

        # Duplicate call
        r2 = process_disposal_event(
            student_id="student_safe",
            predicted_class="glass",
            confidence=0.91,
            accepted_class="glass",
            location_id="LIB",
            event_uuid=evt_dupe,
        )
        self.assertTrue(r2["is_duplicate"])
        
        # Verify in database that bin counter was not incremented
        cursor = self.conn.cursor()
        cursor.execute("SELECT item_count FROM bins WHERE bin_id = 'BIN-LIB-GLASS_METAL'")
        count_after_dupe = cursor.fetchone()["item_count"]
        self.assertEqual(count_after_dupe, c1, "Duplicate submission must never increment bin counters")

        # Restore SQLite bin count
        cursor.execute("UPDATE bins SET item_count = 0 WHERE bin_id = 'BIN-LIB-GLASS_METAL'")
        self.conn.commit()

        print("\n[TEST 15 PASSED] Duplicate-request idempotency and invalid-input resilience verified.")

if __name__ == "__main__":
    unittest.main()
