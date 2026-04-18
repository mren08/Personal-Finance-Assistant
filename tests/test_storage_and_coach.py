import tempfile
import unittest

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
    def test_chat_message_can_add_manual_restaurant_transaction(self):
        coach = OverspendingCoach()

        result = coach.process_message(
            "I ate at Howoo for $50 and paid my friend back on Zelle.",
            profile={"subscriptions": [], "category_totals": {}},
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


if __name__ == "__main__":
    unittest.main()
