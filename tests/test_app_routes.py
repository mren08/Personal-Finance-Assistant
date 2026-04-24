import io
import sqlite3
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

MULTI_MONTH_CSV = """Transaction Date,Description,Category,Amount
03/03/2026,Coffee Shop,Food & Drink,-20.00
03/08/2026,Grocer,Groceries,-80.00
04/03/2026,NETFLIX.COM,Subscriptions,-15.49
04/06/2026,Restaurant Row,Dining,-120.00
"""

GAS_AND_MEMBERSHIP_CSV = """Transaction Date,Description,Category,Amount
01/03/2026,BP#34122123010 OCEAN BP,Travel,-92.10
02/03/2026,BP#34122123010 OCEAN BP,Travel,-107.20
01/07/2026,CLR*ClubPilate7187010242,Wellness,-99.65
02/07/2026,CLR*ClubPilate7187010242,Wellness,-99.65
"""

OCT_NOV_FOOD_CSV = """Transaction Date,Description,Category,Amount
10/03/2025,SWEETGREEN,Food & Drink,-18.25
10/11/2025,SWEETGREEN,Food & Drink,-24.75
10/15/2025,JOE'S PIZZA,Food & Drink,-31.50
10/22/2025,JOE'S PIZZA,Food & Drink,-29.00
11/04/2025,STARBUCKS,Food & Drink,-9.47
11/12/2025,CHIPOTLE,Food & Drink,-14.80
"""

INSIGHTS_CSV = """Transaction Date,Description,Category,Amount
01/03/2026,Restaurant Row,Dining,-100.00
02/03/2026,Restaurant Row,Dining,-100.00
03/03/2026,Restaurant Row,Dining,-100.00
02/05/2026,NETFLIX.COM,Subscriptions,-15.49
03/05/2026,NETFLIX.COM,Subscriptions,-15.49
04/03/2026,Restaurant Row,Dining,-180.00
04/05/2026,NETFLIX.COM,Subscriptions,-15.49
"""

BEHAVIOR_CSV = """Transaction Date,Description,Category,Amount
04/04/2026,Brunch Club,Dining,-120.00
04/05/2026,Cocktail Bar,Dining,-90.00
04/06/2026,Office Lunch,Dining,-35.00
04/10/2026,Delta Travel,Travel,-220.00
04/11/2026,Airport Dinner,Dining,-70.00
04/16/2026,Sushi Spot,Dining,-85.00
04/17/2026,Steak Night,Dining,-75.00
"""


class AppRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["APP_DB_PATH"] = f"{self.temp_dir.name}/test.db"
        os.environ["SECRET_KEY"] = "test-secret"
        self.app = app_module.create_app()
        self.client = self.app.test_client()

    def _signup_and_login(self, follow_redirects: bool = False):
        self.client.post("/signup", data={"email": "demo@example.com", "password": "secret123"})
        return self.client.post(
            "/login",
            data={"email": "demo@example.com", "password": "secret123"},
            follow_redirects=follow_redirects,
        )

    def _upload_receipt_for_review(self, filename: str = "receipt.jpg") -> dict:
        response = self.client.post(
            "/api/upload-receipts",
            data={"receipts": [(io.BytesIO(b"fake image"), filename)]},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(len(payload["receipts"]), 1)
        return payload["receipts"][0]

    def tearDown(self):
        self.temp_dir.cleanup()
        os.environ.pop("APP_DB_PATH", None)
        os.environ.pop("SECRET_KEY", None)

    def test_index_page_loads(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Build Better Budgeting Habits with an AI Assistant", response.data)

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

    def test_signup_creates_account_but_keeps_user_logged_out(self):
        response = self.client.post(
            "/signup",
            data={"email": "demo@example.com", "password": "secret123"},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Account created. Sign in with the email and password you just set.", response.data)
        self.assertIn(b"Sign in", response.data)
        self.assertIn(b"No account?", response.data)
        self.assertIn(b"Create one now", response.data)
        self.assertIn(b'id="signup-panel" hidden', response.data)
        self.assertNotIn(b"AI Chatbot", response.data)

    def test_login_shows_error_for_bad_credentials(self):
        self.client.post("/signup", data={"email": "demo@example.com", "password": "secret123"})

        response = self.client.post(
            "/login",
            data={"email": "demo@example.com", "password": "wrongpass"},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 401)
        self.assertIn(b"Invalid email or password.", response.data)

    def test_forgot_password_updates_password_and_shows_notice(self):
        self.client.post("/signup", data={"email": "demo@example.com", "password": "secret123"})

        reset_response = self.client.post(
            "/forgot-password",
            data={"email": "demo@example.com", "new_password": "newsecret456"},
            follow_redirects=True,
        )
        login_response = self.client.post(
            "/login",
            data={"email": "demo@example.com", "password": "newsecret456"},
            follow_redirects=True,
        )

        self.assertEqual(reset_response.status_code, 200)
        self.assertIn(b"Password updated. Sign in with your new password.", reset_response.data)
        self.assertEqual(login_response.status_code, 200)
        self.assertIn(b"AI Chatbot", login_response.data)

    def test_upload_persists_transactions_for_logged_in_user(self):
        self._signup_and_login()

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

    def test_logged_in_dashboard_shows_receipt_upload_section(self):
        self._signup_and_login()

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Upload receipts", response.data)
        self.assertIn(b"receipt-review-list", response.data)

    def test_approved_receipt_disappears_from_pending_review_queue(self):
        self._signup_and_login()

        with patch("app.extract_receipt_batch") as extract_receipt_batch:
            extract_receipt_batch.return_value = [
                {
                    "receipt_upload_id": self.app.config["storage"].create_receipt_upload(
                        1,
                        "ready-receipt.jpg",
                        "uploads/ready-receipt.jpg",
                    ),
                    "merchant": "Trader Joe's",
                    "transaction_date": "2026-04-23",
                    "total_amount": 48.22,
                    "category": "Groceries",
                    "category_confidence": 0.94,
                    "status": "ready",
                    "behavior_note": "",
                    "item_tags": ["groceries"],
                }
            ]
            receipt = self._upload_receipt_for_review("ready-receipt.jpg")

        pending_response = self.client.get("/")
        self.assertEqual(pending_response.status_code, 200)
        self.assertIn(b"Trader Joe&#39;s", pending_response.data)

        approve_response = self.client.post(
            f"/api/receipts/{receipt['id']}/approve",
            json={
                "merchant": "Trader Joe's",
                "transaction_date": "2026-04-23",
                "total_amount": 48.22,
                "category": "Groceries",
                "month": "2026-04",
            },
        )
        self.assertEqual(approve_response.status_code, 200)
        approve_payload = approve_response.get_json()
        self.assertEqual(approve_payload["profile"]["pending_receipts"], [])

        refreshed_response = self.client.get("/")
        self.assertEqual(refreshed_response.status_code, 200)
        html = refreshed_response.data.decode("utf-8")
        review_list_start = html.index('<ul id="receipt-review-list"')
        review_list_end = html.index("</ul>", review_list_start)
        review_list_markup = html[review_list_start:review_list_end]
        self.assertNotIn("Trader Joe&#39;s", review_list_markup)

    def test_receipt_upload_response_includes_profile_pending_receipts_for_persisted_cards(self):
        self._signup_and_login()

        with patch("app.extract_receipt_batch") as extract_receipt_batch:
            extract_receipt_batch.return_value = [
                {
                    "receipt_upload_id": self.app.config["storage"].create_receipt_upload(
                        1,
                        "ready-receipt.jpg",
                        "uploads/ready-receipt.jpg",
                    ),
                    "merchant": "Trader Joe's",
                    "transaction_date": "2026-04-23",
                    "total_amount": 48.22,
                    "category": "Groceries",
                    "category_confidence": 0.94,
                    "status": "ready",
                    "behavior_note": "",
                    "item_tags": ["groceries"],
                }
            ]
            response = self.client.post(
                "/api/upload-receipts",
                data={"receipts": [(io.BytesIO(b"fake image"), "ready-receipt.jpg")]},
                content_type="multipart/form-data",
            )

        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(payload["receipts"]), 1)
        self.assertIn("profile", payload)
        self.assertEqual(len(payload["profile"]["pending_receipts"]), 1)
        self.assertEqual(payload["profile"]["pending_receipts"][0]["merchant"], "Trader Joe's")
        self.assertEqual(payload["profile"]["pending_receipts"][0]["status"], "ready")

    def test_receipt_upload_response_separates_error_cards_from_persisted_pending_receipts(self):
        self._signup_and_login()

        with patch("app.extract_receipt_batch") as extract_receipt_batch:
            extract_receipt_batch.return_value = [
                {
                    "merchant": "",
                    "transaction_date": "",
                    "total_amount": 0.0,
                    "category": "",
                    "category_confidence": 0.0,
                    "status": "error",
                    "behavior_note": "Could not read receipt.",
                    "item_tags": [],
                },
                {
                    "receipt_upload_id": self.app.config["storage"].create_receipt_upload(
                        1,
                        "ready-receipt.jpg",
                        "uploads/ready-receipt.jpg",
                    ),
                    "merchant": "Sweetgreen",
                    "transaction_date": "2026-04-23",
                    "total_amount": 18.50,
                    "category": "Dining",
                    "category_confidence": 0.94,
                    "status": "ready",
                    "behavior_note": "",
                    "item_tags": ["salad"],
                },
            ]
            response = self.client.post(
                "/api/upload-receipts",
                data={
                    "receipts": [
                        (io.BytesIO(b"fake image 1"), "error-receipt.jpg"),
                        (io.BytesIO(b"fake image 2"), "ready-receipt.jpg"),
                    ]
                },
                content_type="multipart/form-data",
            )

        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(payload["receipts"]), 2)
        self.assertEqual(payload["receipts"][0]["status"], "error")
        self.assertNotIn("id", payload["receipts"][0])
        self.assertEqual(len(payload["profile"]["pending_receipts"]), 1)
        self.assertEqual(payload["profile"]["pending_receipts"][0]["merchant"], "Sweetgreen")
        self.assertEqual(payload["profile"]["pending_receipts"][0]["status"], "ready")

    def test_chat_endpoint_saves_manual_transaction_action(self):
        self._signup_and_login()

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
        self._signup_and_login()
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
        self._signup_and_login()

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

    def test_monthly_plan_saves_month_scoped_entry_and_can_be_edited(self):
        self._signup_and_login()
        self.client.post(
            "/api/upload-statement",
            data={"statement": (io.BytesIO(MULTI_MONTH_CSV.encode("utf-8")), "statement.csv")},
            content_type="multipart/form-data",
        )

        april_response = self.client.post(
            "/api/profile",
            json={
                "month": "2026-04",
                "monthly_income": 3000,
                "fixed_expenses": 500,
                "budgeting_goal": "Save 500 for a trip",
            },
        )
        april_payload = april_response.get_json()

        self.assertEqual(april_response.status_code, 200)
        self.assertEqual(april_payload["profile"]["financial_profile"]["monthly_income"], 3000)
        self.assertEqual(april_payload["profile"]["financial_profile"]["fixed_expenses"], 500)
        self.assertEqual(april_payload["profile"]["financial_profile"]["budgeting_goal"], "Save 500 for a trip")
        self.assertIn(
            "April 2026, monthly income of $3000.00, fixed expenses of $500.00, goal is to Save 500 for a trip",
            [item["summary"] for item in april_payload["profile"]["monthly_plan_history"]],
        )

        march_response = self.client.post(
            "/api/profile",
            json={
                "month": "2026-03",
                "monthly_income": 2800,
                "fixed_expenses": 650,
                "budgeting_goal": "Pay down dining overspend",
            },
        )
        march_payload = march_response.get_json()
        self.assertEqual(march_response.status_code, 200)
        self.assertEqual(march_payload["profile"]["selected_month"], "2026-03")
        self.assertEqual(march_payload["profile"]["financial_profile"]["monthly_income"], 2800)
        self.assertEqual(len(march_payload["profile"]["monthly_plan_history"]), 2)

        edited_response = self.client.post(
            "/api/profile",
            json={
                "month": "2026-04",
                "monthly_income": 3200,
                "fixed_expenses": 550,
                "budgeting_goal": "Save 700 for a trip",
            },
        )
        edited_payload = edited_response.get_json()
        self.assertEqual(edited_response.status_code, 200)
        self.assertEqual(edited_payload["profile"]["financial_profile"]["monthly_income"], 3200)
        april_entry = next(
            item for item in edited_payload["profile"]["monthly_plan_history"] if item["month_key"] == "2026-04"
        )
        self.assertIn("April 2026, monthly income of $3200.00, fixed expenses of $550.00", april_entry["summary"])

    def test_chat_route_persists_agent_note_from_llm_result(self):
        self._signup_and_login()
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

    def test_chat_route_falls_back_to_local_coaching_when_openai_reply_is_generic_failure(self):
        self._signup_and_login()
        self.client.post(
            "/api/profile",
            json={
                "monthly_income": 3000,
                "fixed_expenses": 1000,
                "budgeting_goal": "Trim subscriptions",
            },
        )
        recurring_csv = """Transaction Date,Description,Category,Amount
02/01/2026,NETFLIX.COM,Subscriptions,-15.49
03/02/2026,NETFLIX.COM,Subscriptions,-15.49
04/03/2026,NETFLIX.COM,Subscriptions,-15.49
"""
        self.client.post(
            "/api/upload-statement",
            data={"statement": (io.BytesIO(recurring_csv.encode("utf-8")), "statement.csv")},
            content_type="multipart/form-data",
        )

        with patch("app.build_agent_service") as build_agent_service:
            build_agent_service.return_value.run_chat_turn.return_value = {
                "reply": "I couldn't produce a reliable coaching response right now.",
                "actions": [],
            }

            response = self.client.post("/api/chat", json={"message": "Should I keep Netflix?"})

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(payload["reply"], "I couldn't produce a reliable coaching response right now.")
        self.assertIn("Netflix", payload["reply"])

    def test_upload_generates_proactive_ai_chatbot_message_with_recurring_and_category_context(self):
        self._signup_and_login()
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
        self.assertTrue("ira" in assistant_message.lower() or "401(k)" in assistant_message.lower() or "401k" in assistant_message.lower())

    def test_dashboard_defaults_to_newest_transaction_month_and_can_switch_months(self):
        self._signup_and_login()
        self.client.post(
            "/api/profile",
            json={
                "monthly_income": 3000,
                "fixed_expenses": 1000,
                "budgeting_goal": "Cut restaurant spending",
            },
        )
        self.client.post(
            "/api/upload-statement",
            data={"statement": (io.BytesIO(MULTI_MONTH_CSV.encode("utf-8")), "statement.csv")},
            content_type="multipart/form-data",
        )

        april_response = self.client.get("/")
        march_response = self.client.get("/?month=2026-03")

        self.assertEqual(april_response.status_code, 200)
        self.assertIn(b'<option value="2026-04" selected>', april_response.data)
        self.assertIn(b"April 2026 focus", april_response.data)
        self.assertIn(b"$1864.51", april_response.data)

        self.assertEqual(march_response.status_code, 200)
        self.assertIn(b'<option value="2026-03" selected>', march_response.data)
        self.assertIn(b"March 2026 focus", march_response.data)
        self.assertIn(b"$1900.00", march_response.data)

    def test_ai_chatbot_can_handle_subscription_cut_request_for_detected_recurring_charge(self):
        self._signup_and_login()
        self.client.post(
            "/api/profile",
            json={
                "monthly_income": 3000,
                "fixed_expenses": 1000,
                "budgeting_goal": "Trim subscriptions",
            },
        )
        pilates_csv = """Transaction Date,Description,Category,Amount
02/01/2026,PILATES CLUB,Wellness,-85.00
03/01/2026,PILATES CLUB,Wellness,-85.00
04/01/2026,PILATES CLUB,Wellness,-85.00
"""
        self.client.post(
            "/api/upload-statement",
            data={"statement": (io.BytesIO(pilates_csv.encode("utf-8")), "statement.csv")},
            content_type="multipart/form-data",
        )

        response = self.client.post("/api/chat", json={"message": "I want to cut pilates"})
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertIn("Pilates", payload["reply"])
        self.assertIn("cancel", payload["reply"].lower())
        self.assertIn(
            "PILATES CLUB",
            [decision["merchant"] for decision in payload["profile"]["subscription_decisions"]],
        )
        self.assertTrue(payload["profile"]["user_decisions"])

    def test_ai_chatbot_can_save_user_decision_note_from_chat(self):
        self._signup_and_login()
        pilates_csv = """Transaction Date,Description,Category,Amount
09/01/2025,CLR*ClubPilate7187010242,Wellness,-107.88
10/01/2025,CLR*ClubPilate7187010242,Wellness,-107.88
11/01/2025,CLR*ClubPilate7187010242,Wellness,-107.88
"""
        self.client.post(
            "/api/upload-statement",
            data={"statement": (io.BytesIO(pilates_csv.encode("utf-8")), "statement.csv")},
            content_type="multipart/form-data",
        )

        response = self.client.post(
            "/api/chat",
            json={"message": "Okay I'm switching my workout class out to yoga instead."},
        )
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertIn("logged", payload["reply"].lower())
        self.assertEqual(payload["action"]["type"], "save_user_decision")
        self.assertTrue(payload["profile"]["user_decisions"])
        self.assertIn("Workout swap", payload["profile"]["user_decisions"][0]["title"])
        self.assertIn("yoga", payload["profile"]["user_decisions"][0]["content"].lower())

    def test_ai_chatbot_can_answer_follow_up_about_called_out_subscription(self):
        self._signup_and_login()
        self.client.post(
            "/api/profile",
            json={
                "monthly_income": 3000,
                "fixed_expenses": 1000,
                "budgeting_goal": "Trim subscriptions",
            },
        )
        recurring_csv = """Transaction Date,Description,Category,Amount
02/01/2026,NETFLIX.COM,Subscriptions,-15.49
03/02/2026,NETFLIX.COM,Subscriptions,-15.49
04/03/2026,NETFLIX.COM,Subscriptions,-15.49
"""
        self.client.post(
            "/api/upload-statement",
            data={"statement": (io.BytesIO(recurring_csv.encode("utf-8")), "statement.csv")},
            content_type="multipart/form-data",
        )

        response = self.client.post("/api/chat", json={"message": "Should I keep it?"})
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertIn("Netflix", payload["reply"])
        self.assertTrue("keep" in payload["reply"].lower() or "cut" in payload["reply"].lower())

    def test_ai_chatbot_can_add_generic_spend_without_merchant_name(self):
        self._signup_and_login()

        response = self.client.post("/api/chat", json={"message": "I spent $40"})
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["action"]["type"], "none")
        self.assertEqual(payload["profile"]["transaction_count"], 0)
        self.assertIn("where", payload["reply"].lower())
        self.assertIn("when", payload["reply"].lower())
        self.assertIn("how", payload["reply"].lower())
        self.assertIn("already counted", payload["reply"].lower())

    def test_ai_chatbot_can_give_grounded_advice_from_saved_context(self):
        self._signup_and_login()
        self.client.post(
            "/api/profile",
            json={
                "monthly_income": 3000,
                "fixed_expenses": 1000,
                "budgeting_goal": "Spend less on dining",
            },
        )
        recurring_csv = """Transaction Date,Description,Category,Amount
02/01/2026,NETFLIX.COM,Subscriptions,-15.49
03/02/2026,NETFLIX.COM,Subscriptions,-15.49
04/03/2026,NETFLIX.COM,Subscriptions,-15.49
04/06/2026,Restaurant Row,Dining,-120.00
"""
        self.client.post(
            "/api/upload-statement",
            data={"statement": (io.BytesIO(recurring_csv.encode("utf-8")), "statement.csv")},
            content_type="multipart/form-data",
        )

        response = self.client.post("/api/chat", json={"message": "What should I cut this month?"})
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertIn("Dining", payload["reply"])
        self.assertIn("Netflix", payload["reply"])
        self.assertIn("Spend less on dining", payload["reply"])
        self.assertIn("Focus:", payload["reply"])
        self.assertIn("Next cut:", payload["reply"])
        self.assertIn("Action:", payload["reply"])

    def test_ai_chatbot_can_build_a_tight_plan_from_saved_context(self):
        self._signup_and_login()
        self.client.post(
            "/api/profile",
            json={
                "monthly_income": 1000,
                "fixed_expenses": 500,
                "budgeting_goal": "Save 200 for a trip",
            },
        )
        recurring_csv = """Transaction Date,Description,Category,Amount
02/03/2026,NETFLIX.COM,Subscriptions,-15.49
03/03/2026,NETFLIX.COM,Subscriptions,-15.49
04/03/2026,NETFLIX.COM,Subscriptions,-15.49
04/06/2026,Restaurant Row,Dining,-220.00
04/07/2026,Grocer,Groceries,-90.00
"""
        self.client.post(
            "/api/upload-statement",
            data={"statement": (io.BytesIO(recurring_csv.encode("utf-8")), "statement.csv")},
            content_type="multipart/form-data",
        )

        response = self.client.post("/api/chat", json={"message": "help me build a tight plan"})
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertIn("Tight plan for", payload["reply"])
        self.assertIn("1.", payload["reply"])
        self.assertIn("2.", payload["reply"])
        self.assertIn("3.", payload["reply"])
        self.assertIn("Dining", payload["reply"])
        self.assertIn("Netflix", payload["reply"])
        self.assertIn("Save 200 for a trip", payload["reply"])

    def test_ai_chatbot_answers_monthly_gas_average_from_uploaded_history(self):
        self._signup_and_login()
        self.client.post(
            "/api/upload-statement",
            data={"statement": (io.BytesIO(GAS_AND_MEMBERSHIP_CSV.encode("utf-8")), "statement.csv")},
            content_type="multipart/form-data",
        )

        response = self.client.post("/api/chat", json={"message": "how much do i spend on gas monthly"})
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertIn("average monthly gas spend is $99.65", payload["reply"].lower())
        self.assertNotIn("main focus", payload["reply"].lower())

    def test_ai_chatbot_identifies_biggest_food_and_drink_merchant_for_named_month(self):
        self._signup_and_login()
        self.client.post(
            "/api/upload-statement",
            data={"statement": (io.BytesIO(OCT_NOV_FOOD_CSV.encode("utf-8")), "statement.csv")},
            content_type="multipart/form-data",
        )

        response = self.client.post(
            "/api/chat",
            json={"message": "identify the biggest food and drink merchant for october"},
        )
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertIn("October", payload["reply"])
        self.assertIn("Joe's Pizza", payload["reply"])
        self.assertIn("$60.50", payload["reply"])

    def test_ai_chatbot_subscription_alternatives_first_ask_for_city(self):
        self._signup_and_login()
        pilates_csv = """Transaction Date,Description,Category,Amount
09/01/2025,CLR*ClubPilate7187010242,Wellness,-107.88
10/01/2025,CLR*ClubPilate7187010242,Wellness,-107.88
11/01/2025,CLR*ClubPilate7187010242,Wellness,-107.88
"""
        self.client.post(
            "/api/upload-statement",
            data={"statement": (io.BytesIO(pilates_csv.encode("utf-8")), "statement.csv")},
            content_type="multipart/form-data",
        )

        response = self.client.post(
            "/api/chat",
            json={"message": "what alternatives do i have to club pilates"},
        )
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertIn("which city you are in", payload["reply"].lower())
        self.assertIn("yoga", payload["reply"].lower())
        self.assertIn("community rec", payload["reply"].lower())

    def test_ai_chatbot_subscription_alternatives_use_city_follow_up(self):
        self._signup_and_login()
        pilates_csv = """Transaction Date,Description,Category,Amount
09/01/2025,CLR*ClubPilate7187010242,Wellness,-107.88
10/01/2025,CLR*ClubPilate7187010242,Wellness,-107.88
11/01/2025,CLR*ClubPilate7187010242,Wellness,-107.88
"""
        self.client.post(
            "/api/upload-statement",
            data={"statement": (io.BytesIO(pilates_csv.encode("utf-8")), "statement.csv")},
            content_type="multipart/form-data",
        )

        self.client.post(
            "/api/chat",
            json={"message": "what alternatives do i have to club pilates"},
        )
        response = self.client.post(
            "/api/chat",
            json={"message": "Boston"},
        )
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertIn("Boston", payload["reply"])
        self.assertIn("\n1.", payload["reply"])
        self.assertIn("\n2.", payload["reply"])
        self.assertIn("\n3.", payload["reply"])
        self.assertIn("yoga", payload["reply"].lower())
        self.assertIn("ymca", payload["reply"].lower())

    def test_gas_station_repeat_charges_are_not_treated_as_recurring_subscriptions(self):
        self._signup_and_login()
        response = self.client.post(
            "/api/upload-statement",
            data={"statement": (io.BytesIO(GAS_AND_MEMBERSHIP_CSV.encode("utf-8")), "statement.csv")},
            content_type="multipart/form-data",
        )

        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        merchants = [item["merchant"] for item in payload["profile"]["subscriptions"]]
        self.assertIn("CLR*ClubPilate7187010242", merchants)
        self.assertNotIn("BP#34122123010 OCEAN BP", merchants)

    def test_logged_in_dashboard_shows_income_and_leftover_money_sections(self):
        self._signup_and_login(follow_redirects=True)

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Monthly income", response.data)
        self.assertIn(b"Fixed expenses", response.data)
        self.assertIn(b"Left this month", response.data)
        self.assertIn(b"Category breakdown", response.data)
        self.assertIn(b"category-donut-chart", response.data)
        self.assertIn(b"month-selector", response.data)
        self.assertIn(b"transactions-category-filter", response.data)
        self.assertIn(b"User decisions & notes", response.data)
        self.assertIn(b"messages-toggle", response.data)
        self.assertIn(b"messages-jump-bottom", response.data)
        self.assertIn(b"data-tooltip=", response.data)
        self.assertIn(b"chart-tooltip", response.data)

    def test_logged_out_homepage_shows_forgot_password_form(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Forgot password?", response.data)
        self.assertIn(b"/forgot-password", response.data)
        self.assertIn(b"No account?", response.data)
        self.assertIn(b"Create one now", response.data)
        self.assertIn(b'id="signup-panel" hidden', response.data)

    def test_signup_error_reopens_create_account_panel(self):
        self.client.post("/signup", data={"email": "demo@example.com", "password": "secret123"})

        response = self.client.post(
            "/signup",
            data={"email": "demo@example.com", "password": "secret123"},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"An account with that email already exists.", response.data)
        self.assertIn(b'id="signup-panel"', response.data)
        self.assertNotIn(b'id="signup-panel" hidden', response.data)

    def test_dashboard_shows_top_three_insights_for_selected_month(self):
        self._signup_and_login()
        self.client.post(
            "/api/upload-statement",
            data={"statement": (io.BytesIO(INSIGHTS_CSV.encode("utf-8")), "statement.csv")},
            content_type="multipart/form-data",
        )
        self.client.post(
            "/api/profile",
            json={
                "month": "2026-04",
                "monthly_income": 1000,
                "fixed_expenses": 300,
                "budgeting_goal": "Save 1000 for travel",
            },
        )

        response = self.client.get("/?month=2026-04")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Top 3 Insights This Month", response.data)
        self.assertIn(b"more on Dining compared to your 3-month average", response.data)
        self.assertIn(b"recurring subscriptions totaling", response.data)
        self.assertIn(b"reaching your $1,000 goal", response.data)

    def test_dashboard_shows_recommended_actions_above_subscriptions(self):
        self._signup_and_login()
        self.client.post(
            "/api/upload-statement",
            data={"statement": (io.BytesIO(INSIGHTS_CSV.encode("utf-8")), "statement.csv")},
            content_type="multipart/form-data",
        )
        self.client.post(
            "/api/profile",
            json={
                "month": "2026-04",
                "monthly_income": 1000,
                "fixed_expenses": 300,
                "budgeting_goal": "Save 1000 for travel",
            },
        )

        response = self.client.get("/?month=2026-04")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Recommended Actions", response.data)
        self.assertIn(b"Reduce Dining by", response.data)
        self.assertIn(b"Cancel 1 subscription", response.data)
        self.assertIn(b"Set weekly discretionary cap", response.data)
        self.assertLess(response.data.find(b"Recommended Actions"), response.data.find(b"Recurring subscriptions"))

    def test_dashboard_category_breakdown_shows_budget_and_last_month_context(self):
        self._signup_and_login()
        category_csv = """Transaction Date,Description,Category,Amount
03/04/2026,Restaurant Row,Dining,-150.00
04/04/2026,Restaurant Row,Dining,-420.00
"""
        self.client.post(
            "/api/upload-statement",
            data={"statement": (io.BytesIO(category_csv.encode("utf-8")), "statement.csv")},
            content_type="multipart/form-data",
        )
        self.client.post(
            "/api/profile",
            json={
                "month": "2026-04",
                "monthly_income": 1000,
                "fixed_expenses": 200,
                "budgeting_goal": "Save 500 for travel",
            },
        )

        response = self.client.get("/?month=2026-04")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"vs budget", response.data)
        self.assertIn(b"vs last month", response.data)
        self.assertIn(b"OVER budget", response.data)
        self.assertIn(b"legend-item overspending", response.data)

    def test_ai_chatbot_can_surface_behavioral_patterns(self):
        self._signup_and_login()
        self.client.post(
            "/api/upload-statement",
            data={"statement": (io.BytesIO(BEHAVIOR_CSV.encode("utf-8")), "statement.csv")},
            content_type="multipart/form-data",
        )
        self.client.post(
            "/api/profile",
            json={
                "month": "2026-04",
                "monthly_income": 1000,
                "fixed_expenses": 200,
                "budgeting_goal": "Save 500 for travel",
            },
        )

        response = self.client.post("/api/chat", json={"message": "what behavioral patterns do you see?"})
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertIn("Behavior patterns", payload["reply"])
        self.assertTrue(
            "weekend" in payload["reply"].lower()
            or "travel" in payload["reply"].lower()
            or "week 3" in payload["reply"].lower()
        )

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

    def test_receipt_upload_creates_multiple_review_cards(self):
        self._signup_and_login()

        response = self.client.post(
            "/api/upload-receipts",
            data={
                "receipts": [
                    (io.BytesIO(b"fake image 1"), "receipt-1.jpg"),
                    (io.BytesIO(b"fake image 2"), "receipt-2.jpg"),
                ]
            },
            content_type="multipart/form-data",
        )

        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertIn("receipts", payload)
        self.assertEqual(len(payload["receipts"]), 2)
        self.assertEqual(payload["receipts"][0]["status"], "needs_correction")
        self.assertEqual(payload["receipts"][1]["status"], "needs_correction")
        self.assertIn("id", payload["receipts"][0])
        self.assertIn("id", payload["receipts"][1])

    def test_receipt_review_allows_approving_uploaded_receipt_with_corrected_fields(self):
        self._signup_and_login()

        receipt = self._upload_receipt_for_review("receipt-approve.jpg")
        response = self.client.post(
            f"/api/receipts/{receipt['id']}/approve",
            json={
                "merchant": "Trader Joe's",
                "transaction_date": "2026-04-23",
                "total_amount": 48.22,
                "category": "Groceries",
                "month": "2026-04",
            },
        )

        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertIn("transaction_id", payload)
        self.assertEqual(payload["profile"]["transaction_count"], 1)
        self.assertEqual(payload["profile"]["transactions"][0]["description"], "Trader Joe's")
        self.assertEqual(payload["profile"]["transactions"][0]["category"], "Groceries")
        self.assertEqual(payload["profile"]["transactions"][0]["source"], "receipt")

    def test_receipt_review_rejects_invalid_approval_payloads(self):
        self._signup_and_login()
        receipt = self._upload_receipt_for_review("receipt-invalid.jpg")

        invalid_cases = [
            (
                {
                    "merchant": "   ",
                    "transaction_date": "2026-04-23",
                    "total_amount": 48.22,
                    "category": "Groceries",
                },
                {"error": "Merchant is required before saving this receipt."},
            ),
            (
                {
                    "merchant": "Trader Joe's",
                    "transaction_date": "   ",
                    "total_amount": 48.22,
                    "category": "Groceries",
                },
                {"error": "Transaction date is required before saving this receipt."},
            ),
            (
                {
                    "merchant": "Trader Joe's",
                    "transaction_date": "04/23/2026",
                    "total_amount": 48.22,
                    "category": "Groceries",
                },
                {"error": "Transaction date must use YYYY-MM-DD."},
            ),
            (
                {
                    "merchant": "Trader Joe's",
                    "transaction_date": "2026-04-23",
                    "total_amount": "abc",
                    "category": "Groceries",
                },
                {"error": "Total amount must be a valid number greater than 0."},
            ),
            (
                {
                    "merchant": "Trader Joe's",
                    "transaction_date": "2026-04-23",
                    "total_amount": "NaN",
                    "category": "Groceries",
                },
                {"error": "Total amount must be a valid number greater than 0."},
            ),
            (
                {
                    "merchant": "Trader Joe's",
                    "transaction_date": "2026-04-23",
                    "total_amount": "Infinity",
                    "category": "Groceries",
                },
                {"error": "Total amount must be a valid number greater than 0."},
            ),
            (
                {
                    "merchant": "Trader Joe's",
                    "transaction_date": "2026-04-23",
                    "total_amount": 0,
                    "category": "Groceries",
                },
                {"error": "Total amount must be a valid number greater than 0."},
            ),
            (
                {
                    "merchant": "Trader Joe's",
                    "transaction_date": "2026-04-23",
                    "total_amount": 48.22,
                    "category": "   ",
                },
                {"error": "Choose a category before saving this receipt."},
            ),
        ]

        for payload, expected in invalid_cases:
            with self.subTest(payload=payload):
                response = self.client.post(
                    f"/api/receipts/{receipt['id']}/approve",
                    json=payload,
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.get_json(), expected)

    def test_receipt_discard_rejects_already_approved_receipt(self):
        self._signup_and_login()
        receipt = self._upload_receipt_for_review("receipt-approved.jpg")
        approve_response = self.client.post(
            f"/api/receipts/{receipt['id']}/approve",
            json={
                "merchant": "Sweetgreen",
                "transaction_date": "2026-04-23",
                "total_amount": 18.5,
                "category": "Dining",
                "month": "2026-04",
            },
        )
        self.assertEqual(approve_response.status_code, 200)

        response = self.client.post(f"/api/receipts/{receipt['id']}/discard")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json(),
            {"error": "Receipt extraction has already been finalized."},
        )

    def test_receipt_upload_marks_persistence_failures_as_error_cards(self):
        self._signup_and_login()

        with patch("app.extract_receipt_batch") as extract_receipt_batch:
            extract_receipt_batch.return_value = [
                {
                    "receipt_upload_id": 101,
                    "merchant": "Sweetgreen",
                    "transaction_date": "2026-04-23",
                    "total_amount": 18.5,
                    "category": "Dining",
                    "category_confidence": 0.91,
                    "status": "ready",
                    "behavior_note": "",
                    "item_tags": ["lunch"],
                },
                {
                    "receipt_upload_id": 102,
                    "merchant": "",
                    "transaction_date": "",
                    "total_amount": 0.0,
                    "category": "",
                    "category_confidence": 0.0,
                    "status": "error",
                    "behavior_note": "Could not read receipt.",
                    "item_tags": [],
                },
            ]

            response = self.client.post(
                "/api/upload-receipts",
                data={
                    "receipts": [
                        (io.BytesIO(b"fake image 1"), "receipt-1.jpg"),
                        (io.BytesIO(b"fake image 2"), "receipt-2.jpg"),
                    ]
                },
                content_type="multipart/form-data",
            )

        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(payload["receipts"]), 2)
        self.assertEqual(payload["receipts"][0]["status"], "error")
        self.assertEqual(payload["receipts"][0]["behavior_note"], "Receipt upload does not exist.")
        self.assertNotIn("id", payload["receipts"][0])
        self.assertEqual(payload["receipts"][1]["status"], "error")

    def test_receipt_upload_converts_real_storage_exceptions_into_error_cards(self):
        self._signup_and_login()

        with patch.object(self.app.config["storage"], "save_receipt_extraction", side_effect=sqlite3.OperationalError("db locked")):
            response = self.client.post(
                "/api/upload-receipts",
                data={"receipts": [(io.BytesIO(b"fake image"), "receipt-1.jpg")]},
                content_type="multipart/form-data",
            )

        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(payload["receipts"]), 1)
        self.assertEqual(payload["receipts"][0]["status"], "error")
        self.assertEqual(payload["receipts"][0]["behavior_note"], "db locked")
        self.assertNotIn("id", payload["receipts"][0])

    def test_receipt_upload_converts_create_upload_failures_into_error_cards_without_blocking_batch(self):
        self._signup_and_login()

        original_create_receipt_upload = self.app.config["storage"].create_receipt_upload
        call_count = 0

        def flaky_create_receipt_upload(user_id, filename, storage_path):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise sqlite3.OperationalError("upload row locked")
            return original_create_receipt_upload(user_id, filename, storage_path)

        with patch.object(self.app.config["storage"], "create_receipt_upload", side_effect=flaky_create_receipt_upload):
            response = self.client.post(
                "/api/upload-receipts",
                data={
                    "receipts": [
                        (io.BytesIO(b"fake image 1"), "receipt-1.jpg"),
                        (io.BytesIO(b"fake image 2"), "receipt-2.jpg"),
                    ]
                },
                content_type="multipart/form-data",
            )

        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(payload["receipts"]), 2)
        self.assertEqual(payload["receipts"][0]["status"], "error")
        self.assertEqual(payload["receipts"][0]["behavior_note"], "upload row locked")
        self.assertNotIn("id", payload["receipts"][0])
        self.assertEqual(payload["receipts"][1]["status"], "needs_correction")
        self.assertIn("id", payload["receipts"][1])

    def test_receipt_upload_reuses_cached_merchant_category_before_web_lookup(self):
        self._signup_and_login()
        self.app.config["storage"].save_cached_merchant_category("trader joes", "Groceries", 0.97, "cache")

        with patch("app.extract_receipt_batch") as extract_receipt_batch, patch(
            "app.enrich_merchant_category_from_web",
            create=True,
        ) as enrich_merchant_category_from_web:
            extract_receipt_batch.return_value = [
                {
                    "receipt_upload_id": self.app.config["storage"].create_receipt_upload(
                        1,
                        "receipt-1.jpg",
                        "uploads/receipt-1.jpg",
                    ),
                    "merchant": "Trader Joes",
                    "transaction_date": "2026-04-23",
                    "total_amount": 48.22,
                    "category": "",
                    "category_confidence": 0.0,
                    "status": "needs_correction",
                    "behavior_note": "",
                    "item_tags": ["groceries"],
                }
            ]

            response = self.client.post(
                "/api/upload-receipts",
                data={"receipts": [(io.BytesIO(b"fake image"), "receipt-1.jpg")]},
                content_type="multipart/form-data",
            )

        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(payload["receipts"]), 1)
        self.assertEqual(payload["receipts"][0]["category"], "Groceries")
        self.assertEqual(payload["receipts"][0]["category_confidence"], 0.97)
        self.assertEqual(payload["receipts"][0]["status"], "ready")
        enrich_merchant_category_from_web.assert_not_called()

    def test_receipt_upload_reuses_legacy_format_cached_merchant_key_after_normalization_change(self):
        self._signup_and_login()

        with self.app.config["storage"]._connect() as conn:
            conn.execute(
                """
                INSERT INTO merchant_category_cache (
                    merchant_key,
                    category,
                    confidence,
                    enrichment_source,
                    checked_at
                )
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                ("corner-shop", "Groceries", 0.97, "cache"),
            )

        with patch("app.extract_receipt_batch") as extract_receipt_batch, patch(
            "app.enrich_merchant_category_from_web",
            create=True,
        ) as enrich_merchant_category_from_web:
            extract_receipt_batch.return_value = [
                {
                    "receipt_upload_id": self.app.config["storage"].create_receipt_upload(
                        1,
                        "receipt-legacy.jpg",
                        "uploads/receipt-legacy.jpg",
                    ),
                    "merchant": "Corner-Shop",
                    "transaction_date": "2026-04-23",
                    "total_amount": 44.15,
                    "category": "",
                    "category_confidence": 0.0,
                    "status": "needs_correction",
                    "behavior_note": "",
                    "item_tags": [],
                }
            ]

            response = self.client.post(
                "/api/upload-receipts",
                data={"receipts": [(io.BytesIO(b"fake image"), "receipt-legacy.jpg")]},
                content_type="multipart/form-data",
            )

        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["receipts"][0]["category"], "Groceries")
        self.assertEqual(payload["receipts"][0]["category_confidence"], 0.97)
        self.assertEqual(payload["receipts"][0]["status"], "ready")
        enrich_merchant_category_from_web.assert_not_called()

    def test_receipt_upload_skips_web_lookup_for_high_confidence_extracted_category(self):
        self._signup_and_login()

        with patch("app.extract_receipt_batch") as extract_receipt_batch, patch(
            "app.enrich_merchant_category_from_web",
            create=True,
        ) as enrich_merchant_category_from_web:
            extract_receipt_batch.return_value = [
                {
                    "receipt_upload_id": self.app.config["storage"].create_receipt_upload(
                        1,
                        "receipt-ready.jpg",
                        "uploads/receipt-ready.jpg",
                    ),
                    "merchant": "Known Market",
                    "transaction_date": "2026-04-23",
                    "total_amount": 58.10,
                    "category": "Groceries",
                    "category_confidence": 0.94,
                    "status": "ready",
                    "behavior_note": "",
                    "item_tags": ["produce"],
                }
            ]

            response = self.client.post(
                "/api/upload-receipts",
                data={"receipts": [(io.BytesIO(b"fake image"), "receipt-ready.jpg")]},
                content_type="multipart/form-data",
            )

        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(payload["receipts"]), 1)
        self.assertEqual(payload["receipts"][0]["category"], "Groceries")
        self.assertEqual(payload["receipts"][0]["category_confidence"], 0.94)
        self.assertEqual(payload["receipts"][0]["status"], "ready")
        enrich_merchant_category_from_web.assert_not_called()

    def test_receipt_upload_marks_low_confidence_prefilled_category_as_needs_category(self):
        self._signup_and_login()

        with patch("app.extract_receipt_batch") as extract_receipt_batch, patch(
            "app.enrich_merchant_category_from_web",
            create=True,
        ) as enrich_merchant_category_from_web:
            extract_receipt_batch.return_value = [
                {
                    "receipt_upload_id": self.app.config["storage"].create_receipt_upload(
                        1,
                        "receipt-2.jpg",
                        "uploads/receipt-2.jpg",
                    ),
                    "merchant": "Unknown Corner Shop",
                    "transaction_date": "2026-04-23",
                    "total_amount": 19.45,
                    "category": "Dining",
                    "category_confidence": 0.42,
                    "status": "needs_correction",
                    "behavior_note": "",
                    "item_tags": [],
                }
            ]
            enrich_merchant_category_from_web.return_value = {
                "category": "",
                "confidence": 0.31,
                "source": "web",
            }

            response = self.client.post(
                "/api/upload-receipts",
                data={"receipts": [(io.BytesIO(b"fake image"), "receipt-2.jpg")]},
                content_type="multipart/form-data",
            )

        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(payload["receipts"]), 1)
        self.assertEqual(payload["receipts"][0]["category"], "")
        self.assertEqual(payload["receipts"][0]["status"], "needs_category")
        self.assertEqual(payload["receipts"][0]["category_confidence"], 0.0)
        enrich_merchant_category_from_web.assert_called_once_with("Unknown Corner Shop")

    def test_receipt_upload_clears_low_confidence_category_when_merchant_is_blank(self):
        self._signup_and_login()

        with patch("app.extract_receipt_batch") as extract_receipt_batch, patch(
            "app.enrich_merchant_category_from_web",
            create=True,
        ) as enrich_merchant_category_from_web:
            extract_receipt_batch.return_value = [
                {
                    "receipt_upload_id": self.app.config["storage"].create_receipt_upload(
                        1,
                        "receipt-blank-merchant.jpg",
                        "uploads/receipt-blank-merchant.jpg",
                    ),
                    "merchant": "",
                    "transaction_date": "2026-04-23",
                    "total_amount": 17.25,
                    "category": "Dining",
                    "category_confidence": 0.41,
                    "status": "needs_correction",
                    "behavior_note": "",
                    "item_tags": [],
                }
            ]

            response = self.client.post(
                "/api/upload-receipts",
                data={"receipts": [(io.BytesIO(b"fake image"), "receipt-blank-merchant.jpg")]},
                content_type="multipart/form-data",
            )

        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["receipts"][0]["merchant"], "")
        self.assertEqual(payload["receipts"][0]["category"], "")
        self.assertEqual(payload["receipts"][0]["category_confidence"], 0.0)
        self.assertEqual(payload["receipts"][0]["status"], "needs_category")
        enrich_merchant_category_from_web.assert_not_called()

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
