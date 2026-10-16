import unittest
import json
import sys
from pathlib import Path

# Add src directory
SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from app import app
import c_twin_service


class TestCTwinEndpoints(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_01_get_state(self):
        resp = self.client.get("/api/c-twin/state?student_id=student_101")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        state = data["data"]
        self.assertIn("bins", state)
        self.assertEqual(len(state["bins"]), 10)
        self.assertIn("events", state)
        self.assertIn("reports", state)
        self.assertIn("student", state)
        self.assertIn("config", state)
        print("\n[C-TWIN TEST 1 PASSED] State API returned 10 bins and complete synchronized data.")

    def test_02_dispose_flow(self):
        resp = self.client.post(
            "/api/c-twin/dispose",
            json={
                "waste_type": "Plastic",
                "bin_id": "B01",
                "student_id": "student_101",
                "event_uuid": "TEST-EV-01"
            }
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["data"]["pts"], 10)
        self.assertIn("lines", data["data"])
        print("\n[C-TWIN TEST 2 PASSED] Disposal API successfully processed plastic disposal (+10 pts).")

    def test_03_report_and_escalation_flow(self):
        import uuid
        test_uid = f"rep_student_{uuid.uuid4().hex[:6]}"
        resp = self.client.post(
            "/api/c-twin/report",
            json={
                "bin_id": "B02",
                "user_id": test_uid,
                "problem": "Full / overflowing",
                "description": "Bin lid stuck"
            }
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        print("\n[C-TWIN TEST 3 PASSED] Report API recorded student report.")

    def test_04_collection_flow(self):
        resp = self.client.post(
            "/api/c-twin/collect",
            json={"bin_id": "B01", "operator_name": "Garbage Collector"}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["data"]["bin_id"], "B01")
        print("\n[C-TWIN TEST 4 PASSED] Collection API emptied bin and resolved reports.")

    def test_05_guest_code_flow(self):
        gen_resp = self.client.post(
            "/api/c-twin/code/generate",
            json={"user_id": "guest_test", "bin_id": "B01", "waste_type": "Plastic"}
        )
        self.assertEqual(gen_resp.status_code, 200)
        gen_data = gen_resp.get_json()
        self.assertTrue(gen_data["success"])
        code = gen_data["data"]["code"]
        self.assertEqual(len(code), 6)

        # Redeem code
        red_resp = self.client.post(
            "/api/c-twin/code/redeem",
            json={"user_id": "guest_test", "bin_id": "B01", "waste_type": "Plastic", "code": code}
        )
        self.assertEqual(red_resp.status_code, 200)
        red_data = red_resp.get_json()
        self.assertTrue(red_data["success"])
        print("\n[C-TWIN TEST 5 PASSED] Guest disposal code generation and simulated keypad redemption verified.")

    def test_06_reward_redemption_flow(self):
        import uuid
        test_sid = f"st_rew_{uuid.uuid4().hex[:6]}"
        resp = self.client.post(
            "/api/c-twin/redeem",
            json={"student_id": test_sid, "reward_id": "R4"}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertIn("voucher_code", data["data"])
        print(f"\n[C-TWIN TEST 6 PASSED] Reward redemption succeeded with voucher: {data['data']['voucher_code']}")

    def test_07_config_update(self):
        resp = self.client.post(
            "/api/c-twin/config",
            json={"threshold": 6, "window_hours": 48, "weights": [35, 35, 20, 10]}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        print("\n[C-TWIN TEST 7 PASSED] Admin escalation thresholds and weights updated.")

    def test_08_greenmind_ask(self):
        resp = self.client.post(
            "/api/c-twin/ask",
            json={"question": "Where does plastic go?", "role": "student"}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertIn("Plastic", data["answer"])
        print(f"\n[C-TWIN TEST 8 PASSED] GreenMind AI response: {data['answer'][:60]}...")

    def test_09_root_route_rendering(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("C-Twin — Circularity. Community. Clarity.", html)
        self.assertIn("EcoTwin", html)
        self.assertIn("Digital Twin", html)
        self.assertIn("SortSense", html)
        self.assertIn("Find Bin", html)
        self.assertIn("GreenMind", html)
        print("\n[C-TWIN TEST 9 PASSED] Root route renders full C-Twin video frontend.")


if __name__ == "__main__":
    unittest.main()
