import io
import sys
import base64
import unittest
import uuid
from pathlib import Path
from PIL import Image

SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import config
from app import app
from database import init_db, get_db_connection
import supabase_client
from digital_twin_service import get_dashboard_data, process_disposal_event

class TestLiveCameraWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        cls.client = app.test_client()
        cls.app = app
        cls.tracked_classification_ids = []
        cls.tracked_event_uuids = []

        # Create a test synthetic image for live frame simulation (224x224 RGB)
        img = Image.new("RGB", (224, 224), color=(73, 109, 137))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        cls.test_jpeg_bytes = buf.getvalue()
        cls.test_b64_data = "data:image/jpeg;base64," + base64.b64encode(cls.test_jpeg_bytes).decode("utf-8")

    @classmethod
    def tearDownClass(cls):
        """Safe cleanup: cleans up all test classifications and events created during this run."""
        # 1. Clean up from Cloud Supabase
        client = supabase_client.get_supabase_client()
        if client and supabase_client.is_supabase_active():
            for test_id in cls.tracked_classification_ids:
                try:
                    client.table("recycling_operations").delete().eq("classification_id", test_id).execute()
                    client.table("waste_classifications").delete().eq("id", test_id).execute()
                except Exception:
                    pass

        # 2. Clean up from SQLite local database
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            for test_id in cls.tracked_classification_ids:
                cur.execute("DELETE FROM recycling_operations WHERE classification_id = ?", (test_id,))
                cur.execute("DELETE FROM waste_classifications WHERE id = ?", (test_id,))
            for evt_uuid in cls.tracked_event_uuids:
                cur.execute("DELETE FROM disposal_events WHERE event_uuid = ?", (evt_uuid,))
                cur.execute("DELETE FROM rewards_ledger WHERE event_uuid = ?", (evt_uuid,))
            conn.commit()
            conn.close()
        except Exception:
            pass

    def test_01_live_camera_multipart_classification(self):
        """Case 2: Live photo captured and classified via multipart live_frame to MobileNetV2."""
        data = {
            "live_frame": (io.BytesIO(self.test_jpeg_bytes), "live_camera_capture.jpg", "image/jpeg"),
            "capture_source": "live_camera",
        }
        resp = self.client.post(
            "/api/classify",
            data=data,
            content_type="multipart/form-data",
            headers={"X-Capture-Source": "live_camera"}
        )
        self.assertEqual(resp.status_code, 200)
        res = resp.get_json()
        self.assertTrue(res["success"])
        self.assertIn("predicted_class", res["data"])
        self.assertIn("confidence", res["data"])
        self.assertIn("disposal_guidance", res["data"])
        self.assertIn("recycling_pathway", res["data"])
        self.assertEqual(res["data"]["input_mode"], "live_camera_capture")
        self.assertTrue(res["data"]["verified_live"])

        # Track and verify Supabase persistence
        cls_id = res["data"]["classification_id"]
        self.assertIsNotNone(cls_id)
        self.tracked_classification_ids.append(cls_id)

        client = supabase_client.get_supabase_client()
        if client and supabase_client.is_supabase_active():
            fetch_res = client.table("waste_classifications").select("capture_source,waste_category").eq("id", cls_id).execute()
            self.assertEqual(len(fetch_res.data), 1)
            self.assertEqual(fetch_res.data[0]["capture_source"], "live_camera")

        print(f"\n[PASS] Live camera multipart classification: {res['data']['predicted_class']} ({res['data']['confidence_pct']}%) persisted as {cls_id}.")

    def test_02_live_camera_base64_canvas_classification(self):
        """Case 2b: Live photo captured from HTML5 Canvas transmitted as JSON base64."""
        payload = {
            "image_data": self.test_b64_data,
            "capture_source": "live_camera",
            "capture_timestamp": "2026-10-16T10:00:00Z"
        }
        resp = self.client.post(
            "/api/classify",
            json=payload,
            headers={"X-Capture-Source": "live_camera"}
        )
        self.assertEqual(resp.status_code, 200)
        res = resp.get_json()
        self.assertTrue(res["success"])
        self.assertIn(res["data"]["predicted_class"], config.CLASSES)

        cls_id = res["data"]["classification_id"]
        self.assertIsNotNone(cls_id)
        self.tracked_classification_ids.append(cls_id)

        print(f"\n[PASS] Live canvas base64 classification: {res['data']['predicted_class']} persisted as {cls_id}.")

    def test_03_reject_direct_upload_without_camera_attestation(self):
        """Case 6a: Reject arbitrary file upload when capture_source is missing."""
        data = {
            "image": (io.BytesIO(self.test_jpeg_bytes), "uploaded_file.jpg", "image/jpeg"),
        }
        resp = self.client.post(
            "/api/classify",
            data=data,
            content_type="multipart/form-data"
        )
        self.assertEqual(resp.status_code, 400)
        res = resp.get_json()
        self.assertFalse(res["success"])
        self.assertIn("Direct file uploads", res["error"])
        print("\n[PASS] Direct file upload without live camera attestation rejected with HTTP 400.")

    def test_04_reject_prerecorded_video_files(self):
        """Case 6b: Reject attempts to classify video files (mp4, webm, avi, etc.)."""
        fake_video_bytes = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 300
        data = {
            "live_frame": (io.BytesIO(fake_video_bytes), "sample_recording.mp4", "video/mp4"),
            "capture_source": "live_camera",
        }
        resp = self.client.post(
            "/api/classify",
            data=data,
            content_type="multipart/form-data",
            headers={"X-Capture-Source": "live_camera"}
        )
        self.assertEqual(resp.status_code, 415)
        res = resp.get_json()
        self.assertFalse(res["success"])
        self.assertIn("video", res["error"].lower())
        print("\n[PASS] Prerecorded video rejected with HTTP 415.")

    def test_05_reject_video_data_uri(self):
        """Case 6c: Reject JSON payloads with data:video/* URI."""
        payload = {
            "image_data": "data:video/mp4;base64,AAAAHGZ0eXBtcDQyAAAA",
            "capture_source": "live_camera"
        }
        resp = self.client.post(
            "/api/classify",
            json=payload,
            headers={"X-Capture-Source": "live_camera"}
        )
        self.assertEqual(resp.status_code, 415)
        res = resp.get_json()
        self.assertFalse(res["success"])
        print("\n[PASS] Base64 video data URI rejected with HTTP 415.")

    def test_06_dataset_sample_routes_strictly_disabled(self):
        """Case 6d: Ensure sample image and dataset browsing routes are disabled (HTTP 403)."""
        resp1 = self.client.post("/api/classify_sample", json={"image_path": "any"})
        self.assertEqual(resp1.status_code, 403)

        resp2 = self.client.get("/api/sample_image?class=plastic")
        self.assertEqual(resp2.status_code, 403)

        resp3 = self.client.get("/dataset_image/plastic/test.jpg")
        self.assertEqual(resp3.status_code, 403)
        print("\n[PASS] Sample endpoints securely disabled (HTTP 403).")

    def test_07_disposal_updates_digital_twin_and_supabase_bins(self):
        """Case 7: Verifies successful classification disposal updates virtual waste bin and reverts cleanly."""
        evt_uuid = f"test-dt-{uuid.uuid4()}"
        self.tracked_event_uuids.append(evt_uuid)

        # Process disposal
        disp_resp = self.client.post(
            "/api/dispose",
            json={
                "event_uuid": evt_uuid,
                "student_id": "test_student_cam",
                "predicted_class": "plastic",
                "confidence": 0.95,
                "accepted_class": "plastic",
                "location_id": "LIB",
                "was_overridden": False,
                "image_name": "live_camera_capture.jpg"
            }
        )
        self.assertEqual(disp_resp.status_code, 200)
        disp_res = disp_resp.get_json()
        self.assertTrue(disp_res["success"])
        self.assertEqual(disp_res["data"]["bin_id"], "BIN-LIB-DRY_RECYCLABLE")
        self.assertGreater(disp_res["data"]["ecocoins_awarded"], 0)
        self.assertIn("recycling_pathway", disp_res["data"])

        # Verify Supabase bin telemetry was synchronized
        if "bin_telemetry" in disp_res["data"]:
            self.assertIn("bin_id", disp_res["data"]["bin_telemetry"])

        # Immediately revert the bin test increment in Supabase to preserve real data
        supabase_client.update_recycling_bin_load(
            location_code="LIB",
            accepted_category="dry_recyclable",
            item_delta=-1,
            weight_kg_delta=-0.04
        )
        print("\n[PASS] Digital twin metrics and Supabase bin synchronization verified and baseline restored.")

    def test_08_corrupted_frame_validation(self):
        """Validation: Corrupted or non-image bytes are rejected."""
        data = {
            "live_frame": (io.BytesIO(b"not an image at all"), "bad.jpg", "image/jpeg"),
            "capture_source": "live_camera",
        }
        resp = self.client.post(
            "/api/classify",
            data=data,
            content_type="multipart/form-data",
            headers={"X-Capture-Source": "live_camera"}
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.get_json()["success"])
        print("\n[PASS] Corrupted image bytes correctly rejected with HTTP 400.")

    def test_09_dashboard_refresh_retains_persistent_data(self):
        """Case 9: Verify refreshing dashboard renders persisted Supabase views and digital twin state."""
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("EcoTwin", html)
        self.assertIn("Digital Twin", html)

        # Check API endpoint for dashboard JSON state
        api_resp = self.client.get("/api/dashboard?student_id=student_101")
        self.assertEqual(api_resp.status_code, 200)
        dash_data = api_resp.get_json()["data"]
        self.assertIn("bins", dash_data)
        self.assertIn("database_status", dash_data)
        self.assertEqual(dash_data["database_status"]["engine"], "Supabase PostgreSQL")
        print("\n[PASS] Dashboard refresh verified: Supabase PostgreSQL persistent telemetry retained.")

    def test_10_invalid_dimensions_and_corrupt_payload_handling(self):
        """Validation: Extreme image dimensions (too small) are caught and rejected."""
        tiny_img = Image.new("RGB", (16, 16), color=(255, 0, 0))
        buf = io.BytesIO()
        tiny_img.save(buf, format="JPEG")
        data = {
            "live_frame": (io.BytesIO(buf.getvalue()), "tiny.jpg", "image/jpeg"),
            "capture_source": "live_camera",
        }
        resp = self.client.post(
            "/api/classify",
            data=data,
            content_type="multipart/form-data",
            headers={"X-Capture-Source": "live_camera"}
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("too low", resp.get_json()["error"])
        print("\n[PASS] Under-dimensioned frame (<32x32) rejected gracefully.")

    def test_11_resilience_under_supabase_outage_simulation(self):
        """Resilience: If Supabase connection is temporarily unavailable, local SQLite replica handles transaction."""
        original_status = supabase_client._IS_CONNECTED
        try:
            # Simulate temporary network disconnect
            supabase_client._IS_CONNECTED = False
            rec = supabase_client.record_waste_classification(
                waste_category="paper",
                confidence=0.88,
                model_version=config.MODEL_VERSION,
                capture_source="live_camera",
                classification_status="successful",
                metadata={"test_run": "outage_simulation"}
            )
            self.assertIsNotNone(rec["id"])
            self.tracked_classification_ids.append(rec["id"])
            self.assertEqual(rec["waste_category"], "paper")

            # Check status reports local fallback
            status = supabase_client.get_database_status()
            self.assertEqual(status["engine"], "SQLite Local Replica")
            self.assertEqual(status["mode"], "local_fallback")
        finally:
            supabase_client._IS_CONNECTED = original_status

        print("\n[PASS] Database resilience verified: Local SQLite replica seamlessly takes over during simulated outage.")

if __name__ == "__main__":
    unittest.main()
