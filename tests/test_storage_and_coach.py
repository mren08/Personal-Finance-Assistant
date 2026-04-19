import tempfile
import unittest
import sqlite3

from coach import OverspendingCoach
from storage import Storage


class StorageTests(unittest.TestCase):
    def test_storage_creates_user_and_persists_transactions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")

            user_id = storage.create_user("michelle@example.com", "secret123")
            storage.add_transactions(
                user_id,
                [
                    {
                        "date": "2026-04-01",
                        "description": "NETFLIX.COM",
                        "amount": 15.49,
                        "category": "Subscriptions",
                        "source": "statement",
                    }
                ],
            )

            profile = storage.get_dashboard_data(user_id)

            self.assertEqual(profile["transaction_count"], 1)
            self.assertEqual(profile["category_totals"]["Subscriptions"], 15.49)

    def test_storage_persists_profile_notes_and_monthly_summary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")
            user_id = storage.create_user("michelle@example.com", "secret123")

            storage.upsert_financial_profile(
                user_id,
                monthly_income=4200,
                fixed_expenses=1800,
                budgeting_goal="Cut dining spend",
            )
            storage.save_agent_note(
                user_id,
                note_type="behavior_pattern",
                content="Dining usually spikes on weekends.",
            )
            storage.save_monthly_summary(
                user_id,
                month_key="2026-04",
                income=4200,
                fixed_expenses=1800,
                tracked_spending=1200,
                recurring_monthly_total=80,
                leftover_money=3000,
                discretionary_remaining=1200,
                summary_text="You still have room this month, but dining is the swing category.",
            )

            profile = storage.get_dashboard_data(user_id)

            self.assertEqual(profile["financial_profile"]["monthly_income"], 4200)
            self.assertEqual(profile["financial_profile"]["fixed_expenses"], 1800)
            self.assertEqual(profile["financial_profile"]["budgeting_goal"], "Cut dining spend")
            self.assertEqual(profile["agent_notes"][0]["content"], "Dining usually spikes on weekends.")
            self.assertEqual(profile["monthly_summary"]["leftover_money"], 3000)

    def test_storage_returns_latest_month_summary_even_if_an_older_month_is_regenerated(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")
            user_id = storage.create_user("michelle@example.com", "secret123")

            storage.save_monthly_summary(
                user_id,
                month_key="2026-04",
                income=4200,
                fixed_expenses=1800,
                tracked_spending=1200,
                recurring_monthly_total=80,
                leftover_money=3000,
                discretionary_remaining=1200,
                summary_text="April summary",
            )
            storage.save_monthly_summary(
                user_id,
                month_key="2026-05",
                income=4300,
                fixed_expenses=1800,
                tracked_spending=1000,
                recurring_monthly_total=90,
                leftover_money=3300,
                discretionary_remaining=1400,
                summary_text="May summary",
            )
            storage.save_monthly_summary(
                user_id,
                month_key="2026-04",
                income=4200,
                fixed_expenses=1800,
                tracked_spending=1250,
                recurring_monthly_total=80,
                leftover_money=2950,
                discretionary_remaining=1150,
                summary_text="April summary regenerated later",
            )

            summary = storage.get_dashboard_data(user_id)["monthly_summary"]

            self.assertEqual(summary["month_key"], "2026-05")
            self.assertEqual(summary["summary_text"], "May summary")

    def test_storage_rejects_orphan_profile_note_and_summary_writes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")

            with self.assertRaises(sqlite3.IntegrityError):
                storage.upsert_financial_profile(
                    999,
                    monthly_income=4200,
                    fixed_expenses=1800,
                    budgeting_goal="Cut dining spend",
                )

            with self.assertRaises(sqlite3.IntegrityError):
                storage.save_agent_note(999, note_type="behavior_pattern", content="No user exists.")

            with self.assertRaises(sqlite3.IntegrityError):
                storage.save_monthly_summary(
                    999,
                    month_key="2026-04",
                    income=4200,
                    fixed_expenses=1800,
                    tracked_spending=1200,
                    recurring_monthly_total=80,
                    leftover_money=3000,
                    discretionary_remaining=1200,
                    summary_text="Orphan summary",
                )

    def test_storage_saves_chat_history(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")

            user_id = storage.create_user("michelle@example.com", "secret123")
            storage.add_chat_message(user_id, "user", "Do I really need Netflix?")
            storage.add_chat_message(user_id, "assistant", "No. Pick one streaming service.")

            messages = storage.list_chat_messages(user_id)

            self.assertEqual(len(messages), 2)
            self.assertEqual(messages[0]["role"], "user")
            self.assertIn("Pick one", messages[1]["content"])


class CoachTests(unittest.TestCase):
    def test_chat_message_asks_for_confirmation_when_existing_match_is_uncertain(self):
        coach = OverspendingCoach()

        result = coach.process_message(
            "I spent $50 at Howoo.",
            profile={
                "subscriptions": [],
                "category_totals": {},
                "transactions": [
                    {
                        "date": "2026-04-18",
                        "description": "HOWOO KOREAN STEAKHOUSE",
                        "amount": 50.0,
                        "category": "Dining",
                        "source": "statement",
                    }
                ],
                "pending_action": None,
            },
        )

        self.assertEqual(result["action"]["type"], "confirm_transaction_match")
        self.assertIn("already included", result["reply"])

    def test_chat_message_can_add_manual_restaurant_transaction_when_no_match_exists(self):
        coach = OverspendingCoach()

        result = coach.process_message(
            "I spent $50 at Howoo.",
            profile={"subscriptions": [], "category_totals": {}, "transactions": [], "pending_action": None},
        )

        self.assertEqual(result["action"]["type"], "add_transaction")
        self.assertEqual(result["action"]["transaction"]["amount"], 50.0)
        self.assertEqual(result["action"]["transaction"]["category"], "Dining")
        self.assertIn("Howoo", result["reply"])

    def test_chat_message_can_mark_subscription_to_cancel(self):
        coach = OverspendingCoach()

        result = coach.process_message(
            "Cancel Netflix. I do not need it.",
            profile={"subscriptions": [{"merchant": "Netflix", "monthly_equivalent": 15.49}]},
        )

        self.assertEqual(result["action"]["type"], "mark_subscription_cancel")
        self.assertEqual(result["action"]["merchant"], "Netflix")
        self.assertIn("Cancel it", result["reply"])

    def test_confirmation_reply_of_no_adds_pending_transaction(self):
        coach = OverspendingCoach()

        result = coach.process_message(
            "No, it is not in there yet.",
            profile={
                "subscriptions": [],
                "category_totals": {},
                "transactions": [],
                "pending_action": {
                    "type": "confirm_transaction_match",
                    "transaction": {
                        "date": "2026-04-18",
                        "description": "Howoo",
                        "amount": 50.0,
                        "category": "Dining",
                        "source": "chat_manual",
                    },
                },
            },
        )

        self.assertEqual(result["action"]["type"], "add_transaction")
        self.assertIn("adding it now", result["reply"].lower())


if __name__ == "__main__":
    unittest.main()
