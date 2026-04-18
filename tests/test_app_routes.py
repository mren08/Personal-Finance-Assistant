import io
import os
import tempfile
import unittest
from pathlib import Path

import app as app_module


SAMPLE_CSV = """Transaction Date,Description,Category,Amount
01/03/2026,Coffee Shop,Food & Drink,-12.50
01/04/2026,Grocer,Groceries,-48.10
01/05/2026,Payment,Credit Card Payment,125.00
"""


class AppRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["APP_DB_PATH"] = f"{self.temp_dir.name}/test.db"
        os.environ["SECRET_KEY"] = "test-secret"
        self.app = app_module.create_app()
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()
        os.environ.pop("APP_DB_PATH", None)
        os.environ.pop("SECRET_KEY", None)

    def test_index_page_loads(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Cut the waste", response.data)

    def test_healthcheck_returns_ok(self):
        response = self.client.get("/healthz")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})

    def test_analyze_rejects_missing_statement(self):
        response = self.client.post("/api/analyze", data={})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json(),
            {"error": "Missing CSV file input named 'statement'."},
        )

    def test_analyze_accepts_valid_csv_upload(self):
        response = self.client.post(
            "/api/analyze",
            data={
                "statement": (io.BytesIO(SAMPLE_CSV.encode("utf-8")), "statement.csv"),
                "monthly_budget": "3000",
                "fixed_costs": "900",
                "goal_name": "Emergency Fund",
                "goal_amount": "600",
                "goal_timeline_months": "6",
                "manual_expenses_json": "[]",
                "budget_caps_json": "{}",
            },
            content_type="multipart/form-data",
        )

        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["transaction_count"], 2)
        self.assertEqual(payload["total_spent"], 60.6)
        self.assertEqual(payload["category_totals"]["Groceries"], 48.1)
        self.assertIn("recommendations", payload)
        self.assertIn("actionable_tips_details", payload)

    def test_signup_logs_user_in_and_shows_dashboard(self):
        response = self.client.post(
            "/signup",
            data={"email": "demo@example.com", "password": "secret123"},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Overspending Coach", response.data)
        self.assertIn(b"Accountability Chat", response.data)

    def test_upload_persists_transactions_for_logged_in_user(self):
        self.client.post("/signup", data={"email": "demo@example.com", "password": "secret123"})

        response = self.client.post(
            "/api/upload-statement",
            data={"statement": (io.BytesIO(SAMPLE_CSV.encode("utf-8")), "statement.csv")},
            content_type="multipart/form-data",
        )

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["saved_transactions"], 2)
        self.assertIn("profile", payload)
        self.assertEqual(payload["profile"]["transaction_count"], 2)

    def test_chat_endpoint_saves_manual_transaction_action(self):
        self.client.post("/signup", data={"email": "demo@example.com", "password": "secret123"})

        response = self.client.post(
            "/api/chat",
            json={"message": "I ate at Howoo for $50"},
        )

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["action"]["type"], "add_transaction")
        self.assertIn("messages", payload)
        self.assertGreaterEqual(payload["profile"]["transaction_count"], 1)

    def test_analyze_rejects_blank_filename(self):
        response = self.client.post(
            "/api/analyze",
            data={"statement": (io.BytesIO(b""), "")},
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json(),
            {"error": "Please choose a CSV file before submitting."},
        )

    def test_analyze_rejects_whitespace_only_filename(self):
        response = self.client.post(
            "/api/analyze",
            data={"statement": (io.BytesIO(b""), "   ")},
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json(),
            {"error": "Please choose a CSV file before submitting."},
        )

    def test_readme_mentions_web_service_flow_and_public_demo_risk(self):
        readme = Path("README.md").read_text(encoding="utf-8")

        self.assertIn("New > Web Service", readme)
        self.assertIn("New > Blueprint", readme)
        self.assertIn("not production-safe for sensitive financial data", readme)


if __name__ == "__main__":
    unittest.main()
