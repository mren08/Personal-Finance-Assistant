import io
import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

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
        self.assertIn(b"help you achieve your budgeting goals", response.data)

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
        self.assertIn(b"Personal Finance AI Assistant", response.data)
        self.assertIn(b"AI Chatbot", response.data)

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

    def test_chat_endpoint_asks_before_adding_possible_duplicate_transaction(self):
        self.client.post("/signup", data={"email": "demo@example.com", "password": "secret123"})
        self.client.post(
            "/api/upload-statement",
            data={"statement": (io.BytesIO("""Transaction Date,Description,Category,Amount
04/18/2026,HOWOO KOREAN STEAKHOUSE,Dining,-50.00
""".encode("utf-8")), "statement.csv")},
            content_type="multipart/form-data",
        )

        response = self.client.post(
            "/api/chat",
            json={"message": "I spent $50 at Howoo."},
        )

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["action"]["type"], "confirm_transaction_match")
        self.assertEqual(payload["profile"]["transaction_count"], 1)

    def test_profile_update_route_saves_income_and_fixed_expenses(self):
        self.client.post("/signup", data={"email": "demo@example.com", "password": "secret123"})

        response = self.client.post(
            "/api/profile",
            json={
                "monthly_income": 4200,
                "fixed_expenses": 1800,
                "budgeting_goal": "Spend less on dining",
            },
        )

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["profile"]["financial_profile"]["monthly_income"], 4200)
        self.assertEqual(payload["profile"]["financial_profile"]["fixed_expenses"], 1800)
        self.assertEqual(payload["profile"]["monthly_summary"]["available_before_fixed"], 4200)
        self.assertEqual(payload["profile"]["monthly_summary"]["leftover_money"], 2400)
        self.assertEqual(
            payload["profile"]["agent_notes"][0]["note_type"],
            f"{datetime.now(UTC).strftime('%B %Y')} focus",
        )

    def test_chat_route_persists_agent_note_from_llm_result(self):
        self.client.post("/signup", data={"email": "demo@example.com", "password": "secret123"})
        self.client.post(
            "/api/profile",
            json={
                "monthly_income": 4200,
                "fixed_expenses": 1800,
                "budgeting_goal": "Spend less on dining",
            },
        )

        with patch("app.build_agent_service") as build_agent_service:
            build_agent_service.return_value.run_chat_turn.return_value = {
                "reply": "You have $2400 left this month.",
                "actions": [
                    {
                        "type": "save_agent_note",
                        "note_type": "monthly_focus",
                        "content": "Dining should stay under control if the user wants buffer.",
                    }
                ],
            }

            response = self.client.post(
                "/api/chat",
                json={"message": "How much do I have left this month?"},
            )

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["reply"], "You have $2400 left this month.")
        self.assertIn(
            "Dining should stay under control if the user wants buffer.",
            [note["content"] for note in payload["profile"]["agent_notes"]],
        )

    def test_upload_generates_proactive_ai_chatbot_message_with_recurring_and_category_context(self):
        self.client.post("/signup", data={"email": "demo@example.com", "password": "secret123"})
        self.client.post(
            "/api/profile",
            json={
                "monthly_income": 3000,
                "fixed_expenses": 1000,
                "budgeting_goal": "Cut back on subscriptions",
            },
        )
        recurring_csv = """Transaction Date,Description,Category,Amount
02/01/2026,NETFLIX.COM,Subscriptions,-15.49
03/02/2026,NETFLIX.COM,Subscriptions,-15.49
04/03/2026,NETFLIX.COM,Subscriptions,-15.49
04/06/2026,RESTAURANT ROW,Dining,-120.00
"""

        response = self.client.post(
            "/api/upload-statement",
            data={"statement": (io.BytesIO(recurring_csv.encode("utf-8")), "statement.csv")},
            content_type="multipart/form-data",
        )

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertIn("AI Chatbot", self.client.get("/").data.decode("utf-8"))
        self.assertGreaterEqual(len(payload["profile"]["messages"]), 1)
        assistant_message = payload["profile"]["messages"][-1]["content"]
        self.assertIn("recurring charges", assistant_message.lower())
        self.assertIn("Dining", assistant_message)
        self.assertIn("Netflix", assistant_message)

    def test_logged_in_dashboard_shows_income_and_leftover_money_sections(self):
        self.client.post(
            "/signup",
            data={"email": "demo@example.com", "password": "secret123"},
            follow_redirects=True,
        )

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Monthly income", response.data)
        self.assertIn(b"Fixed expenses", response.data)
        self.assertIn(b"Left this month", response.data)
        self.assertIn(b"Agent notes", response.data)
        self.assertIn(b"Category breakdown", response.data)
        self.assertIn(b"category-donut-chart", response.data)

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

    def test_readme_mentions_openai_key_and_profile_inputs(self):
        readme = Path("README.md").read_text(encoding="utf-8")

        self.assertIn("OPENAI_API_KEY", readme)
        self.assertIn("monthly income", readme.lower())
        self.assertIn("fixed expenses", readme.lower())
        self.assertIn("agent notes", readme.lower())


if __name__ == "__main__":
    unittest.main()
