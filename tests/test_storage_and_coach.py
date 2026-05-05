import hashlib
import tempfile
import unittest
import sqlite3
from unittest import mock

from coach import OverspendingCoach
from storage import Storage


class StorageTests(unittest.TestCase):
    @staticmethod
    def _set_password_reset_token_expires_at(storage: Storage, raw_token: str, expires_at: str) -> None:
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        with storage._connect() as conn:
            conn.execute(
                "UPDATE password_reset_tokens SET expires_at = ? WHERE token_hash = ?",
                (expires_at, token_hash),
            )

    def test_create_password_reset_token_returns_raw_token_for_existing_user(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")

            user_id = storage.create_user("demo@example.com", "secret123")

            payload = storage.create_password_reset_token("demo@example.com")

            self.assertEqual(payload["user_id"], user_id)
            self.assertEqual(payload["email"], "demo@example.com")
            self.assertTrue(payload["token"])
            self.assertEqual(payload["expires_in_minutes"], 30)

            with storage._connect() as conn:
                row = conn.execute(
                    "SELECT token_hash FROM password_reset_tokens WHERE user_id = ?",
                    (user_id,),
                ).fetchone()

            self.assertIsNotNone(row)
            self.assertNotEqual(row["token_hash"], payload["token"])

    def test_create_password_reset_token_is_neutral_for_missing_user(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")

            payload = storage.create_password_reset_token("missing@example.com")

            self.assertIsNone(payload)

    def test_get_password_reset_token_rejects_invalid_token(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")

            self.assertIsNone(storage.get_password_reset_token("not-a-real-token"))

    def test_reset_password_with_token_updates_password_and_consumes_token(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")

            storage.create_user("demo@example.com", "secret123")
            payload = storage.create_password_reset_token("demo@example.com")

            storage.reset_password_with_token(payload["token"], "newsecret456")

            self.assertEqual(storage.authenticate_user("demo@example.com", "newsecret456"), payload["user_id"])
            self.assertIsNone(storage.authenticate_user("demo@example.com", "secret123"))
            self.assertIsNone(storage.get_password_reset_token(payload["token"]))

    def test_reset_password_with_token_invalidates_older_tokens_for_same_user(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")

            storage.create_user("demo@example.com", "secret123")
            older = storage.create_password_reset_token("demo@example.com")
            newer = storage.create_password_reset_token("demo@example.com")

            storage.reset_password_with_token(newer["token"], "newsecret456")

            self.assertIsNone(storage.get_password_reset_token(older["token"]))
            self.assertIsNone(storage.get_password_reset_token(newer["token"]))

    def test_create_password_reset_token_revokes_existing_unused_tokens_for_same_user(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")

            storage.create_user("demo@example.com", "secret123")
            older = storage.create_password_reset_token("demo@example.com")
            newer = storage.create_password_reset_token("demo@example.com")

            self.assertIsNone(storage.get_password_reset_token(older["token"]))
            self.assertIsNotNone(storage.get_password_reset_token(newer["token"]))

    def test_reset_password_with_token_rejects_already_used_token(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")

            storage.create_user("demo@example.com", "secret123")
            payload = storage.create_password_reset_token("demo@example.com")
            with storage._connect() as conn:
                conn.execute(
                    "UPDATE password_reset_tokens SET used_at = CURRENT_TIMESTAMP WHERE token_hash = ?",
                    (hashlib.sha256(payload["token"].encode("utf-8")).hexdigest(),),
                )

            with self.assertRaises(ValueError):
                storage.reset_password_with_token(payload["token"], "newsecret456")

    def test_reset_password_with_token_rejects_expired_token(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")

            storage.create_user("demo@example.com", "secret123")
            payload = storage.create_password_reset_token("demo@example.com")
            self._set_password_reset_token_expires_at(
                storage,
                payload["token"],
                "2000-01-01T00:00:00+00:00",
            )

            with self.assertRaises(ValueError):
                storage.reset_password_with_token(payload["token"], "newsecret456")

    def test_get_password_reset_token_rejects_expired_token(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")

            storage.create_user("demo@example.com", "secret123")
            payload = storage.create_password_reset_token("demo@example.com")
            self._set_password_reset_token_expires_at(
                storage,
                payload["token"],
                "2000-01-01T00:00:00+00:00",
            )

            self.assertIsNone(storage.get_password_reset_token(payload["token"]))

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

    def test_storage_reuses_cached_merchant_category(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")

            storage.save_cached_merchant_category(
                "sweetgreen",
                category="Dining",
                confidence=0.93,
                enrichment_source="web",
            )

            cached = storage.get_cached_merchant_category("sweetgreen")

            self.assertIsNotNone(cached)
            self.assertEqual(cached["category"], "Dining")
            self.assertEqual(cached["enrichment_source"], "web")
            self.assertEqual(cached["confidence"], 0.93)

    def test_storage_promotes_receipt_behavior_note_into_top_insights(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")

            user_id = storage.create_user("demo@example.com", "secret123")
            storage.add_transactions(
                user_id,
                [
                    {
                        "date": "2026-03-10",
                        "description": "Sweetgreen",
                        "amount": 12.00,
                        "category": "Dining",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-10",
                        "description": "Sweetgreen",
                        "amount": 18.00,
                        "category": "Dining",
                        "source": "receipt",
                    }
                ],
            )
            storage.save_monthly_plan(
                user_id,
                month_key="2026-04",
                monthly_income=4200,
                fixed_expenses=1800,
                budgeting_goal="Save $1000 for vacation",
            )
            storage.save_monthly_summary(
                user_id,
                month_key="2026-04",
                income=4200,
                fixed_expenses=1800,
                tracked_spending=1800,
                recurring_monthly_total=0,
                leftover_money=300,
                discretionary_remaining=300,
                summary_text="April summary",
            )
            storage.save_receipt_behavior_insight(
                user_id,
                month_key="2026-04",
                note="This is your 5th dining expense this week.",
            )

            dashboard = storage.get_dashboard_data(user_id, "2026-04")

            self.assertEqual(len(dashboard["top_insights"]), 3)
            self.assertIn("This is your 5th dining expense this week.", dashboard["top_insights"])
            self.assertNotIn(
                "You still have $300.00 left in April 2026 after fixed expenses.",
                dashboard["top_insights"],
            )

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

    def test_storage_persists_structured_goal_fields_on_financial_profile(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")
            user_id = storage.create_user("michelle@example.com", "secret123")

            storage.upsert_financial_profile(
                user_id,
                monthly_income=4200,
                fixed_expenses=1800,
                budgeting_goal="",
                goal_name="Vacation fund",
                goal_target_amount=2000,
                goal_target_date="2026-09-01",
                current_saved_amount=550,
            )

            profile = storage.get_financial_profile(user_id)

            self.assertEqual(profile["goal_name"], "Vacation fund")
            self.assertEqual(profile["goal_target_amount"], 2000)
            self.assertEqual(profile["goal_target_date"], "2026-09-01")
            self.assertEqual(profile["current_saved_amount"], 550)
            self.assertEqual(
                profile["budgeting_goal"],
                "Vacation fund: $2,000 by 2026-09-01 (saved so far: $550)",
            )

    def test_storage_persists_structured_goal_fields_on_monthly_plan(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")
            user_id = storage.create_user("michelle@example.com", "secret123")

            storage.save_monthly_plan(
                user_id,
                month_key="2026-04",
                monthly_income=4200,
                fixed_expenses=1800,
                budgeting_goal="",
                goal_name="Emergency fund",
                goal_target_amount=5000,
                goal_target_date="2026-12-31",
                current_saved_amount=1200,
            )

            plan = storage.get_monthly_plan(user_id, "2026-04")
            plan_history = storage.list_monthly_plans(user_id)

            self.assertEqual(plan["goal_name"], "Emergency fund")
            self.assertEqual(plan["goal_target_amount"], 5000)
            self.assertEqual(plan["goal_target_date"], "2026-12-31")
            self.assertEqual(plan["current_saved_amount"], 1200)
            self.assertEqual(
                plan["budgeting_goal"],
                "Emergency fund: $5,000 by 2026-12-31 (saved so far: $1,200)",
            )
            self.assertEqual(plan_history[0]["goal_name"], "Emergency fund")
            self.assertEqual(plan_history[0]["budgeting_goal"], plan["budgeting_goal"])

    def test_storage_preserves_structured_goal_fields_on_financial_profile_update_without_new_goal_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")
            user_id = storage.create_user("michelle@example.com", "secret123")

            storage.upsert_financial_profile(
                user_id,
                monthly_income=4200,
                fixed_expenses=1800,
                budgeting_goal="Vacation fund",
                goal_name="Vacation fund",
                goal_target_amount=2000,
                goal_target_date="2026-09-01",
                current_saved_amount=550,
            )
            storage.upsert_financial_profile(
                user_id,
                monthly_income=4300,
                fixed_expenses=1900,
                budgeting_goal="Legacy old-style goal text",
            )

            profile = storage.get_financial_profile(user_id)

            self.assertEqual(profile["monthly_income"], 4300)
            self.assertEqual(profile["fixed_expenses"], 1900)
            self.assertEqual(profile["goal_name"], "Vacation fund")
            self.assertEqual(profile["goal_target_amount"], 2000)
            self.assertEqual(profile["goal_target_date"], "2026-09-01")
            self.assertEqual(profile["current_saved_amount"], 550)
            self.assertEqual(profile["budgeting_goal"], "Legacy old-style goal text")

    def test_storage_preserves_structured_goal_fields_on_monthly_plan_update_without_new_goal_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")
            user_id = storage.create_user("michelle@example.com", "secret123")

            storage.save_monthly_plan(
                user_id,
                month_key="2026-04",
                monthly_income=4200,
                fixed_expenses=1800,
                budgeting_goal="Emergency fund",
                goal_name="Emergency fund",
                goal_target_amount=5000,
                goal_target_date="2026-12-31",
                current_saved_amount=1200,
            )
            storage.save_monthly_plan(
                user_id,
                month_key="2026-04",
                monthly_income=4350,
                fixed_expenses=1850,
                budgeting_goal="Legacy old-style goal text",
            )

            plan = storage.get_monthly_plan(user_id, "2026-04")
            plan_history = storage.list_monthly_plans(user_id)

            self.assertEqual(plan["monthly_income"], 4350)
            self.assertEqual(plan["fixed_expenses"], 1850)
            self.assertEqual(plan["goal_name"], "Emergency fund")
            self.assertEqual(plan["goal_target_amount"], 5000)
            self.assertEqual(plan["goal_target_date"], "2026-12-31")
            self.assertEqual(plan["current_saved_amount"], 1200)
            self.assertEqual(plan["budgeting_goal"], "Legacy old-style goal text")
            self.assertEqual(plan_history[0]["goal_name"], "Emergency fund")
            self.assertEqual(plan_history[0]["budgeting_goal"], plan["budgeting_goal"])

    def test_storage_preserves_meaningful_budgeting_goal_when_structured_data_is_partial(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")
            user_id = storage.create_user("michelle@example.com", "secret123")

            storage.upsert_financial_profile(
                user_id,
                monthly_income=4200,
                fixed_expenses=1800,
                budgeting_goal="Save for spring trip",
                goal_name="Trip",
                current_saved_amount=300,
            )

            profile = storage.get_financial_profile(user_id)

            self.assertEqual(profile["goal_name"], "Trip")
            self.assertEqual(profile["goal_target_amount"], 0)
            self.assertEqual(profile["goal_target_date"], "")
            self.assertEqual(profile["current_saved_amount"], 300)
            self.assertEqual(profile["budgeting_goal"], "Save for spring trip")

    def test_storage_legacy_budgeting_goal_only_calls_replace_and_clear_text_without_touching_structured_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")
            user_id = storage.create_user("michelle@example.com", "secret123")

            storage.upsert_financial_profile(
                user_id,
                monthly_income=4200,
                fixed_expenses=1800,
                budgeting_goal="Vacation fund",
                goal_name="Vacation fund",
                goal_target_amount=2000,
                goal_target_date="2026-09-01",
                current_saved_amount=550,
            )
            storage.upsert_financial_profile(
                user_id,
                monthly_income=4300,
                fixed_expenses=1900,
                budgeting_goal="Holiday trip",
            )
            profile = storage.get_financial_profile(user_id)

            self.assertEqual(profile["budgeting_goal"], "Holiday trip")
            self.assertEqual(profile["goal_name"], "Vacation fund")
            self.assertEqual(profile["goal_target_amount"], 2000)
            self.assertEqual(profile["goal_target_date"], "2026-09-01")
            self.assertEqual(profile["current_saved_amount"], 550)

            storage.upsert_financial_profile(
                user_id,
                monthly_income=4300,
                fixed_expenses=1900,
                budgeting_goal="",
            )
            cleared_profile = storage.get_financial_profile(user_id)

            self.assertEqual(cleared_profile["budgeting_goal"], "")
            self.assertEqual(cleared_profile["goal_name"], "Vacation fund")
            self.assertEqual(cleared_profile["goal_target_amount"], 2000)
            self.assertEqual(cleared_profile["goal_target_date"], "2026-09-01")
            self.assertEqual(cleared_profile["current_saved_amount"], 550)

            storage.save_monthly_plan(
                user_id,
                month_key="2026-04",
                monthly_income=4200,
                fixed_expenses=1800,
                budgeting_goal="Emergency fund",
                goal_name="Emergency fund",
                goal_target_amount=5000,
                goal_target_date="2026-12-31",
                current_saved_amount=1200,
            )
            storage.save_monthly_plan(
                user_id,
                month_key="2026-04",
                monthly_income=4350,
                fixed_expenses=1850,
                budgeting_goal="Cut dining for now",
            )
            plan = storage.get_monthly_plan(user_id, "2026-04")

            self.assertEqual(plan["budgeting_goal"], "Cut dining for now")
            self.assertEqual(plan["goal_name"], "Emergency fund")
            self.assertEqual(plan["goal_target_amount"], 5000)
            self.assertEqual(plan["goal_target_date"], "2026-12-31")
            self.assertEqual(plan["current_saved_amount"], 1200)

            storage.save_monthly_plan(
                user_id,
                month_key="2026-04",
                monthly_income=4350,
                fixed_expenses=1850,
                budgeting_goal="",
            )
            cleared_plan = storage.get_monthly_plan(user_id, "2026-04")

            self.assertEqual(cleared_plan["budgeting_goal"], "")
            self.assertEqual(cleared_plan["goal_name"], "Emergency fund")
            self.assertEqual(cleared_plan["goal_target_amount"], 5000)
            self.assertEqual(cleared_plan["goal_target_date"], "2026-12-31")
            self.assertEqual(cleared_plan["current_saved_amount"], 1200)

    def test_storage_reopens_legacy_database_and_adds_goal_columns_safely(self):
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

                    CREATE TABLE financial_profiles (
                        user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                        monthly_income REAL NOT NULL DEFAULT 0,
                        fixed_expenses REAL NOT NULL DEFAULT 0,
                        budgeting_goal TEXT NOT NULL DEFAULT '',
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                    );

                    CREATE TABLE monthly_plans (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        month_key TEXT NOT NULL,
                        monthly_income REAL NOT NULL DEFAULT 0,
                        fixed_expenses REAL NOT NULL DEFAULT 0,
                        budgeting_goal TEXT NOT NULL DEFAULT '',
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(user_id, month_key)
                    );

                    INSERT INTO users (id, email, password_hash) VALUES (1, 'legacy@example.com', 'hash');
                    INSERT INTO financial_profiles (user_id, monthly_income, fixed_expenses, budgeting_goal)
                    VALUES (1, 4200, 1800, 'Save for trip');
                    INSERT INTO monthly_plans (user_id, month_key, monthly_income, fixed_expenses, budgeting_goal)
                    VALUES (1, '2026-04', 4200, 1800, 'Save for car');
                    """
                )

            storage = Storage(db_path)

            with storage._connect() as conn:
                financial_columns = {row["name"] for row in conn.execute("PRAGMA table_info(financial_profiles)").fetchall()}
                monthly_columns = {row["name"] for row in conn.execute("PRAGMA table_info(monthly_plans)").fetchall()}

            profile = storage.get_financial_profile(1)
            plan = storage.get_monthly_plan(1, "2026-04")

            self.assertTrue({"goal_name", "goal_target_amount", "goal_target_date", "current_saved_amount"}.issubset(financial_columns))
            self.assertTrue({"goal_name", "goal_target_amount", "goal_target_date", "current_saved_amount"}.issubset(monthly_columns))
            self.assertEqual(profile["budgeting_goal"], "Save for trip")
            self.assertEqual(profile["goal_name"], "")
            self.assertEqual(profile["goal_target_amount"], 0)
            self.assertEqual(profile["goal_target_date"], "")
            self.assertEqual(profile["current_saved_amount"], 0)
            self.assertEqual(plan["budgeting_goal"], "Save for car")
            self.assertEqual(plan["goal_name"], "")
            self.assertEqual(plan["goal_target_amount"], 0)
            self.assertEqual(plan["goal_target_date"], "")
            self.assertEqual(plan["current_saved_amount"], 0)

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

    def test_dashboard_assigns_goal_focused_but_behind_before_reactive(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")
            user_id = storage.create_user("person@example.com", "secret123")
            storage.add_transactions(
                user_id,
                [
                    {
                        "date": "2026-04-04",
                        "description": "Restaurant Row",
                        "amount": 420.0,
                        "category": "Dining",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-08",
                        "description": "Shopping Run",
                        "amount": 250.0,
                        "category": "Shopping",
                        "source": "statement",
                    },
                ],
            )
            storage.upsert_financial_profile(
                user_id,
                monthly_income=3000,
                fixed_expenses=2600,
                budgeting_goal="",
                goal_name="Japan trip",
                goal_target_amount=2000,
                goal_target_date="2026-06-01",
                current_saved_amount=200,
            )
            storage.save_monthly_summary(
                user_id,
                month_key="2026-04",
                income=3000,
                fixed_expenses=2600,
                tracked_spending=670,
                recurring_monthly_total=0,
                leftover_money=-270,
                discretionary_remaining=-270,
                summary_text="",
            )

            dashboard = storage.get_dashboard_data(user_id, "2026-04")

            self.assertEqual(dashboard["spending_profile"]["name"], "Goal-Focused but Behind")
            self.assertEqual(
                dashboard["spending_profile"]["description"],
                "You have a clear goal, but your current spending pace may delay your progress.",
            )
            self.assertGreaterEqual(len(dashboard["spending_profile"]["reasons"]), 2)
            self.assertIn("Japan trip", dashboard["goal_summary"])

    def test_dashboard_assigns_budget_optimizer_when_discretionary_spend_is_near_cap(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")
            user_id = storage.create_user("person@example.com", "secret123")
            storage.add_transactions(
                user_id,
                [
                    {
                        "date": "2026-04-04",
                        "description": "Restaurant Row",
                        "amount": 500.0,
                        "category": "Dining",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-01",
                        "description": "Spotify",
                        "amount": 70.0,
                        "category": "Subscriptions",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-30",
                        "description": "Spotify",
                        "amount": 70.0,
                        "category": "Subscriptions",
                        "source": "statement",
                    },
                ],
            )
            storage.upsert_financial_profile(
                user_id,
                monthly_income=4000,
                fixed_expenses=2200,
                budgeting_goal="",
                goal_name="",
                goal_target_amount=0,
                goal_target_date="",
                current_saved_amount=0,
            )
            storage.save_monthly_summary(
                user_id,
                month_key="2026-04",
                income=4000,
                fixed_expenses=2200,
                tracked_spending=640.0,
                recurring_monthly_total=70.0,
                leftover_money=1160.0,
                discretionary_remaining=1160.0,
                summary_text="",
            )

            dashboard = storage.get_dashboard_data(user_id, "2026-04")

            self.assertEqual(dashboard["spending_profile"]["name"], "Budget Optimizer")
            self.assertEqual(
                dashboard["spending_profile"]["description"],
                "You are generally staying within budget and may benefit most from small optimizations.",
            )
            self.assertEqual(
                dashboard["spending_profile"]["reasons"][0],
                "Your top discretionary category is Dining at 104% of its own cap.",
            )
            self.assertIn("recurring subscriptions totaling", dashboard["spending_profile"]["reasons"][1])
            self.assertEqual(
                dashboard["spending_profile"]["why_this"],
                "Based on your current month transactions and the discretionary cap model.",
            )
            self.assertEqual(dashboard["goal_summary"], "")

    def test_dashboard_builds_three_scenario_cards_with_goal_impact(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")
            user_id = storage.create_user("person@example.com", "secret123")
            storage.add_transactions(
                user_id,
                [
                    {
                        "date": "2026-03-28",
                        "description": "Old Month Dining",
                        "amount": 310.0,
                        "category": "Dining",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-04",
                        "description": "Dining Out",
                        "amount": 420.0,
                        "category": "Dining",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-08",
                        "description": "Shopping Run",
                        "amount": 260.0,
                        "category": "Shopping",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-09",
                        "description": "Netflix",
                        "amount": 15.99,
                        "category": "Subscriptions",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-10",
                        "description": "Spotify",
                        "amount": 9.99,
                        "category": "Subscriptions",
                        "source": "statement",
                    },
                ],
            )
            storage.upsert_financial_profile(
                user_id,
                monthly_income=4000,
                fixed_expenses=2200,
                budgeting_goal="",
                goal_name="Japan trip",
                goal_target_amount=2000,
                goal_target_date="2026-08-01",
                current_saved_amount=300,
            )
            storage.save_monthly_summary(
                user_id,
                month_key="2026-04",
                income=4000,
                fixed_expenses=2200,
                tracked_spending=705.98,
                recurring_monthly_total=25.98,
                leftover_money=1094.02,
                discretionary_remaining=1094.02,
                summary_text="",
            )

            dashboard = storage.get_dashboard_data(user_id, "2026-04")

            scenarios = dashboard["scenario_analysis"]
            self.assertEqual(len(scenarios), 3)
            self.assertEqual(
                [item["title"] for item in scenarios],
                [
                    "Stay on Current Path",
                    "Moderate Adjustment",
                    "Aggressive Savings",
                ],
            )
            self.assertEqual(
                [item["scenario_key"] for item in scenarios],
                [
                    "stay_on_current_path",
                    "moderate_adjustment",
                    "aggressive_savings",
                ],
            )

            expected_fields = {
                "title",
                "savings_impact_monthly",
                "actions",
                "goal_impact",
                "tradeoff",
                "why_this",
                "chat_prompt",
                "cta_label",
                "scenario_key",
            }
            for scenario in scenarios:
                self.assertTrue(expected_fields.issubset(scenario.keys()))
                self.assertGreaterEqual(len(scenario["actions"]), 2)
                self.assertLessEqual(len(scenario["actions"]), 3)
                self.assertEqual(scenario["cta_label"], "Ask AI to build this plan")
                self.assertEqual(
                    scenario["why_this"],
                    "Based on your uploaded transactions, budget caps, recurring subscriptions, and savings goal.",
                )

            self.assertEqual(scenarios[0]["savings_impact_monthly"], 0)
            self.assertTrue(any("current path" in action.lower() for action in scenarios[0]["actions"]))
            self.assertIn("may", scenarios[0]["goal_impact"].lower())

            self.assertGreater(scenarios[1]["savings_impact_monthly"], 0)
            self.assertTrue(any("dining" in action.lower() for action in scenarios[1]["actions"]))
            self.assertTrue(any("subscription" in action.lower() for action in scenarios[1]["actions"]))
            self.assertIn("ask ai", scenarios[1]["cta_label"].lower())
            self.assertIn("moderate adjustment plan", scenarios[1]["chat_prompt"].lower())
            self.assertIn("may", scenarios[1]["tradeoff"].lower())

            self.assertGreater(scenarios[2]["savings_impact_monthly"], scenarios[1]["savings_impact_monthly"])
            self.assertTrue(any("shopping" in action.lower() for action in scenarios[2]["actions"]))
            self.assertTrue(any("subscription" in action.lower() for action in scenarios[2]["actions"]))

    def test_dashboard_scenarios_fallback_when_not_enough_data(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")
            user_id = storage.create_user("person@example.com", "secret123")
            storage.upsert_financial_profile(
                user_id,
                monthly_income=0,
                fixed_expenses=0,
                budgeting_goal="",
                goal_name="",
                goal_target_amount=0,
                goal_target_date="",
                current_saved_amount=0,
            )

            dashboard = storage.get_dashboard_data(user_id, None)

            scenarios = dashboard["scenario_analysis"]
            self.assertEqual(len(scenarios), 3)
            self.assertEqual(
                [item["title"] for item in scenarios],
                [
                    "Stay on Current Path",
                    "Moderate Adjustment",
                    "Aggressive Savings",
                ],
            )
            for scenario in scenarios:
                self.assertEqual(scenario["actions"], [])
                self.assertEqual(scenario["goal_impact"], "")
                self.assertEqual(scenario["tradeoff"], "")
                self.assertEqual(scenario["cta_label"], "Ask AI to build this plan")
                self.assertEqual(scenario["why_this"], "")

    def test_dashboard_scenarios_fallback_for_profile_only_user_without_month_evidence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")
            user_id = storage.create_user("person@example.com", "secret123")
            storage.upsert_financial_profile(
                user_id,
                monthly_income=4500,
                fixed_expenses=2300,
                budgeting_goal="",
                goal_name="Emergency fund",
                goal_target_amount=5000,
                goal_target_date="2026-12-31",
                current_saved_amount=800,
            )

            dashboard = storage.get_dashboard_data(user_id, None)

            scenarios = dashboard["scenario_analysis"]
            self.assertEqual(len(scenarios), 3)
            for scenario in scenarios:
                self.assertEqual(scenario["actions"], [])
                self.assertEqual(scenario["goal_impact"], "")
                self.assertEqual(scenario["tradeoff"], "")
                self.assertEqual(scenario["cta_label"], "Ask AI to build this plan")
                self.assertEqual(scenario["why_this"], "")

    def test_dashboard_scenarios_use_resolved_selected_month_when_month_key_is_omitted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")
            user_id = storage.create_user("person@example.com", "secret123")
            storage.add_transactions(
                user_id,
                [
                    {
                        "date": "2026-03-05",
                        "description": "Dining Out",
                        "amount": 180.0,
                        "category": "Dining",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-06",
                        "description": "Shopping Run",
                        "amount": 210.0,
                        "category": "Shopping",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-10",
                        "description": "Netflix",
                        "amount": 18.99,
                        "category": "Subscriptions",
                        "source": "statement",
                    },
                ],
            )
            storage.upsert_financial_profile(
                user_id,
                monthly_income=4200,
                fixed_expenses=2200,
                budgeting_goal="",
                goal_name="Trip fund",
                goal_target_amount=1500,
                goal_target_date="2026-10-01",
                current_saved_amount=300,
            )
            storage.save_monthly_summary(
                user_id,
                month_key="2026-04",
                income=4200,
                fixed_expenses=2200,
                tracked_spending=228.99,
                recurring_monthly_total=18.99,
                leftover_money=1771.01,
                discretionary_remaining=1771.01,
                summary_text="",
            )

            dashboard = storage.get_dashboard_data(user_id, None)

            scenarios = dashboard["scenario_analysis"]
            self.assertEqual(len(scenarios), 3)
            self.assertEqual(dashboard["selected_month"], "2026-04")
            for scenario in scenarios:
                self.assertNotIn("Upload more transaction history", scenario["tradeoff"])
                self.assertEqual(
                    scenario["why_this"],
                    "Based on your uploaded transactions, budget caps, recurring subscriptions, and savings goal.",
                )
            self.assertTrue(any("shopping" in action.lower() for action in scenarios[1]["actions"]))

    def test_dashboard_scenarios_fallback_when_selected_month_has_no_discretionary_or_recurring_evidence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")
            user_id = storage.create_user("person@example.com", "secret123")
            storage.add_transactions(
                user_id,
                [
                    {
                        "date": "2026-03-08",
                        "description": "Dining Out",
                        "amount": 160.0,
                        "category": "Dining",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-04",
                        "description": "Grocery Run",
                        "amount": 120.0,
                        "category": "Groceries",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-10",
                        "description": "Pharmacy",
                        "amount": 35.0,
                        "category": "Health",
                        "source": "statement",
                    },
                ],
            )
            storage.upsert_financial_profile(
                user_id,
                monthly_income=3500,
                fixed_expenses=2000,
                budgeting_goal="",
                goal_name="Emergency fund",
                goal_target_amount=2500,
                goal_target_date="2026-12-31",
                current_saved_amount=500,
            )
            storage.save_monthly_summary(
                user_id,
                month_key="2026-04",
                income=3500,
                fixed_expenses=2000,
                tracked_spending=155.0,
                recurring_monthly_total=0,
                leftover_money=1345.0,
                discretionary_remaining=1345.0,
                summary_text="",
            )

            dashboard = storage.get_dashboard_data(user_id, "2026-04")

            scenarios = dashboard["scenario_analysis"]
            self.assertEqual(len(scenarios), 3)
            for scenario in scenarios:
                self.assertEqual(scenario["actions"], [])
                self.assertEqual(scenario["goal_impact"], "")
                self.assertEqual(scenario["tradeoff"], "")
                self.assertEqual(scenario["why_this"], "")

    def test_dashboard_scenarios_fallback_when_selected_month_has_only_one_small_discretionary_transaction(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")
            user_id = storage.create_user("person@example.com", "secret123")
            storage.add_transactions(
                user_id,
                [
                    {
                        "date": "2026-03-06",
                        "description": "Dining Out",
                        "amount": 180.0,
                        "category": "Dining",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-05",
                        "description": "Coffee Shop",
                        "amount": 6.5,
                        "category": "Coffee",
                        "source": "statement",
                    },
                ],
            )
            storage.upsert_financial_profile(
                user_id,
                monthly_income=3600,
                fixed_expenses=2100,
                budgeting_goal="",
                goal_name="Buffer",
                goal_target_amount=1200,
                goal_target_date="2026-10-01",
                current_saved_amount=300,
            )
            storage.save_monthly_summary(
                user_id,
                month_key="2026-04",
                income=3600,
                fixed_expenses=2100,
                tracked_spending=6.5,
                recurring_monthly_total=0,
                leftover_money=1493.5,
                discretionary_remaining=1493.5,
                summary_text="",
            )

            dashboard = storage.get_dashboard_data(user_id, "2026-04")

            scenarios = dashboard["scenario_analysis"]
            self.assertEqual(len(scenarios), 3)
            for scenario in scenarios:
                self.assertEqual(scenario["actions"], [])
                self.assertEqual(scenario["goal_impact"], "")
                self.assertEqual(scenario["tradeoff"], "")
                self.assertEqual(scenario["why_this"], "")

    def test_dashboard_scenarios_do_not_double_count_subscriptions_category_cuts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")
            user_id = storage.create_user("person@example.com", "secret123")
            storage.add_transactions(
                user_id,
                [
                    {
                        "date": "2026-03-05",
                        "description": "Netflix",
                        "amount": 18.99,
                        "category": "Subscriptions",
                        "source": "statement",
                    },
                    {
                        "date": "2026-03-06",
                        "description": "Spotify",
                        "amount": 11.99,
                        "category": "Subscriptions",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-05",
                        "description": "Netflix",
                        "amount": 18.99,
                        "category": "Subscriptions",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-06",
                        "description": "Spotify",
                        "amount": 11.99,
                        "category": "Subscriptions",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-10",
                        "description": "Takeout",
                        "amount": 24.0,
                        "category": "Dining",
                        "source": "statement",
                    },
                ],
            )
            storage.upsert_financial_profile(
                user_id,
                monthly_income=3200,
                fixed_expenses=1900,
                budgeting_goal="",
                goal_name="Buffer",
                goal_target_amount=1000,
                goal_target_date="2026-09-01",
                current_saved_amount=200,
            )
            storage.save_monthly_summary(
                user_id,
                month_key="2026-04",
                income=3200,
                fixed_expenses=1900,
                tracked_spending=54.98,
                recurring_monthly_total=30.98,
                leftover_money=1245.02,
                discretionary_remaining=1245.02,
                summary_text="",
            )

            dashboard = storage.get_dashboard_data(user_id, "2026-04")

            moderate = dashboard["scenario_analysis"][1]
            aggressive = dashboard["scenario_analysis"][2]

            self.assertFalse(any("reduce subscriptions" in action.lower() for action in moderate["actions"]))
            self.assertFalse(any("reduce subscriptions" in action.lower() for action in aggressive["actions"]))
            self.assertLessEqual(moderate["savings_impact_monthly"], 30.98)
            self.assertLessEqual(aggressive["savings_impact_monthly"], 30.98 + round(24.0 * 0.35, 2))

    def test_dashboard_aggressive_savings_keeps_second_discretionary_category_when_only_one_is_overspent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")
            user_id = storage.create_user("person@example.com", "secret123")
            storage.add_transactions(
                user_id,
                [
                    {
                        "date": "2026-03-05",
                        "description": "Netflix",
                        "amount": 15.99,
                        "category": "Subscriptions",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-04",
                        "description": "Dining Out",
                        "amount": 700.0,
                        "category": "Dining",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-08",
                        "description": "Shopping Run",
                        "amount": 180.0,
                        "category": "Shopping",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-10",
                        "description": "Netflix",
                        "amount": 15.99,
                        "category": "Subscriptions",
                        "source": "statement",
                    },
                ],
            )
            storage.upsert_financial_profile(
                user_id,
                monthly_income=4000,
                fixed_expenses=2200,
                budgeting_goal="",
                goal_name="Trip fund",
                goal_target_amount=1800,
                goal_target_date="2026-11-01",
                current_saved_amount=250,
            )
            storage.save_monthly_summary(
                user_id,
                month_key="2026-04",
                income=4000,
                fixed_expenses=2200,
                tracked_spending=895.99,
                recurring_monthly_total=15.99,
                leftover_money=904.01,
                discretionary_remaining=904.01,
                summary_text="",
            )

            dashboard = storage.get_dashboard_data(user_id, "2026-04")

            aggressive = dashboard["scenario_analysis"][2]
            self.assertTrue(any("dining" in action.lower() for action in aggressive["actions"]))
            self.assertTrue(any("shopping" in action.lower() for action in aggressive["actions"]))

    def test_dashboard_scenarios_ignore_prior_month_only_recurring_evidence_for_selected_month(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")
            user_id = storage.create_user("person@example.com", "secret123")
            storage.add_transactions(
                user_id,
                [
                    {
                        "date": "2026-01-05",
                        "description": "Netflix",
                        "amount": 15.99,
                        "category": "Subscriptions",
                        "source": "statement",
                    },
                    {
                        "date": "2026-02-05",
                        "description": "Netflix",
                        "amount": 15.99,
                        "category": "Subscriptions",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-05",
                        "description": "Coffee Shop",
                        "amount": 7.25,
                        "category": "Coffee",
                        "source": "statement",
                    },
                ],
            )
            storage.upsert_financial_profile(
                user_id,
                monthly_income=3800,
                fixed_expenses=2200,
                budgeting_goal="",
                goal_name="Buffer",
                goal_target_amount=1000,
                goal_target_date="2026-10-01",
                current_saved_amount=250,
            )
            storage.save_monthly_summary(
                user_id,
                month_key="2026-04",
                income=3800,
                fixed_expenses=2200,
                tracked_spending=7.25,
                recurring_monthly_total=0,
                leftover_money=1592.75,
                discretionary_remaining=1592.75,
                summary_text="",
            )

            dashboard = storage.get_dashboard_data(user_id, "2026-04")

            scenarios = dashboard["scenario_analysis"]
            self.assertEqual(len(scenarios), 3)
            for scenario in scenarios:
                self.assertEqual(scenario["actions"], [])
                self.assertEqual(scenario["goal_impact"], "")
                self.assertEqual(scenario["tradeoff"], "")

    def test_dashboard_scenarios_do_not_recommend_fixed_bills_as_subscription_cuts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")
            user_id = storage.create_user("person@example.com", "secret123")
            storage.add_transactions(
                user_id,
                [
                    {
                        "date": "2026-03-02",
                        "description": "Verizon Wireless",
                        "amount": 85.0,
                        "category": "Phone",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-02",
                        "description": "Verizon Wireless",
                        "amount": 85.0,
                        "category": "Phone",
                        "source": "statement",
                    },
                    {
                        "date": "2026-03-07",
                        "description": "Spotify",
                        "amount": 11.99,
                        "category": "Subscriptions",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-07",
                        "description": "Spotify",
                        "amount": 11.99,
                        "category": "Subscriptions",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-10",
                        "description": "Dining Out",
                        "amount": 140.0,
                        "category": "Dining",
                        "source": "statement",
                    },
                ],
            )
            storage.upsert_financial_profile(
                user_id,
                monthly_income=4100,
                fixed_expenses=2300,
                budgeting_goal="",
                goal_name="Trip",
                goal_target_amount=1800,
                goal_target_date="2026-11-01",
                current_saved_amount=350,
            )
            storage.save_monthly_summary(
                user_id,
                month_key="2026-04",
                income=4100,
                fixed_expenses=2300,
                tracked_spending=236.99,
                recurring_monthly_total=96.99,
                leftover_money=1563.01,
                discretionary_remaining=1563.01,
                summary_text="",
            )

            dashboard = storage.get_dashboard_data(user_id, "2026-04")

            moderate = dashboard["scenario_analysis"][1]
            aggressive = dashboard["scenario_analysis"][2]
            self.assertFalse(any("verizon" in action.lower() for action in moderate["actions"]))
            self.assertFalse(any("wireless" in action.lower() for action in moderate["actions"]))
            self.assertFalse(any("verizon" in action.lower() for action in aggressive["actions"]))
            self.assertFalse(any("wireless" in action.lower() for action in aggressive["actions"]))
            self.assertTrue(any("spotify" in action.lower() for action in moderate["actions"] + aggressive["actions"]))

    def test_dashboard_scenarios_do_not_recommend_phone_category_bill_even_with_neutral_merchant_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")
            user_id = storage.create_user("person@example.com", "secret123")
            storage.add_transactions(
                user_id,
                [
                    {
                        "date": "2026-03-03",
                        "description": "AT&T Mobility",
                        "amount": 95.0,
                        "category": "Phone",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-03",
                        "description": "AT&T Mobility",
                        "amount": 95.0,
                        "category": "Phone",
                        "source": "statement",
                    },
                    {
                        "date": "2026-03-07",
                        "description": "Spotify",
                        "amount": 11.99,
                        "category": "Subscriptions",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-07",
                        "description": "Spotify",
                        "amount": 11.99,
                        "category": "Subscriptions",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-10",
                        "description": "Dining Out",
                        "amount": 140.0,
                        "category": "Dining",
                        "source": "statement",
                    },
                ],
            )
            storage.upsert_financial_profile(
                user_id,
                monthly_income=4100,
                fixed_expenses=2300,
                budgeting_goal="",
                goal_name="Trip",
                goal_target_amount=1800,
                goal_target_date="2026-11-01",
                current_saved_amount=350,
            )
            storage.save_monthly_summary(
                user_id,
                month_key="2026-04",
                income=4100,
                fixed_expenses=2300,
                tracked_spending=246.99,
                recurring_monthly_total=106.99,
                leftover_money=1553.01,
                discretionary_remaining=1553.01,
                summary_text="",
            )

            dashboard = storage.get_dashboard_data(user_id, "2026-04")

            moderate = dashboard["scenario_analysis"][1]
            aggressive = dashboard["scenario_analysis"][2]
            self.assertFalse(any("mobility" in action.lower() for action in moderate["actions"] + aggressive["actions"]))
            self.assertFalse(any("at&t" in action.lower() for action in moderate["actions"] + aggressive["actions"]))
            self.assertTrue(any("spotify" in action.lower() for action in moderate["actions"] + aggressive["actions"]))

    def test_dashboard_scenarios_can_use_recurring_health_membership(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")
            user_id = storage.create_user("person@example.com", "secret123")
            storage.add_transactions(
                user_id,
                [
                    {
                        "date": "2026-03-06",
                        "description": "Club Pilates",
                        "amount": 89.0,
                        "category": "Health",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-06",
                        "description": "Club Pilates",
                        "amount": 89.0,
                        "category": "Health",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-10",
                        "description": "Dining Out",
                        "amount": 180.0,
                        "category": "Dining",
                        "source": "statement",
                    },
                ],
            )
            storage.upsert_financial_profile(
                user_id,
                monthly_income=3900,
                fixed_expenses=2200,
                budgeting_goal="",
                goal_name="Trip",
                goal_target_amount=1800,
                goal_target_date="2026-11-01",
                current_saved_amount=300,
            )
            storage.save_monthly_summary(
                user_id,
                month_key="2026-04",
                income=3900,
                fixed_expenses=2200,
                tracked_spending=269.0,
                recurring_monthly_total=89.0,
                leftover_money=1431.0,
                discretionary_remaining=1431.0,
                summary_text="",
            )

            dashboard = storage.get_dashboard_data(user_id, "2026-04")

            moderate = dashboard["scenario_analysis"][1]
            aggressive = dashboard["scenario_analysis"][2]
            self.assertTrue(any("pilates" in action.lower() for action in moderate["actions"] + aggressive["actions"]))

    def test_dashboard_scenarios_can_use_fitness_membership_with_blocked_substring_in_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")
            user_id = storage.create_user("person@example.com", "secret123")
            storage.add_transactions(
                user_id,
                [
                    {
                        "date": "2026-03-06",
                        "description": "Las Vegas Athletic Club",
                        "amount": 79.0,
                        "category": "Fitness",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-06",
                        "description": "Las Vegas Athletic Club",
                        "amount": 79.0,
                        "category": "Fitness",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-10",
                        "description": "Dining Out",
                        "amount": 160.0,
                        "category": "Dining",
                        "source": "statement",
                    },
                ],
            )
            storage.upsert_financial_profile(
                user_id,
                monthly_income=3900,
                fixed_expenses=2200,
                budgeting_goal="",
                goal_name="Trip",
                goal_target_amount=1800,
                goal_target_date="2026-11-01",
                current_saved_amount=300,
            )
            storage.save_monthly_summary(
                user_id,
                month_key="2026-04",
                income=3900,
                fixed_expenses=2200,
                tracked_spending=239.0,
                recurring_monthly_total=79.0,
                leftover_money=1461.0,
                discretionary_remaining=1461.0,
                summary_text="",
            )

            dashboard = storage.get_dashboard_data(user_id, "2026-04")

            moderate = dashboard["scenario_analysis"][1]
            aggressive = dashboard["scenario_analysis"][2]
            self.assertTrue(
                any("las vegas athletic club" in action.lower() for action in moderate["actions"] + aggressive["actions"])
            )

    def test_dashboard_scenarios_can_use_active_recurring_subscription_before_current_month_charge_posts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")
            user_id = storage.create_user("person@example.com", "secret123")
            storage.add_transactions(
                user_id,
                [
                    {
                        "date": "2026-02-05",
                        "description": "Netflix",
                        "amount": 15.99,
                        "category": "Subscriptions",
                        "source": "statement",
                    },
                    {
                        "date": "2026-03-05",
                        "description": "Netflix",
                        "amount": 15.99,
                        "category": "Subscriptions",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-08",
                        "description": "Dining Out",
                        "amount": 210.0,
                        "category": "Dining",
                        "source": "statement",
                    },
                ],
            )
            storage.upsert_financial_profile(
                user_id,
                monthly_income=3900,
                fixed_expenses=2200,
                budgeting_goal="",
                goal_name="Trip",
                goal_target_amount=1800,
                goal_target_date="2026-11-01",
                current_saved_amount=300,
            )
            storage.save_monthly_summary(
                user_id,
                month_key="2026-04",
                income=3900,
                fixed_expenses=2200,
                tracked_spending=210.0,
                recurring_monthly_total=15.99,
                leftover_money=1490.0,
                discretionary_remaining=1490.0,
                summary_text="",
            )

            dashboard = storage.get_dashboard_data(user_id, "2026-04")

            moderate = dashboard["scenario_analysis"][1]
            aggressive = dashboard["scenario_analysis"][2]
            self.assertTrue(any("netflix" in action.lower() for action in moderate["actions"] + aggressive["actions"]))

    def test_dashboard_scenarios_do_not_treat_recurring_pharmacy_as_discretionary_subscription_cut(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")
            user_id = storage.create_user("person@example.com", "secret123")
            storage.add_transactions(
                user_id,
                [
                    {
                        "date": "2026-03-06",
                        "description": "CVS Pharmacy",
                        "amount": 42.0,
                        "category": "Health",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-06",
                        "description": "CVS Pharmacy",
                        "amount": 42.0,
                        "category": "Health",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-10",
                        "description": "Dining Out",
                        "amount": 180.0,
                        "category": "Dining",
                        "source": "statement",
                    },
                ],
            )
            storage.upsert_financial_profile(
                user_id,
                monthly_income=3900,
                fixed_expenses=2200,
                budgeting_goal="",
                goal_name="Trip",
                goal_target_amount=1800,
                goal_target_date="2026-11-01",
                current_saved_amount=300,
            )
            storage.save_monthly_summary(
                user_id,
                month_key="2026-04",
                income=3900,
                fixed_expenses=2200,
                tracked_spending=222.0,
                recurring_monthly_total=42.0,
                leftover_money=1478.0,
                discretionary_remaining=1478.0,
                summary_text="",
            )

            dashboard = storage.get_dashboard_data(user_id, "2026-04")

            moderate = dashboard["scenario_analysis"][1]
            aggressive = dashboard["scenario_analysis"][2]
            self.assertFalse(any("cvs" in action.lower() for action in moderate["actions"] + aggressive["actions"]))
            self.assertFalse(any("pharmacy" in action.lower() for action in moderate["actions"] + aggressive["actions"]))

    def test_dashboard_scenarios_recurring_only_month_avoids_generic_category_trim_actions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")
            user_id = storage.create_user("person@example.com", "secret123")
            storage.add_transactions(
                user_id,
                [
                    {
                        "date": "2026-03-05",
                        "description": "Netflix",
                        "amount": 15.99,
                        "category": "Subscriptions",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-05",
                        "description": "Netflix",
                        "amount": 15.99,
                        "category": "Subscriptions",
                        "source": "statement",
                    },
                ],
            )
            storage.upsert_financial_profile(
                user_id,
                monthly_income=3200,
                fixed_expenses=1900,
                budgeting_goal="",
                goal_name="Buffer",
                goal_target_amount=1000,
                goal_target_date="2026-09-01",
                current_saved_amount=200,
            )
            storage.save_monthly_summary(
                user_id,
                month_key="2026-04",
                income=3200,
                fixed_expenses=1900,
                tracked_spending=15.99,
                recurring_monthly_total=15.99,
                leftover_money=1284.01,
                discretionary_remaining=1284.01,
                summary_text="",
            )

            dashboard = storage.get_dashboard_data(user_id, "2026-04")

            moderate = dashboard["scenario_analysis"][1]
            aggressive = dashboard["scenario_analysis"][2]
            self.assertFalse(any("trim one discretionary category" in action.lower() for action in moderate["actions"]))
            self.assertFalse(any("cut back across one or two discretionary categories" in action.lower() for action in aggressive["actions"]))
            self.assertTrue(any("netflix" in action.lower() for action in moderate["actions"] + aggressive["actions"]))

    def test_dashboard_scenarios_do_not_double_count_recurring_entertainment_inside_category_cut(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")
            user_id = storage.create_user("person@example.com", "secret123")
            storage.add_transactions(
                user_id,
                [
                    {
                        "date": "2026-03-05",
                        "description": "Netflix",
                        "amount": 25.0,
                        "category": "Entertainment",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-05",
                        "description": "Netflix",
                        "amount": 25.0,
                        "category": "Entertainment",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-09",
                        "description": "Movie Night",
                        "amount": 75.0,
                        "category": "Entertainment",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-12",
                        "description": "Dining Out",
                        "amount": 180.0,
                        "category": "Dining",
                        "source": "statement",
                    },
                ],
            )
            storage.upsert_financial_profile(
                user_id,
                monthly_income=4000,
                fixed_expenses=2200,
                budgeting_goal="",
                goal_name="Buffer",
                goal_target_amount=1500,
                goal_target_date="2026-11-01",
                current_saved_amount=300,
            )
            storage.save_monthly_summary(
                user_id,
                month_key="2026-04",
                income=4000,
                fixed_expenses=2200,
                tracked_spending=280.0,
                recurring_monthly_total=25.0,
                leftover_money=1520.0,
                discretionary_remaining=1520.0,
                summary_text="",
            )

            dashboard = storage.get_dashboard_data(user_id, "2026-04")

            moderate = dashboard["scenario_analysis"][1]
            aggressive = dashboard["scenario_analysis"][2]
            self.assertTrue(any("netflix" in action.lower() for action in moderate["actions"] + aggressive["actions"]))
            self.assertLessEqual(moderate["savings_impact_monthly"], 25.0 + round((180.0 * 0.18), 2))
            self.assertLessEqual(
                aggressive["savings_impact_monthly"],
                25.0 + round((180.0 * 0.35), 2) + round((75.0 * 0.30), 2),
            )

    def test_dashboard_scenarios_do_not_emit_zero_dollar_category_cut_after_overlap_netting(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")
            user_id = storage.create_user("person@example.com", "secret123")
            storage.add_transactions(
                user_id,
                [
                    {
                        "date": "2026-03-05",
                        "description": "Netflix",
                        "amount": 25.0,
                        "category": "Entertainment",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-05",
                        "description": "Netflix",
                        "amount": 25.0,
                        "category": "Entertainment",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-12",
                        "description": "Dining Out",
                        "amount": 180.0,
                        "category": "Dining",
                        "source": "statement",
                    },
                ],
            )
            storage.upsert_financial_profile(
                user_id,
                monthly_income=4000,
                fixed_expenses=2200,
                budgeting_goal="",
                goal_name="Buffer",
                goal_target_amount=1500,
                goal_target_date="2026-11-01",
                current_saved_amount=300,
            )
            storage.save_monthly_summary(
                user_id,
                month_key="2026-04",
                income=4000,
                fixed_expenses=2200,
                tracked_spending=205.0,
                recurring_monthly_total=25.0,
                leftover_money=1595.0,
                discretionary_remaining=1595.0,
                summary_text="",
            )

            dashboard = storage.get_dashboard_data(user_id, "2026-04")

            moderate = dashboard["scenario_analysis"][1]
            aggressive = dashboard["scenario_analysis"][2]
            self.assertFalse(any("$0.00/month" in action for action in moderate["actions"]))
            self.assertFalse(any("$0.00/month" in action for action in aggressive["actions"]))
            self.assertFalse(
                any("entertainment by about 18%" in action.lower() for action in moderate["actions"])
            )
            self.assertFalse(
                any("entertainment by about 35%" in action.lower() for action in aggressive["actions"])
            )

    def test_dashboard_stay_on_current_path_prefers_top_discretionary_category_over_fixed_category(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")
            user_id = storage.create_user("person@example.com", "secret123")
            storage.add_transactions(
                user_id,
                [
                    {
                        "date": "2026-04-01",
                        "description": "Rent",
                        "amount": 1600.0,
                        "category": "Housing",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-05",
                        "description": "Dining Out",
                        "amount": 220.0,
                        "category": "Dining",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-08",
                        "description": "Netflix",
                        "amount": 18.99,
                        "category": "Subscriptions",
                        "source": "statement",
                    },
                ],
            )
            storage.upsert_financial_profile(
                user_id,
                monthly_income=4500,
                fixed_expenses=2000,
                budgeting_goal="",
                goal_name="Travel",
                goal_target_amount=1800,
                goal_target_date="2026-11-01",
                current_saved_amount=200,
            )
            storage.save_monthly_summary(
                user_id,
                month_key="2026-04",
                income=4500,
                fixed_expenses=2000,
                tracked_spending=1838.99,
                recurring_monthly_total=18.99,
                leftover_money=661.01,
                discretionary_remaining=661.01,
                summary_text="",
            )

            dashboard = storage.get_dashboard_data(user_id, "2026-04")

            stay = dashboard["scenario_analysis"][0]
            self.assertTrue(any("dining" in action.lower() for action in stay["actions"]))
            self.assertFalse(any("housing" in action.lower() for action in stay["actions"]))

    def test_dashboard_long_dated_goal_is_not_marked_behind(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")
            user_id = storage.create_user("person@example.com", "secret123")
            storage.add_transactions(
                user_id,
                [
                    {
                        "date": "2026-04-04",
                        "description": "Dining Out",
                        "amount": 120.0,
                        "category": "Dining",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-08",
                        "description": "Grocery Run",
                        "amount": 80.0,
                        "category": "Groceries",
                        "source": "statement",
                    },
                ],
            )
            storage.upsert_financial_profile(
                user_id,
                monthly_income=3000,
                fixed_expenses=2000,
                budgeting_goal="",
                goal_name="Vacation fund",
                goal_target_amount=1200,
                goal_target_date="2026-12-01",
                current_saved_amount=200,
            )
            storage.save_monthly_summary(
                user_id,
                month_key="2026-04",
                income=3000,
                fixed_expenses=2000,
                tracked_spending=200,
                recurring_monthly_total=0,
                leftover_money=350,
                discretionary_remaining=350,
                summary_text="",
            )

            dashboard = storage.get_dashboard_data(user_id, "2026-04")

            self.assertNotEqual(dashboard["spending_profile"]["name"], "Goal-Focused but Behind")
            self.assertEqual(dashboard["spending_profile"]["name"], "Flexible Spender")
            self.assertEqual(
                dashboard["spending_profile"]["description"],
                "Your spending patterns are mixed, with room for more consistent planning.",
            )
            self.assertIn("2026-12-01", dashboard["goal_summary"])

    def test_dashboard_uses_combined_discretionary_cap_for_budget_optimizer(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")
            user_id = storage.create_user("person@example.com", "secret123")
            storage.add_transactions(
                user_id,
                [
                    {
                        "date": "2026-04-04",
                        "description": "Dining Out",
                        "amount": 220.0,
                        "category": "Dining",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-08",
                        "description": "Shopping Run",
                        "amount": 160.0,
                        "category": "Shopping",
                        "source": "statement",
                    },
                ],
            )
            storage.upsert_financial_profile(
                user_id,
                monthly_income=2000,
                fixed_expenses=1200,
                budgeting_goal="",
                goal_name="",
                goal_target_amount=0,
                goal_target_date="",
                current_saved_amount=0,
            )
            storage.save_monthly_summary(
                user_id,
                month_key="2026-04",
                income=2000,
                fixed_expenses=1200,
                tracked_spending=380,
                recurring_monthly_total=0,
                leftover_money=1420,
                discretionary_remaining=1420,
                summary_text="",
            )

            dashboard = storage.get_dashboard_data(user_id, "2026-04")

            self.assertEqual(dashboard["spending_profile"]["name"], "Budget Optimizer")
            self.assertEqual(
                dashboard["spending_profile"]["description"],
                "You are generally staying within budget and may benefit most from small optimizations.",
            )
            self.assertEqual(
                dashboard["spending_profile"]["reasons"][0],
                "Your top discretionary category is Dining at 92% of its own cap.",
            )

    def test_dashboard_budget_optimizer_explains_top_discretionary_category_not_top_overall_category(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")
            user_id = storage.create_user("person@example.com", "secret123")
            storage.add_transactions(
                user_id,
                [
                    {
                        "date": "2026-04-04",
                        "description": "Rent",
                        "amount": 700.0,
                        "category": "Housing",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-08",
                        "description": "Dining Out",
                        "amount": 420.0,
                        "category": "Dining",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-10",
                        "description": "Shopping Run",
                        "amount": 340.0,
                        "category": "Shopping",
                        "source": "statement",
                    },
                ],
            )
            storage.upsert_financial_profile(
                user_id,
                monthly_income=4000,
                fixed_expenses=2200,
                budgeting_goal="",
                goal_name="",
                goal_target_amount=0,
                goal_target_date="",
                current_saved_amount=0,
            )
            storage.save_monthly_summary(
                user_id,
                month_key="2026-04",
                income=4000,
                fixed_expenses=2200,
                tracked_spending=1460,
                recurring_monthly_total=0,
                leftover_money=340,
                discretionary_remaining=760,
                summary_text="",
            )

            dashboard = storage.get_dashboard_data(user_id, "2026-04")

            self.assertEqual(dashboard["spending_profile"]["name"], "Budget Optimizer")
            self.assertEqual(
                dashboard["spending_profile"]["description"],
                "You are generally staying within budget and may benefit most from small optimizations.",
            )
            self.assertEqual(
                dashboard["spending_profile"]["reasons"][0],
                "Your top discretionary category is Dining at 88% of its own cap.",
            )
            self.assertNotIn("Housing", dashboard["spending_profile"]["reasons"][0])

    def test_dashboard_goal_deadline_at_month_end_is_more_lenient_than_month_start(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")
            user_id_late = storage.create_user("late@example.com", "secret123")
            user_id_early = storage.create_user("early@example.com", "secret123")

            for user_id in (user_id_late, user_id_early):
                storage.add_transactions(
                    user_id,
                    [
                        {
                            "date": "2026-04-04",
                            "description": "Dining Out",
                            "amount": 120.0,
                            "category": "Dining",
                            "source": "statement",
                        },
                        {
                            "date": "2026-04-08",
                            "description": "Grocery Run",
                            "amount": 80.0,
                            "category": "Groceries",
                            "source": "statement",
                        },
                    ],
                )
                storage.upsert_financial_profile(
                    user_id,
                    monthly_income=3000,
                    fixed_expenses=2000,
                    budgeting_goal="",
                    goal_name="Vacation fund",
                    goal_target_amount=1200,
                    goal_target_date="2026-05-31" if user_id == user_id_late else "2026-05-01",
                    current_saved_amount=200,
                )
                storage.save_monthly_summary(
                    user_id,
                    month_key="2026-04",
                    income=3000,
                    fixed_expenses=2000,
                    tracked_spending=200,
                    recurring_monthly_total=0,
                    leftover_money=600,
                    discretionary_remaining=600,
                    summary_text="",
                )

            late_dashboard = storage.get_dashboard_data(user_id_late, "2026-04")
            early_dashboard = storage.get_dashboard_data(user_id_early, "2026-04")

            self.assertEqual(late_dashboard["spending_profile"]["name"], "Flexible Spender")
            self.assertEqual(early_dashboard["spending_profile"]["name"], "Goal-Focused but Behind")
            self.assertEqual(
                early_dashboard["spending_profile"]["description"],
                "You have a clear goal, but your current spending pace may delay your progress.",
            )

    def test_dashboard_invalid_goal_date_does_not_trigger_goal_focused_but_behind(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")
            user_id = storage.create_user("invalid-date@example.com", "secret123")
            storage.add_transactions(
                user_id,
                [
                    {
                        "date": "2026-04-04",
                        "description": "Dining Out",
                        "amount": 120.0,
                        "category": "Dining",
                        "source": "statement",
                    }
                ],
            )
            storage.upsert_financial_profile(
                user_id,
                monthly_income=3000,
                fixed_expenses=2000,
                budgeting_goal="",
                goal_name="Vacation fund",
                goal_target_amount=1200,
                goal_target_date="not-a-date",
                current_saved_amount=200,
            )
            storage.save_monthly_summary(
                user_id,
                month_key="2026-04",
                income=3000,
                fixed_expenses=2000,
                tracked_spending=120,
                recurring_monthly_total=0,
                leftover_money=800,
                discretionary_remaining=800,
                summary_text="",
            )

            dashboard = storage.get_dashboard_data(user_id, "2026-04")

            self.assertNotEqual(dashboard["spending_profile"]["name"], "Goal-Focused but Behind")
            self.assertEqual(dashboard["spending_profile"]["name"], "Flexible Spender")

    def test_dashboard_reports_reactive_spender_when_spend_is_over_combined_cap(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")
            user_id = storage.create_user("reactive@example.com", "secret123")
            storage.add_transactions(
                user_id,
                [
                    {
                        "date": "2026-04-04",
                        "description": "Dining Out",
                        "amount": 700.0,
                        "category": "Dining",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-08",
                        "description": "Shopping Run",
                        "amount": 350.0,
                        "category": "Shopping",
                        "source": "statement",
                    },
                ],
            )
            storage.upsert_financial_profile(
                user_id,
                monthly_income=4000,
                fixed_expenses=2200,
                budgeting_goal="",
                goal_name="",
                goal_target_amount=0,
                goal_target_date="2026-12-31",
                current_saved_amount=0,
            )
            storage.save_monthly_summary(
                user_id,
                month_key="2026-04",
                income=4000,
                fixed_expenses=2200,
                tracked_spending=1050,
                recurring_monthly_total=0,
                leftover_money=1000,
                discretionary_remaining=-250,
                summary_text="",
            )

            dashboard = storage.get_dashboard_data(user_id, "2026-04")

            self.assertEqual(dashboard["spending_profile"]["name"], "Reactive Spender")
            self.assertEqual(
                dashboard["spending_profile"]["description"],
                "You tend to overspend in discretionary categories, especially when expenses are not actively tracked.",
            )
            self.assertIn("more than 25%", dashboard["spending_profile"]["reasons"][1])

    def test_dashboard_amount_only_goal_does_not_become_goal_focused_but_behind(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")
            user_id = storage.create_user("partial@example.com", "secret123")
            storage.add_transactions(
                user_id,
                [
                    {
                        "date": "2026-04-04",
                        "description": "Dining Out",
                        "amount": 120.0,
                        "category": "Dining",
                        "source": "statement",
                    }
                ],
            )
            storage.upsert_financial_profile(
                user_id,
                monthly_income=3000,
                fixed_expenses=2000,
                budgeting_goal="",
                goal_name="Vacation fund",
                goal_target_amount=1200,
                goal_target_date="",
                current_saved_amount=200,
            )
            storage.save_monthly_summary(
                user_id,
                month_key="2026-04",
                income=3000,
                fixed_expenses=2000,
                tracked_spending=120,
                recurring_monthly_total=0,
                leftover_money=800,
                discretionary_remaining=800,
                summary_text="",
            )

            dashboard = storage.get_dashboard_data(user_id, "2026-04")

            self.assertNotEqual(dashboard["spending_profile"]["name"], "Goal-Focused but Behind")
            self.assertEqual(dashboard["spending_profile"]["name"], "Flexible Spender")
            self.assertEqual(
                dashboard["spending_profile"]["description"],
                "Your spending patterns are mixed, with room for more consistent planning.",
            )

    def test_dashboard_due_or_overdue_goal_with_unmet_amount_is_goal_focused_but_behind(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")
            user_id = storage.create_user("due@example.com", "secret123")
            storage.add_transactions(
                user_id,
                [
                    {
                        "date": "2026-04-04",
                        "description": "Dining Out",
                        "amount": 120.0,
                        "category": "Dining",
                        "source": "statement",
                    }
                ],
            )
            storage.upsert_financial_profile(
                user_id,
                monthly_income=3000,
                fixed_expenses=2000,
                budgeting_goal="",
                goal_name="Vacation fund",
                goal_target_amount=1200,
                goal_target_date="2026-04-01",
                current_saved_amount=200,
            )
            storage.save_monthly_summary(
                user_id,
                month_key="2026-04",
                income=3000,
                fixed_expenses=2000,
                tracked_spending=120,
                recurring_monthly_total=0,
                leftover_money=800,
                discretionary_remaining=800,
                summary_text="",
            )

            dashboard = storage.get_dashboard_data(user_id, "2026-04")

            self.assertEqual(dashboard["spending_profile"]["name"], "Goal-Focused but Behind")
            self.assertIn("Vacation fund", dashboard["goal_summary"])
            self.assertEqual(
                dashboard["spending_profile"]["description"],
                "You have a clear goal, but your current spending pace may delay your progress.",
            )

    def test_dashboard_goal_with_missing_monthly_summary_can_still_be_goal_focused_but_behind(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")
            user_id = storage.create_user("nomonthly@example.com", "secret123")
            storage.add_transactions(
                user_id,
                [
                    {
                        "date": "2026-04-04",
                        "description": "Restaurant Row",
                        "amount": 420.0,
                        "category": "Dining",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-08",
                        "description": "Shopping Run",
                        "amount": 250.0,
                        "category": "Shopping",
                        "source": "statement",
                    },
                ],
            )
            storage.upsert_financial_profile(
                user_id,
                monthly_income=3000,
                fixed_expenses=2600,
                budgeting_goal="",
                goal_name="Japan trip",
                goal_target_amount=2000,
                goal_target_date="2026-06-01",
                current_saved_amount=200,
            )

            dashboard = storage.get_dashboard_data(user_id, "2026-04")

            self.assertEqual(dashboard["spending_profile"]["name"], "Goal-Focused but Behind")
            self.assertEqual(
                dashboard["spending_profile"]["description"],
                "You have a clear goal, but your current spending pace may delay your progress.",
            )

    def test_dashboard_missing_monthly_summary_goal_uses_effective_room_in_reason_text(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")
            user_id = storage.create_user("nomonthly-reason@example.com", "secret123")
            storage.add_transactions(
                user_id,
                [
                    {
                        "date": "2026-04-04",
                        "description": "Restaurant Row",
                        "amount": 420.0,
                        "category": "Dining",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-08",
                        "description": "Shopping Run",
                        "amount": 250.0,
                        "category": "Shopping",
                        "source": "statement",
                    },
                ],
            )
            storage.upsert_financial_profile(
                user_id,
                monthly_income=3000,
                fixed_expenses=2600,
                budgeting_goal="",
                goal_name="Japan trip",
                goal_target_amount=2000,
                goal_target_date="2026-06-01",
                current_saved_amount=200,
            )

            dashboard = storage.get_dashboard_data(user_id, "2026-04")

            self.assertEqual(dashboard["spending_profile"]["name"], "Goal-Focused but Behind")
            self.assertIn("Current month spending leaves about $-270.00", dashboard["spending_profile"]["reasons"][1])

    def test_dashboard_goal_only_without_pace_data_does_not_default_to_behind(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")
            user_id = storage.create_user("goal-only@example.com", "secret123")
            storage.upsert_financial_profile(
                user_id,
                monthly_income=0,
                fixed_expenses=0,
                budgeting_goal="",
                goal_name="House down payment",
                goal_target_amount=50000,
                goal_target_date="2026-12-01",
                current_saved_amount=10000,
            )

            dashboard = storage.get_dashboard_data(user_id, "2026-04")

            self.assertNotEqual(dashboard["spending_profile"]["name"], "Goal-Focused but Behind")
            self.assertEqual(dashboard["spending_profile"]["name"], "Flexible Spender")
            self.assertIn("House down payment", dashboard["goal_summary"])

    def test_dashboard_recurring_expenses_are_visible_in_spending_profile_reasons(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")
            user_id = storage.create_user("recurring@example.com", "secret123")
            storage.add_transactions(
                user_id,
                [
                    {
                        "date": "2026-04-04",
                        "description": "Dining Out",
                        "amount": 220.0,
                        "category": "Dining",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-01",
                        "description": "Streaming",
                        "amount": 80.0,
                        "category": "Subscriptions",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-30",
                        "description": "Streaming",
                        "amount": 80.0,
                        "category": "Subscriptions",
                        "source": "statement",
                    },
                ],
            )
            storage.upsert_financial_profile(
                user_id,
                monthly_income=4000,
                fixed_expenses=2200,
                budgeting_goal="",
                goal_name="",
                goal_target_amount=0,
                goal_target_date="",
                current_saved_amount=0,
            )
            storage.save_monthly_summary(
                user_id,
                month_key="2026-04",
                income=4000,
                fixed_expenses=2200,
                tracked_spending=380,
                recurring_monthly_total=80,
                leftover_money=1420,
                discretionary_remaining=1420,
                summary_text="",
            )

            dashboard = storage.get_dashboard_data(user_id, "2026-04")

            self.assertTrue(any("recurring" in reason.lower() for reason in dashboard["spending_profile"]["reasons"]))
            self.assertTrue(any("80.00" in reason for reason in dashboard["spending_profile"]["reasons"]))

    def test_dashboard_coffee_rideshare_and_delivery_count_as_discretionary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")
            user_id = storage.create_user("discretionary@example.com", "secret123")
            storage.add_transactions(
                user_id,
                [
                    {
                        "date": "2026-04-02",
                        "description": "Morning Coffee",
                        "amount": 250.0,
                        "category": "Coffee",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-05",
                        "description": "Uber",
                        "amount": 350.0,
                        "category": "Rideshare",
                        "source": "statement",
                    },
                    {
                        "date": "2026-04-09",
                        "description": "DoorDash",
                        "amount": 250.0,
                        "category": "Delivery",
                        "source": "statement",
                    },
                ],
            )
            storage.upsert_financial_profile(
                user_id,
                monthly_income=4000,
                fixed_expenses=2200,
                budgeting_goal="",
                goal_name="",
                goal_target_amount=0,
                goal_target_date="",
                current_saved_amount=0,
            )

            dashboard = storage.get_dashboard_data(user_id, "2026-04")

            self.assertEqual(dashboard["spending_profile"]["name"], "Budget Optimizer")
            self.assertIn("Rideshare", dashboard["spending_profile"]["reasons"][0])
            self.assertIn("Current discretionary spending is $850.00", dashboard["spending_profile"]["reasons"][1])

    def test_dashboard_groceries_alone_do_not_trigger_discretionary_classification(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = Storage(f"{tmpdir}/app.db")
            user_id = storage.create_user("groceries@example.com", "secret123")
            storage.add_transactions(
                user_id,
                [
                    {
                        "date": "2026-04-03",
                        "description": "Grocery Run",
                        "amount": 650.0,
                        "category": "Groceries",
                        "source": "statement",
                    }
                ],
            )
            storage.upsert_financial_profile(
                user_id,
                monthly_income=4000,
                fixed_expenses=2200,
                budgeting_goal="",
                goal_name="",
                goal_target_amount=0,
                goal_target_date="",
                current_saved_amount=0,
            )
            storage.save_monthly_summary(
                user_id,
                month_key="2026-04",
                income=4000,
                fixed_expenses=2200,
                tracked_spending=650,
                recurring_monthly_total=0,
                leftover_money=1150,
                discretionary_remaining=1150,
                summary_text="",
            )

            dashboard = storage.get_dashboard_data(user_id, "2026-04")

            self.assertEqual(dashboard["spending_profile"]["name"], "Flexible Spender")
            self.assertNotIn("Groceries", " ".join(dashboard["spending_profile"]["reasons"]))

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
