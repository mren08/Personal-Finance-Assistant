import tempfile
import unittest
import sqlite3
from unittest import mock

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

    def test_storage_creates_and_lists_pending_receipt_extractions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")

            user_id = storage.create_user("demo@example.com", "secret123")
            receipt_id = storage.create_receipt_upload(
                user_id,
                filename="receipt-1.jpg",
                storage_path="uploads/receipt-1.jpg",
            )
            storage.save_receipt_extraction(
                user_id,
                receipt_upload_id=receipt_id,
                merchant="Trader Joe's",
                transaction_date="2026-04-23",
                total_amount=48.22,
                category="Groceries",
                category_confidence=0.94,
                status="ready",
                behavior_note="This fits your normal grocery pattern.",
                item_tags_json='["essential spending"]',
                raw_extraction_json='{"total":"48.22"}',
                web_enrichment_json='{"source":"none"}',
            )

            receipts = storage.list_pending_receipt_extractions(user_id)

            self.assertEqual(len(receipts), 1)
            self.assertEqual(receipts[0]["merchant"], "Trader Joe's")
            self.assertEqual(receipts[0]["status"], "ready")

    def test_storage_approves_receipt_into_single_transaction_and_links_it(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")

            user_id = storage.create_user("demo@example.com", "secret123")
            receipt_id = storage.create_receipt_upload(user_id, "receipt-2.jpg", "uploads/receipt-2.jpg")
            extraction_id = storage.save_receipt_extraction(
                user_id,
                receipt_upload_id=receipt_id,
                merchant="Sweetgreen",
                transaction_date="2026-04-23",
                total_amount=18.50,
                category="Dining",
                category_confidence=0.91,
                status="ready",
                behavior_note="This is your 5th dining expense this week.",
                item_tags_json="[]",
                raw_extraction_json="{}",
                web_enrichment_json='{"source":"cache"}',
            )

            transaction_id = storage.approve_receipt_extraction(
                user_id,
                extraction_id,
                merchant="Sweetgreen",
                transaction_date="2026-04-23",
                total_amount=18.50,
                category="Dining",
            )
            dashboard = storage.get_dashboard_data(user_id, "2026-04")

            self.assertEqual(dashboard["transaction_count"], 1)
            self.assertEqual(dashboard["transactions"][0]["description"], "Sweetgreen")
            self.assertEqual(dashboard["transactions"][0]["category"], "Dining")
            self.assertEqual(
                storage.get_receipt_transaction_link(extraction_id)["transaction_id"],
                transaction_id,
            )

    def test_storage_rolls_back_receipt_approval_when_transaction_insert_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")

            user_id = storage.create_user("demo@example.com", "secret123")
            receipt_id = storage.create_receipt_upload(user_id, "receipt-3.jpg", "uploads/receipt-3.jpg")
            extraction_id = storage.save_receipt_extraction(
                user_id,
                receipt_upload_id=receipt_id,
                merchant="Whole Foods",
                transaction_date="2026-04-23",
                total_amount=31.10,
                category="Groceries",
                category_confidence=0.97,
                status="ready",
                behavior_note="This fits your normal grocery pattern.",
                item_tags_json="[]",
                raw_extraction_json="{}",
                web_enrichment_json="{}",
            )

            with mock.patch.object(storage, "_insert_transaction_row", side_effect=RuntimeError("boom")):
                with self.assertRaises(RuntimeError):
                    storage.approve_receipt_extraction(
                        user_id,
                        extraction_id,
                        merchant="Whole Foods",
                        transaction_date="2026-04-23",
                        total_amount=31.10,
                        category="Groceries",
                    )

            receipts = storage.list_pending_receipt_extractions(user_id)
            dashboard = storage.get_dashboard_data(user_id, "2026-04")

            self.assertEqual(len(receipts), 1)
            self.assertEqual(receipts[0]["status"], "ready")
            self.assertEqual(dashboard["transaction_count"], 0)
            self.assertIsNone(storage.get_receipt_transaction_link(extraction_id))

    def test_storage_rejects_duplicate_or_disallowed_receipt_approval_states(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")

            user_id = storage.create_user("demo@example.com", "secret123")
            approved_upload_id = storage.create_receipt_upload(user_id, "receipt-4.jpg", "uploads/receipt-4.jpg")
            approved_extraction_id = storage.save_receipt_extraction(
                user_id,
                receipt_upload_id=approved_upload_id,
                merchant="Sweetgreen",
                transaction_date="2026-04-23",
                total_amount=18.50,
                category="Dining",
                category_confidence=0.91,
                status="ready",
                behavior_note="This is your 5th dining expense this week.",
                item_tags_json="[]",
                raw_extraction_json="{}",
                web_enrichment_json="{}",
            )
            storage.approve_receipt_extraction(
                user_id,
                approved_extraction_id,
                merchant="Sweetgreen",
                transaction_date="2026-04-23",
                total_amount=18.50,
                category="Dining",
            )

            with self.assertRaises(ValueError):
                storage.approve_receipt_extraction(
                    user_id,
                    approved_extraction_id,
                    merchant="Sweetgreen",
                    transaction_date="2026-04-23",
                    total_amount=18.50,
                    category="Dining",
                )

            with storage._connect() as conn:
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(
                        """
                        INSERT INTO receipt_transaction_links (receipt_extraction_id, transaction_id)
                        VALUES (?, ?)
                        """,
                        (approved_extraction_id, storage.get_receipt_transaction_link(approved_extraction_id)["transaction_id"]),
                    )

            discarded_upload_id = storage.create_receipt_upload(user_id, "receipt-5.jpg", "uploads/receipt-5.jpg")
            discarded_extraction_id = storage.save_receipt_extraction(
                user_id,
                receipt_upload_id=discarded_upload_id,
                merchant="Target",
                transaction_date="2026-04-24",
                total_amount=42.75,
                category="Groceries",
                category_confidence=0.88,
                status="ready",
                behavior_note="This fits your normal grocery pattern.",
                item_tags_json="[]",
                raw_extraction_json="{}",
                web_enrichment_json="{}",
            )
            storage.discard_receipt_extraction(user_id, discarded_extraction_id)

            with self.assertRaises(ValueError):
                storage.approve_receipt_extraction(
                    user_id,
                    discarded_extraction_id,
                    merchant="Target",
                    transaction_date="2026-04-24",
                    total_amount=42.75,
                    category="Groceries",
                )

            dashboard = storage.get_dashboard_data(user_id)
            self.assertEqual(dashboard["transaction_count"], 1)
            self.assertEqual(len(dashboard["all_transactions"]), 1)

    def test_storage_rejects_receipt_extraction_for_mismatched_upload_owner(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")

            owner_user_id = storage.create_user("owner@example.com", "secret123")
            other_user_id = storage.create_user("other@example.com", "secret123")
            receipt_id = storage.create_receipt_upload(owner_user_id, "receipt-6.jpg", "uploads/receipt-6.jpg")

            with self.assertRaises(ValueError):
                storage.save_receipt_extraction(
                    other_user_id,
                    receipt_upload_id=receipt_id,
                    merchant="Trader Joe's",
                    transaction_date="2026-04-23",
                    total_amount=48.22,
                    category="Groceries",
                    category_confidence=0.94,
                    status="ready",
                    behavior_note="This fits your normal grocery pattern.",
                    item_tags_json='["essential spending"]',
                    raw_extraction_json='{"total":"48.22"}',
                    web_enrichment_json='{"source":"none"}',
                )

            self.assertEqual(storage.list_pending_receipt_extractions(owner_user_id), [])
            self.assertEqual(storage.list_pending_receipt_extractions(other_user_id), [])

    def test_storage_repairs_duplicate_receipt_links_and_adds_unique_index(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/legacy.db"
            with sqlite3.connect(db_path) as conn:
                conn.executescript(
                    """
                    CREATE TABLE users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        email TEXT UNIQUE NOT NULL,
                        password_hash TEXT NOT NULL
                    );

                    CREATE TABLE transactions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        date TEXT NOT NULL,
                        description TEXT NOT NULL,
                        amount REAL NOT NULL,
                        category TEXT NOT NULL,
                        source TEXT NOT NULL,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    );

                    CREATE TABLE receipt_extractions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        receipt_upload_id INTEGER NOT NULL,
                        user_id INTEGER NOT NULL,
                        merchant TEXT NOT NULL DEFAULT '',
                        transaction_date TEXT NOT NULL DEFAULT '',
                        total_amount REAL NOT NULL DEFAULT 0,
                        category TEXT NOT NULL DEFAULT '',
                        category_confidence REAL NOT NULL DEFAULT 0,
                        status TEXT NOT NULL,
                        behavior_note TEXT NOT NULL DEFAULT '',
                        item_tags_json TEXT NOT NULL DEFAULT '[]',
                        raw_extraction_json TEXT NOT NULL DEFAULT '{}',
                        web_enrichment_json TEXT NOT NULL DEFAULT '{}',
                        reviewed_at TEXT,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    );

                    CREATE TABLE receipt_transaction_links (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        receipt_extraction_id INTEGER NOT NULL REFERENCES receipt_extractions(id) ON DELETE CASCADE,
                        transaction_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    );

                    INSERT INTO users (id, email, password_hash) VALUES (1, 'legacy@example.com', 'hash');
                    INSERT INTO transactions (id, user_id, date, description, amount, category, source)
                    VALUES (1, 1, '2026-04-23', 'Sweetgreen', 18.50, 'Dining', 'receipt');
                    INSERT INTO transactions (id, user_id, date, description, amount, category, source)
                    VALUES (2, 1, '2026-04-23', 'Sweetgreen', 18.50, 'Dining', 'receipt');
                    INSERT INTO receipt_extractions (id, receipt_upload_id, user_id, merchant, transaction_date, total_amount, category, category_confidence, status)
                    VALUES (1, 1, 1, 'Sweetgreen', '2026-04-23', 18.50, 'Dining', 0.91, 'approved');
                    INSERT INTO receipt_transaction_links (id, receipt_extraction_id, transaction_id)
                    VALUES (1, 1, 1);
                    INSERT INTO receipt_transaction_links (id, receipt_extraction_id, transaction_id)
                    VALUES (2, 1, 2);
                    """
                )

            Storage(db_path)

            with sqlite3.connect(db_path) as conn:
                link_count = conn.execute(
                    "SELECT COUNT(*) FROM receipt_transaction_links WHERE receipt_extraction_id = 1"
                ).fetchone()[0]
                receipt_transaction_count = conn.execute(
                    "SELECT COUNT(*) FROM transactions WHERE user_id = 1 AND source = 'receipt'"
                ).fetchone()[0]
                linked_transaction_id = conn.execute(
                    "SELECT transaction_id FROM receipt_transaction_links WHERE receipt_extraction_id = 1"
                ).fetchone()[0]
                indexes = conn.execute("PRAGMA index_list('receipt_transaction_links')").fetchall()

            self.assertEqual(link_count, 1)
            self.assertEqual(receipt_transaction_count, 1)
            self.assertEqual(linked_transaction_id, 1)
            self.assertTrue(
                any(
                    row[1] == "idx_receipt_transaction_links_receipt_extraction_id" and row[2] == 1
                    for row in indexes
                )
            )

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

    def test_storage_saves_user_decision_notes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")

            user_id = storage.create_user("michelle@example.com", "secret123")
            storage.save_user_decision(
                user_id,
                entry_type="decision",
                title="Workout swap",
                content="Switched from Club Pilates to yoga classes.",
            )

            decisions = storage.list_user_decisions(user_id)

            self.assertEqual(len(decisions), 1)
            self.assertEqual(decisions[0]["entry_type"], "decision")
            self.assertEqual(decisions[0]["title"], "Workout swap")
            self.assertIn("yoga classes", decisions[0]["content"])


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

    def test_chat_message_asks_for_where_and_when_if_manual_spend_lacks_details(self):
        coach = OverspendingCoach()

        result = coach.process_message(
            "I spent $40",
            profile={"subscriptions": [], "category_totals": {}, "transactions": [], "pending_action": None},
        )

        self.assertEqual(result["action"]["type"], "none")
        self.assertIn("where", result["reply"].lower())
        self.assertIn("when", result["reply"].lower())
        self.assertIn("how", result["reply"].lower())
        self.assertIn("already counted", result["reply"].lower())

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

    def test_chat_message_can_save_user_decision_note(self):
        coach = OverspendingCoach()

        result = coach.process_message(
            "Okay I'm switching my workout class out to yoga instead.",
            profile={"subscriptions": [{"merchant": "CLR*ClubPilate7187010242", "monthly_equivalent": 107.88}]},
        )

        self.assertEqual(result["action"]["type"], "save_user_decision")
        self.assertEqual(result["action"]["entry_type"], "decision")
        self.assertIn("Workout swap", result["action"]["title"])
        self.assertIn("yoga", result["action"]["content"].lower())


if __name__ == "__main__":
    unittest.main()
