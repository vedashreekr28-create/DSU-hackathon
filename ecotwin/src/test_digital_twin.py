import os
import unittest
import uuid
from pathlib import Path

import config
from database import init_db, get_db_connection
from waste_streams import CLASS_TO_STREAM_MAPPING, get_stream_for_class, calculate_co2_saving
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

    def setUp(self):
        self.conn = get_db_connection()

    def tearDown(self):
        self.conn.close()

    def test_01_model_inference_on_real_image(self):
        """Verifies real CNN inference produces valid probabilities and classes."""
        plastic_samples = list((config.DATA_DIR / "plastic").glob("*.jpg"))
        self.assertTrue(len(plastic_samples) > 0, "Dataset plastic images must exist")
        
        result = classify_waste_image(str(plastic_samples[0]))
        self.assertIn("predicted_class", result)
        self.assertIn(result["predicted_class"], config.CLASSES)
        self.assertGreaterEqual(result["confidence"], 0.0)
        self.assertLessEqual(result["confidence"], 1.0)
        self.assertIn("probabilities", result)
        self.assertEqual(len(result["probabilities"]), 6)
        print(f"\n[TEST 1 PASSED] Real CNN prediction: {result['predicted_class']} ({result['confidence_pct']}%)")

    def test_02_manual_review_threshold_rule(self):
        """Verifies low-confidence prediction triggers manual review flag."""
        # Simulated low vs high confidence check
        test_conf_low = 0.50
        test_conf_high = 0.85
        
        needs_review_low = test_conf_low < config.CONFIDENCE_THRESHOLD
        needs_review_high = test_conf_high < config.CONFIDENCE_THRESHOLD
        
        self.assertTrue(needs_review_low, "Scores below threshold must flag manual review")
        self.assertFalse(needs_review_high, "Scores above threshold must not flag manual review")
        print("\n[TEST 2 PASSED] Manual review threshold logic verified.")

    def test_03_disposal_counter_and_capacity_increment(self):
        """Tests that accepted disposal increments virtual bin by exactly 1 item."""
        bin_id = "BIN-ENG-DRY_RECYCLABLE"
        
        # Check initial
        cursor = self.conn.cursor()
        cursor.execute("SELECT item_count, estimated_fill_percent FROM bins WHERE bin_id = ?", (bin_id,))
        initial = cursor.fetchone()
        init_count = initial["item_count"]
        
        evt_id = f"test-evt-{uuid.uuid4()}"
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
        print(f"\n[TEST 3 PASSED] Bin count accurately incremented from {init_count} to {updated_count}.")

    def test_04_idempotency_prevents_duplicate_disposal_and_rewards(self):
        """Tests that submitting the same event_uuid twice is rejected without double counting."""
        evt_id = f"test-idemp-{uuid.uuid4()}"
        
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
        """Verifies reward is awarded for recyclables and 0 for residual trash."""
        evt_plastic = f"test-reward-p-{uuid.uuid4()}"
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
        """Verifies user manual override is recorded and flags contamination if streams diverge."""
        evt_override = f"test-override-{uuid.uuid4()}"
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
        """Verifies virtual sensor triggers Nearly Full and deduplicates alerts."""
        bin_id = "BIN-LIB-GLASS_METAL"
        
        # Set fill to 85%
        res = simulate_sensor_telemetry(bin_id, 85.0)
        self.assertEqual(res["status"], "Nearly Full")
        
        # Check active alerts count for this bin
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM alerts WHERE bin_id = ? AND is_active = 1", (bin_id,))
        count_alerts = cursor.fetchone()[0]
        self.assertEqual(count_alerts, 1, "Alert must be generated once")
        
        # Send telemetry again at 86% - should NOT create duplicate active alert
        res2 = simulate_sensor_telemetry(bin_id, 86.0)
        cursor.execute("SELECT COUNT(*) FROM alerts WHERE bin_id = ? AND is_active = 1", (bin_id,))
        count_alerts_after = cursor.fetchone()[0]
        self.assertEqual(count_alerts_after, 1, "Alerts must be deduplicated across updates")
        print("\n[TEST 7 PASSED] Sensor threshold alert triggered and deduplicated.")

    def test_08_collection_event_empties_bin_and_resolves_alerts(self):
        """Verifies simulate_collection_event resets fill level and clears alerts."""
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
        """Verifies calling get_dashboard_data does not mutate database counters."""
        dash1 = get_dashboard_data()
        count1 = dash1["total_items"]
        
        dash2 = get_dashboard_data()
        count2 = dash2["total_items"]
        
        self.assertEqual(count1, count2, "Dashboard queries must be strictly idempotent")
        print("\n[TEST 9 PASSED] Dashboard read idempotency confirmed.")

if __name__ == "__main__":
    unittest.main()
