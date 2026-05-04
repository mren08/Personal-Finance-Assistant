from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import secrets
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from csv_parser import CategorizedTransaction, StatementCsvParser
from recurrence import RecurringExpenseAnalyzer
from recommender import BudgetRecommender

_MISSING = object()


class Storage:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    date TEXT NOT NULL,
                    description TEXT NOT NULL,
                    amount REAL NOT NULL,
                    category TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS subscription_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    merchant TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS user_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    entry_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS pending_actions (
                    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    action_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS financial_profiles (
                    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    monthly_income REAL NOT NULL DEFAULT 0,
                    fixed_expenses REAL NOT NULL DEFAULT 0,
                    budgeting_goal TEXT NOT NULL DEFAULT '',
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS monthly_plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    month_key TEXT NOT NULL,
                    monthly_income REAL NOT NULL DEFAULT 0,
                    fixed_expenses REAL NOT NULL DEFAULT 0,
                    budgeting_goal TEXT NOT NULL DEFAULT '',
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, month_key)
                );

                CREATE TABLE IF NOT EXISTS agent_notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    note_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS monthly_summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    month_key TEXT NOT NULL,
                    income REAL NOT NULL,
                    fixed_expenses REAL NOT NULL,
                    tracked_spending REAL NOT NULL,
                    recurring_monthly_total REAL NOT NULL,
                    leftover_money REAL NOT NULL,
                    discretionary_remaining REAL NOT NULL,
                    summary_text TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, month_key)
                );

                CREATE TABLE IF NOT EXISTS receipt_uploads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    filename TEXT NOT NULL,
                    storage_path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS receipt_extractions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    receipt_upload_id INTEGER NOT NULL REFERENCES receipt_uploads(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
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

                CREATE TABLE IF NOT EXISTS receipt_transaction_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    receipt_extraction_id INTEGER NOT NULL REFERENCES receipt_extractions(id) ON DELETE CASCADE,
                    transaction_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS merchant_category_cache (
                    merchant_key TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    enrichment_source TEXT NOT NULL,
                    checked_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS receipt_behavior_insights (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    month_key TEXT NOT NULL,
                    note TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS password_reset_tokens (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    token_hash TEXT NOT NULL UNIQUE,
                    expires_at TEXT NOT NULL,
                    used_at TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                """
            )
            self._repair_receipt_transaction_link_duplicates(conn)
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_receipt_transaction_links_receipt_extraction_id
                ON receipt_transaction_links(receipt_extraction_id)
                """
            )
            self._ensure_column(conn, "financial_profiles", "goal_name", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "financial_profiles", "goal_target_amount", "REAL NOT NULL DEFAULT 0")
            self._ensure_column(conn, "financial_profiles", "goal_target_date", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "financial_profiles", "current_saved_amount", "REAL NOT NULL DEFAULT 0")
            self._ensure_column(conn, "monthly_plans", "goal_name", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "monthly_plans", "goal_target_amount", "REAL NOT NULL DEFAULT 0")
            self._ensure_column(conn, "monthly_plans", "goal_target_date", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "monthly_plans", "current_saved_amount", "REAL NOT NULL DEFAULT 0")

    @staticmethod
    def _repair_receipt_transaction_link_duplicates(conn: sqlite3.Connection) -> None:
        duplicate_groups = conn.execute(
            """
            SELECT receipt_extraction_id
            FROM receipt_transaction_links
            GROUP BY receipt_extraction_id
            HAVING COUNT(*) > 1
            """
        ).fetchall()
        for group in duplicate_groups:
            rows = conn.execute(
                """
                SELECT id, transaction_id
                FROM receipt_transaction_links
                WHERE receipt_extraction_id = ?
                ORDER BY id ASC
                """,
                (group["receipt_extraction_id"],),
            ).fetchall()
            if not rows:
                continue
            duplicate_rows = rows[1:]
            for row in duplicate_rows:
                transaction_id = int(row["transaction_id"])
                usage_count = conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM receipt_transaction_links
                    WHERE transaction_id = ?
                    """,
                    (transaction_id,),
                ).fetchone()[0]
                if usage_count == 1:
                    transaction_row = conn.execute(
                        """
                        SELECT source
                        FROM transactions
                        WHERE id = ?
                        """,
                        (transaction_id,),
                    ).fetchone()
                    if transaction_row and transaction_row["source"] == "receipt":
                        conn.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))
                conn.execute("DELETE FROM receipt_transaction_links WHERE id = ?", (int(row["id"]),))

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @staticmethod
    def _format_currency(amount: float) -> str:
        formatted = f"${float(amount):,.2f}"
        return formatted[:-3] if formatted.endswith(".00") else formatted.rstrip("0").rstrip(".")

    @classmethod
    def _derive_budgeting_goal(
        cls,
        budgeting_goal: str,
        goal_name: str,
        goal_target_amount: float,
        goal_target_date: str,
        current_saved_amount: float,
    ) -> str:
        goal_text = str(budgeting_goal or "").strip()
        goal_name = str(goal_name or "").strip()
        goal_target_date = str(goal_target_date or "").strip()
        goal_target_amount = float(goal_target_amount or 0)
        current_saved_amount = float(current_saved_amount or 0)

        has_structured_goal = (
            (goal_name and (goal_target_amount > 0 or goal_target_date))
            or (goal_target_amount > 0 and goal_target_date)
        )
        if not has_structured_goal:
            if goal_text:
                return goal_text
            if goal_name:
                return goal_name
            if goal_target_amount > 0 and goal_target_date:
                return f"{cls._format_currency(goal_target_amount)} by {goal_target_date}"
            if goal_target_amount > 0:
                return cls._format_currency(goal_target_amount)
            if goal_target_date:
                return f"by {goal_target_date}"
            if current_saved_amount > 0:
                return f"saved so far: {cls._format_currency(current_saved_amount)}"
            return goal_text

        summary_parts: list[str] = []
        if goal_target_amount > 0:
            summary_parts.append(cls._format_currency(goal_target_amount))
        if goal_target_date:
            summary_parts.append(f"by {goal_target_date}")

        prefix = f"{goal_name}: " if goal_name else ""
        summary = f"{prefix}{' '.join(summary_parts)}".strip()
        if current_saved_amount > 0:
            saved_text = f"saved so far: {cls._format_currency(current_saved_amount)}"
            summary = f"{summary} ({saved_text})" if summary else saved_text
        return summary or goal_text

    @staticmethod
    def _goal_field_value(
        provided_value: str | float | object,
        existing_value: Any,
        default_value: str | float,
    ) -> str | float:
        if provided_value is _MISSING:
            return default_value if existing_value is None else existing_value
        return provided_value

    @staticmethod
    def _hash_password(password: str) -> str:
        return hashlib.sha256(password.encode("utf-8")).hexdigest()

    @staticmethod
    def _hash_reset_token(raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    def create_user(self, email: str, password: str) -> int:
        normalized_email = email.strip().lower()
        if not normalized_email or not password:
            raise ValueError("Email and password are required.")

        with self._connect() as conn:
            try:
                cursor = conn.execute(
                    "INSERT INTO users (email, password_hash) VALUES (?, ?)",
                    (normalized_email, self._hash_password(password)),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("An account with that email already exists.") from exc
            return int(cursor.lastrowid)

    def replace_user(self, email: str, password: str) -> int:
        normalized_email = email.strip().lower()
        if not normalized_email or not password:
            raise ValueError("Email and password are required.")

        with self._connect() as conn:
            conn.execute("DELETE FROM users WHERE email = ?", (normalized_email,))
            cursor = conn.execute(
                "INSERT INTO users (email, password_hash) VALUES (?, ?)",
                (normalized_email, self._hash_password(password)),
            )
            return int(cursor.lastrowid)

    def authenticate_user(self, email: str, password: str) -> int | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, password_hash FROM users WHERE email = ?",
                (email.strip().lower(),),
            ).fetchone()
        if not row or row["password_hash"] != self._hash_password(password):
            return None
        return int(row["id"])

    def update_password(self, email: str, new_password: str) -> None:
        normalized_email = email.strip().lower()
        if not normalized_email or not new_password:
            raise ValueError("Email and new password are required.")

        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE users SET password_hash = ? WHERE email = ?",
                (self._hash_password(new_password), normalized_email),
            )
        if cursor.rowcount == 0:
            raise ValueError("No account found for that email.")

    def create_password_reset_token(self, email: str) -> dict[str, Any] | None:
        normalized_email = email.strip().lower()
        with self._connect() as conn:
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            user_row = conn.execute(
                "SELECT id, email FROM users WHERE email = ?",
                (normalized_email,),
            ).fetchone()
            if not user_row:
                return None

            conn.execute(
                """
                UPDATE password_reset_tokens
                SET used_at = ?
                WHERE user_id = ?
                  AND used_at IS NULL
                  AND expires_at > ?
                """,
                (now, int(user_row["id"]), now),
            )
            raw_token = secrets.token_urlsafe(32)
            expires_at = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(timespec="seconds")
            conn.execute(
                """
                INSERT INTO password_reset_tokens (user_id, token_hash, expires_at)
                VALUES (?, ?, ?)
                """,
                (int(user_row["id"]), self._hash_reset_token(raw_token), expires_at),
            )
        return {
            "user_id": int(user_row["id"]),
            "email": user_row["email"],
            "token": raw_token,
            "expires_in_minutes": 30,
        }

    def get_password_reset_token(self, raw_token: str) -> dict[str, Any] | None:
        token_hash = self._hash_reset_token(raw_token)
        with self._connect() as conn:
            row = self._get_active_password_reset_token_row(conn, token_hash)
        if not row:
            return None
        return {
            "id": int(row["id"]),
            "user_id": int(row["user_id"]),
            "email": row["email"],
            "expires_at": row["expires_at"],
            "used_at": row["used_at"],
        }

    def _get_active_password_reset_token_row(
        self,
        conn: sqlite3.Connection,
        token_hash: str,
    ) -> sqlite3.Row | None:
        row = conn.execute(
            """
            SELECT prt.id, prt.user_id, prt.expires_at, prt.used_at, u.email
            FROM password_reset_tokens prt
            JOIN users u ON u.id = prt.user_id
            WHERE prt.token_hash = ?
            """,
            (token_hash,),
        ).fetchone()
        if not row or row["used_at"]:
            return None
        expires_at = datetime.fromisoformat(row["expires_at"])
        if expires_at <= datetime.now(timezone.utc):
            return None
        return row

    def reset_password_with_token(self, raw_token: str, new_password: str) -> None:
        if not new_password:
            raise ValueError("New password is required.")

        token_hash = self._hash_reset_token(raw_token)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute("BEGIN")
            cursor = conn.execute(
                """
                UPDATE password_reset_tokens
                SET used_at = ?
                WHERE token_hash = ?
                  AND used_at IS NULL
                  AND expires_at > ?
                """,
                (now, token_hash, now),
            )
            if cursor.rowcount == 0:
                raise ValueError("Reset link is invalid or expired.")

            row = conn.execute(
                """
                SELECT id, user_id
                FROM password_reset_tokens
                WHERE token_hash = ?
                  AND used_at = ?
                """,
                (token_hash, now),
            ).fetchone()
            if not row:
                raise RuntimeError("Password reset token claim failed.")

            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (self._hash_password(new_password), int(row["user_id"])),
            )
            conn.execute(
                """
                UPDATE password_reset_tokens
                SET used_at = ?
                WHERE user_id = ?
                  AND used_at IS NULL
                  AND id != ?
                  AND expires_at > ?
                """,
                (now, int(row["user_id"]), int(row["id"]), now),
            )

    def get_user(self, user_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT id, email FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return None
        return {"id": int(row["id"]), "email": row["email"]}

    @staticmethod
    def _month_key_from_date(date_value: str) -> str:
        return str(date_value)[:7]

    @staticmethod
    def _month_label(month_key: str) -> str:
        year, month = month_key.split("-")
        month_names = [
            "",
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ]
        return f"{month_names[int(month)]} {year}"

    def add_transactions(self, user_id: int, transactions: list[dict[str, Any]]) -> None:
        if not transactions:
            return

        with self._connect() as conn:
            for item in transactions:
                self._insert_transaction_row(conn, user_id, item)

    def _insert_transaction_row(
        self,
        conn: sqlite3.Connection,
        user_id: int,
        item: dict[str, Any],
    ) -> int:
        cursor = conn.execute(
            """
            INSERT INTO transactions (user_id, date, description, amount, category, source)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                item["date"],
                item["description"],
                round(float(item["amount"]), 2),
                item["category"],
                item["source"],
            ),
        )
        return int(cursor.lastrowid)

    @staticmethod
    def _normalize_merchant_key(merchant_key: str) -> str:
        normalized = re.sub(r"[^a-z0-9]+", " ", merchant_key.strip().lower())
        return re.sub(r"\s+", " ", normalized).strip()

    @staticmethod
    def _legacy_normalize_merchant_key(merchant_key: str) -> str:
        return merchant_key.strip().lower()

    def normalize_merchant_key(self, merchant_key: str) -> str:
        return self._normalize_merchant_key(merchant_key)

    def create_receipt_upload(self, user_id: int, filename: str, storage_path: str) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO receipt_uploads (user_id, filename, storage_path, status)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, filename, storage_path, "uploaded"),
            )
            return int(cursor.lastrowid)

    def save_receipt_extraction(
        self,
        user_id: int,
        receipt_upload_id: int,
        merchant: str,
        transaction_date: str,
        total_amount: float,
        category: str,
        category_confidence: float,
        status: str,
        behavior_note: str,
        item_tags_json: str,
        raw_extraction_json: str,
        web_enrichment_json: str,
    ) -> int:
        with self._connect() as conn:
            upload_row = conn.execute(
                """
                SELECT user_id
                FROM receipt_uploads
                WHERE id = ?
                """,
                (receipt_upload_id,),
            ).fetchone()
            if not upload_row:
                raise ValueError("Receipt upload does not exist.")
            if int(upload_row["user_id"]) != user_id:
                raise ValueError("Receipt upload does not belong to that user.")
            cursor = conn.execute(
                """
                INSERT INTO receipt_extractions (
                    receipt_upload_id,
                    user_id,
                    merchant,
                    transaction_date,
                    total_amount,
                    category,
                    category_confidence,
                    status,
                    behavior_note,
                    item_tags_json,
                    raw_extraction_json,
                    web_enrichment_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt_upload_id,
                    user_id,
                    merchant,
                    transaction_date,
                    round(float(total_amount), 2),
                    category,
                    round(float(category_confidence), 2),
                    status,
                    behavior_note,
                    item_tags_json,
                    raw_extraction_json,
                    web_enrichment_json,
                ),
            )
            return int(cursor.lastrowid)

    def list_pending_receipt_extractions(self, user_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    id,
                    receipt_upload_id,
                    merchant,
                    transaction_date,
                    total_amount,
                    category,
                    category_confidence,
                    status,
                    behavior_note,
                    item_tags_json,
                    raw_extraction_json,
                    web_enrichment_json,
                    reviewed_at,
                    created_at
                FROM receipt_extractions
                WHERE user_id = ?
                  AND status != 'discarded'
                  AND status != 'approved'
                ORDER BY id DESC
                """,
                (user_id,),
            ).fetchall()
        return [self._receipt_extraction_from_row(row) for row in rows]

    @staticmethod
    def _receipt_extraction_from_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["total_amount"] = round(float(item["total_amount"]), 2)
        item["category_confidence"] = round(float(item["category_confidence"]), 2)
        try:
            item["item_tags"] = json.loads(item.get("item_tags_json") or "[]")
        except json.JSONDecodeError:
            item["item_tags"] = []
        return item

    def approve_receipt_extraction(
        self,
        user_id: int,
        extraction_id: int,
        merchant: str,
        transaction_date: str,
        total_amount: float,
        category: str,
    ) -> int:
        with self._connect() as conn:
            extraction_row = conn.execute(
                """
                SELECT behavior_note
                FROM receipt_extractions
                WHERE id = ? AND user_id = ?
                """,
                (extraction_id, user_id),
            ).fetchone()
            # Placeholder uploads start in needs_correction and become approvable once the user supplies valid edits.
            cursor = conn.execute(
                """
                UPDATE receipt_extractions
                SET merchant = ?,
                    transaction_date = ?,
                    total_amount = ?,
                    category = ?,
                    status = ?,
                    reviewed_at = CURRENT_TIMESTAMP
                WHERE id = ? AND user_id = ? AND status IN ('ready', 'needs_category', 'needs_correction')
                """,
                (
                    merchant,
                    transaction_date,
                    round(float(total_amount), 2),
                    category,
                    "approved",
                    extraction_id,
                    user_id,
                ),
            )
            if cursor.rowcount == 0:
                exists = conn.execute(
                    """
                    SELECT status
                    FROM receipt_extractions
                    WHERE id = ? AND user_id = ?
                    """,
                    (extraction_id, user_id),
                ).fetchone()
                if not exists:
                    raise ValueError("No receipt extraction found for that user.")
                if exists["status"] in {"approved", "discarded"}:
                    raise ValueError("Receipt extraction has already been finalized.")
                raise ValueError("Receipt extraction is not pending review.")

            transaction_id = self._insert_transaction_row(
                conn,
                user_id,
                {
                    "date": transaction_date,
                    "description": merchant,
                    "amount": total_amount,
                    "category": category,
                    "source": "receipt",
                },
            )
            try:
                conn.execute(
                    """
                    INSERT INTO receipt_transaction_links (receipt_extraction_id, transaction_id)
                    VALUES (?, ?)
                    """,
                    (extraction_id, transaction_id),
                )
            except sqlite3.IntegrityError as exc:
                raise RuntimeError("Receipt extraction is already linked to a transaction.") from exc
            behavior_note = str(extraction_row["behavior_note"] if extraction_row else "").strip()
            approved_month_key = self._month_key_from_date(transaction_date)
            if behavior_note:
                conn.execute(
                    """
                    INSERT INTO receipt_behavior_insights (user_id, month_key, note)
                    VALUES (?, ?, ?)
                    """,
                    (user_id, approved_month_key, behavior_note),
                )
        return transaction_id

    def discard_receipt_extraction(self, user_id: int, extraction_id: int) -> None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT status
                FROM receipt_extractions
                WHERE id = ? AND user_id = ?
                """,
                (extraction_id, user_id),
            ).fetchone()
            if not row:
                raise ValueError("No receipt extraction found for that user.")
            if row["status"] in {"approved", "discarded"}:
                raise ValueError("Receipt extraction has already been finalized.")

            conn.execute(
                """
                UPDATE receipt_extractions
                SET status = ?, reviewed_at = CURRENT_TIMESTAMP
                WHERE id = ? AND user_id = ?
                """,
                ("discarded", extraction_id, user_id),
            )

    def get_receipt_transaction_link(self, extraction_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, receipt_extraction_id, transaction_id, created_at
                FROM receipt_transaction_links
                WHERE receipt_extraction_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (extraction_id,),
            ).fetchone()
        if not row:
            return None
        return dict(row)

    def get_cached_merchant_category(self, merchant_key: str) -> dict[str, Any] | None:
        normalized_key = self._normalize_merchant_key(merchant_key)
        candidate_keys = [normalized_key]
        legacy_key = self._legacy_normalize_merchant_key(merchant_key)
        if legacy_key and legacy_key not in candidate_keys:
            candidate_keys.append(legacy_key)
        with self._connect() as conn:
            row = None
            for candidate_key in candidate_keys:
                row = conn.execute(
                    """
                    SELECT merchant_key, category, confidence, enrichment_source, checked_at
                    FROM merchant_category_cache
                    WHERE merchant_key = ?
                    """,
                    (candidate_key,),
                ).fetchone()
                if row:
                    break
        if not row:
            return None
        cached = dict(row)
        cached["confidence"] = round(float(cached["confidence"]), 2)
        return cached

    def save_cached_merchant_category(self, merchant_key: str, category: str, confidence: float, enrichment_source: str) -> None:
        normalized_key = self._normalize_merchant_key(merchant_key)
        with self._connect() as conn:
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
                ON CONFLICT(merchant_key) DO UPDATE SET
                    category = excluded.category,
                    confidence = excluded.confidence,
                    enrichment_source = excluded.enrichment_source,
                    checked_at = CURRENT_TIMESTAMP
                """,
                (normalized_key, category, round(float(confidence), 2), enrichment_source),
            )

    def save_receipt_behavior_insight(self, user_id: int, month_key: str, note: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO receipt_behavior_insights (user_id, month_key, note)
                VALUES (?, ?, ?)
                """,
                (user_id, month_key, note),
            )

    def list_receipt_behavior_insights(self, user_id: int, month_key: str) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT note
                FROM receipt_behavior_insights
                WHERE user_id = ? AND month_key = ?
                ORDER BY id DESC
                """,
                (user_id, month_key),
            ).fetchall()
        return [row["note"] for row in rows]

    def add_chat_message(self, user_id: int, role: str, content: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO chat_messages (user_id, role, content) VALUES (?, ?, ?)",
                (user_id, role, content),
            )

    def list_chat_messages(self, user_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT role, content, created_at
                FROM chat_messages
                WHERE user_id = ?
                ORDER BY id ASC
                """,
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_subscription_decision(self, user_id: int, merchant: str, decision: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO subscription_decisions (user_id, merchant, decision)
                VALUES (?, ?, ?)
                """,
                (user_id, merchant, decision),
            )

    def list_subscription_decisions(self, user_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT merchant, decision, created_at
                FROM subscription_decisions
                WHERE user_id = ?
                ORDER BY id DESC
                """,
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_user_decision(self, user_id: int, entry_type: str, title: str, content: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_decisions (user_id, entry_type, title, content)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, entry_type, title, content),
            )

    def list_user_decisions(self, user_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT entry_type, title, content, created_at
                FROM user_decisions
                WHERE user_id = ?
                ORDER BY id DESC
                """,
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def set_pending_action(self, user_id: int, action_type: str, payload: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO pending_actions (user_id, action_type, payload)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    action_type = excluded.action_type,
                    payload = excluded.payload,
                    created_at = CURRENT_TIMESTAMP
                """,
                (user_id, action_type, json.dumps(payload)),
            )

    def get_pending_action(self, user_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT action_type, payload, created_at
                FROM pending_actions
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
        if not row:
            return None
        payload = json.loads(row["payload"])
        return {
            "type": row["action_type"],
            "created_at": row["created_at"],
            **payload,
        }

    def clear_pending_action(self, user_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM pending_actions WHERE user_id = ?", (user_id,))

    def upsert_financial_profile(
        self,
        user_id: int,
        monthly_income: float,
        fixed_expenses: float,
        budgeting_goal: str,
        goal_name: str | object = _MISSING,
        goal_target_amount: float | object = _MISSING,
        goal_target_date: str | object = _MISSING,
        current_saved_amount: float | object = _MISSING,
    ) -> None:
        structured_goal_provided = any(
            value is not _MISSING
            for value in (goal_name, goal_target_amount, goal_target_date, current_saved_amount)
        )
        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT goal_name, goal_target_amount, goal_target_date, current_saved_amount
                FROM financial_profiles
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
            if structured_goal_provided:
                goal_name_value = self._goal_field_value(goal_name, existing["goal_name"] if existing else None, "")
                goal_target_amount_value = self._goal_field_value(
                    goal_target_amount,
                    existing["goal_target_amount"] if existing else None,
                    0,
                )
                goal_target_date_value = self._goal_field_value(
                    goal_target_date,
                    existing["goal_target_date"] if existing else None,
                    "",
                )
                current_saved_amount_value = self._goal_field_value(
                    current_saved_amount,
                    existing["current_saved_amount"] if existing else None,
                    0,
                )
                budgeting_goal = self._derive_budgeting_goal(
                    budgeting_goal,
                    str(goal_name_value or ""),
                    float(goal_target_amount_value or 0),
                    str(goal_target_date_value or ""),
                    float(current_saved_amount_value or 0),
                )
            else:
                goal_name_value = existing["goal_name"] if existing else ""
                goal_target_amount_value = existing["goal_target_amount"] if existing else 0
                goal_target_date_value = existing["goal_target_date"] if existing else ""
                current_saved_amount_value = existing["current_saved_amount"] if existing else 0
                budgeting_goal = str(budgeting_goal or "").strip()
            conn.execute(
                """
                INSERT INTO financial_profiles (
                    user_id,
                    monthly_income,
                    fixed_expenses,
                    budgeting_goal,
                    goal_name,
                    goal_target_amount,
                    goal_target_date,
                    current_saved_amount
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    monthly_income = excluded.monthly_income,
                    fixed_expenses = excluded.fixed_expenses,
                    budgeting_goal = excluded.budgeting_goal,
                    goal_name = excluded.goal_name,
                    goal_target_amount = excluded.goal_target_amount,
                    goal_target_date = excluded.goal_target_date,
                    current_saved_amount = excluded.current_saved_amount,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    user_id,
                    round(float(monthly_income), 2),
                    round(float(fixed_expenses), 2),
                    budgeting_goal,
                    str(goal_name_value or "").strip(),
                    round(float(goal_target_amount_value or 0), 2),
                    str(goal_target_date_value or "").strip(),
                    round(float(current_saved_amount_value or 0), 2),
                ),
            )

    def save_monthly_plan(
        self,
        user_id: int,
        month_key: str,
        monthly_income: float,
        fixed_expenses: float,
        budgeting_goal: str,
        goal_name: str | object = _MISSING,
        goal_target_amount: float | object = _MISSING,
        goal_target_date: str | object = _MISSING,
        current_saved_amount: float | object = _MISSING,
    ) -> None:
        structured_goal_provided = any(
            value is not _MISSING
            for value in (goal_name, goal_target_amount, goal_target_date, current_saved_amount)
        )
        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT goal_name, goal_target_amount, goal_target_date, current_saved_amount
                FROM monthly_plans
                WHERE user_id = ? AND month_key = ?
                """,
                (user_id, month_key),
            ).fetchone()
            if structured_goal_provided:
                goal_name_value = self._goal_field_value(goal_name, existing["goal_name"] if existing else None, "")
                goal_target_amount_value = self._goal_field_value(
                    goal_target_amount,
                    existing["goal_target_amount"] if existing else None,
                    0,
                )
                goal_target_date_value = self._goal_field_value(
                    goal_target_date,
                    existing["goal_target_date"] if existing else None,
                    "",
                )
                current_saved_amount_value = self._goal_field_value(
                    current_saved_amount,
                    existing["current_saved_amount"] if existing else None,
                    0,
                )
                budgeting_goal = self._derive_budgeting_goal(
                    budgeting_goal,
                    str(goal_name_value or ""),
                    float(goal_target_amount_value or 0),
                    str(goal_target_date_value or ""),
                    float(current_saved_amount_value or 0),
                )
            else:
                goal_name_value = existing["goal_name"] if existing else ""
                goal_target_amount_value = existing["goal_target_amount"] if existing else 0
                goal_target_date_value = existing["goal_target_date"] if existing else ""
                current_saved_amount_value = existing["current_saved_amount"] if existing else 0
                budgeting_goal = str(budgeting_goal or "").strip()
            conn.execute(
                """
                INSERT INTO monthly_plans (
                    user_id,
                    month_key,
                    monthly_income,
                    fixed_expenses,
                    budgeting_goal,
                    goal_name,
                    goal_target_amount,
                    goal_target_date,
                    current_saved_amount
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, month_key) DO UPDATE SET
                    monthly_income = excluded.monthly_income,
                    fixed_expenses = excluded.fixed_expenses,
                    budgeting_goal = excluded.budgeting_goal,
                    goal_name = excluded.goal_name,
                    goal_target_amount = excluded.goal_target_amount,
                    goal_target_date = excluded.goal_target_date,
                    current_saved_amount = excluded.current_saved_amount,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    user_id,
                    month_key,
                    round(float(monthly_income), 2),
                    round(float(fixed_expenses), 2),
                    budgeting_goal,
                    str(goal_name_value or "").strip(),
                    round(float(goal_target_amount_value or 0), 2),
                    str(goal_target_date_value or "").strip(),
                    round(float(current_saved_amount_value or 0), 2),
                ),
            )

    def save_agent_note(self, user_id: int, note_type: str, content: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_notes (user_id, note_type, content)
                VALUES (?, ?, ?)
                """,
                (user_id, note_type, content),
            )

    def replace_agent_note(self, user_id: int, note_type: str, content: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM agent_notes WHERE user_id = ? AND note_type = ?",
                (user_id, note_type),
            )
            conn.execute(
                """
                INSERT INTO agent_notes (user_id, note_type, content)
                VALUES (?, ?, ?)
                """,
                (user_id, note_type, content),
            )

    def save_monthly_summary(
        self,
        user_id: int,
        month_key: str,
        income: float,
        fixed_expenses: float,
        tracked_spending: float,
        recurring_monthly_total: float,
        leftover_money: float,
        discretionary_remaining: float,
        summary_text: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO monthly_summaries (
                    user_id,
                    month_key,
                    income,
                    fixed_expenses,
                    tracked_spending,
                    recurring_monthly_total,
                    leftover_money,
                    discretionary_remaining,
                    summary_text
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, month_key) DO UPDATE SET
                    income = excluded.income,
                    fixed_expenses = excluded.fixed_expenses,
                    tracked_spending = excluded.tracked_spending,
                    recurring_monthly_total = excluded.recurring_monthly_total,
                    leftover_money = excluded.leftover_money,
                    discretionary_remaining = excluded.discretionary_remaining,
                    summary_text = excluded.summary_text,
                    created_at = CURRENT_TIMESTAMP
                """,
                (
                    user_id,
                    month_key,
                    round(float(income), 2),
                    round(float(fixed_expenses), 2),
                    round(float(tracked_spending), 2),
                    round(float(recurring_monthly_total), 2),
                    round(float(leftover_money), 2),
                    round(float(discretionary_remaining), 2),
                    summary_text,
                ),
            )

    def get_financial_profile(self, user_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    monthly_income,
                    fixed_expenses,
                    budgeting_goal,
                    goal_name,
                    goal_target_amount,
                    goal_target_date,
                    current_saved_amount,
                    updated_at
                FROM financial_profiles
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "monthly_income": round(float(row["monthly_income"]), 2),
            "fixed_expenses": round(float(row["fixed_expenses"]), 2),
            "budgeting_goal": row["budgeting_goal"],
            "goal_name": row["goal_name"],
            "goal_target_amount": round(float(row["goal_target_amount"]), 2),
            "goal_target_date": row["goal_target_date"],
            "current_saved_amount": round(float(row["current_saved_amount"]), 2),
            "updated_at": row["updated_at"],
        }

    def get_monthly_plan(self, user_id: int, month_key: str | None) -> dict[str, Any] | None:
        if not month_key:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    month_key,
                    monthly_income,
                    fixed_expenses,
                    budgeting_goal,
                    goal_name,
                    goal_target_amount,
                    goal_target_date,
                    current_saved_amount,
                    updated_at
                FROM monthly_plans
                WHERE user_id = ? AND month_key = ?
                """,
                (user_id, month_key),
            ).fetchone()
        if not row:
            return None
        return {
            "month_key": row["month_key"],
            "month_label": self._month_label(row["month_key"]),
            "monthly_income": round(float(row["monthly_income"]), 2),
            "fixed_expenses": round(float(row["fixed_expenses"]), 2),
            "budgeting_goal": row["budgeting_goal"],
            "goal_name": row["goal_name"],
            "goal_target_amount": round(float(row["goal_target_amount"]), 2),
            "goal_target_date": row["goal_target_date"],
            "current_saved_amount": round(float(row["current_saved_amount"]), 2),
            "updated_at": row["updated_at"],
        }

    def list_monthly_plans(self, user_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    month_key,
                    monthly_income,
                    fixed_expenses,
                    budgeting_goal,
                    goal_name,
                    goal_target_amount,
                    goal_target_date,
                    current_saved_amount,
                    updated_at
                FROM monthly_plans
                WHERE user_id = ?
                ORDER BY month_key DESC
                """,
                (user_id,),
            ).fetchall()
        plans = []
        for row in rows:
            month_label = self._month_label(row["month_key"])
            plans.append(
                {
                    "month_key": row["month_key"],
                    "month_label": month_label,
                    "monthly_income": round(float(row["monthly_income"]), 2),
                    "fixed_expenses": round(float(row["fixed_expenses"]), 2),
                    "budgeting_goal": row["budgeting_goal"],
                    "goal_name": row["goal_name"],
                    "goal_target_amount": round(float(row["goal_target_amount"]), 2),
                    "goal_target_date": row["goal_target_date"],
                    "current_saved_amount": round(float(row["current_saved_amount"]), 2),
                    "updated_at": row["updated_at"],
                    "summary": (
                        f"{month_label}, monthly income of ${float(row['monthly_income']):.2f}, "
                        f"fixed expenses of ${float(row['fixed_expenses']):.2f}, "
                        f"goal is to {row['budgeting_goal'] or 'not set'}"
                    ),
                }
            )
        return plans

    def list_agent_notes(self, user_id: int, month_key: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT note_type, content, created_at
                FROM agent_notes
                WHERE user_id = ?
                  AND note_type != 'monthly_focus'
                ORDER BY id DESC
                """,
                (user_id,),
            ).fetchall()
        notes = [dict(row) for row in rows]
        if not month_key:
            return notes

        focus_note_type = f"{self._month_label(month_key)} focus"
        focused = [note for note in notes if note["note_type"] == focus_note_type]
        others = [note for note in notes if note["note_type"] != focus_note_type and " focus" not in note["note_type"]]
        return [*focused, *others]

    def get_monthly_summary(self, user_id: int, month_key: str | None = None) -> dict[str, Any] | None:
        with self._connect() as conn:
            if month_key:
                row = conn.execute(
                    """
                    SELECT month_key, income, fixed_expenses, tracked_spending, recurring_monthly_total,
                           leftover_money, discretionary_remaining, summary_text, created_at
                    FROM monthly_summaries
                    WHERE user_id = ? AND month_key = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (user_id, month_key),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT month_key, income, fixed_expenses, tracked_spending, recurring_monthly_total,
                           leftover_money, discretionary_remaining, summary_text, created_at
                    FROM monthly_summaries
                    WHERE user_id = ?
                    ORDER BY month_key DESC, id DESC
                    LIMIT 1
                    """,
                    (user_id,),
                ).fetchone()
        if not row:
            return None
        return {
            "month_key": row["month_key"],
            "month_label": self._month_label(row["month_key"]),
            "income": round(float(row["income"]), 2),
            "fixed_expenses": round(float(row["fixed_expenses"]), 2),
            "tracked_spending": round(float(row["tracked_spending"]), 2),
            "recurring_monthly_total": round(float(row["recurring_monthly_total"]), 2),
            "available_before_fixed": round(float(row["income"]) - float(row["tracked_spending"]), 2),
            "leftover_money": round(float(row["leftover_money"]), 2),
            "discretionary_remaining": round(float(row["discretionary_remaining"]), 2),
            "summary_text": row["summary_text"],
            "created_at": row["created_at"],
        }

    def get_dashboard_data(self, user_id: int, month_key: str | None = None) -> dict[str, Any]:
        with self._connect() as conn:
            transaction_rows = conn.execute(
                """
                SELECT date, description, amount, category, source
                FROM transactions
                WHERE user_id = ?
                ORDER BY date DESC, id DESC
                """,
                (user_id,),
            ).fetchall()

        transactions = [
            {
                "date": row["date"],
                "description": row["description"],
                "amount": round(float(row["amount"]), 2),
                "category": row["category"],
                "source": row["source"],
            }
            for row in transaction_rows
        ]
        available_months = sorted({self._month_key_from_date(item["date"]) for item in transactions}, reverse=True)
        selected_month = month_key if month_key in available_months else (available_months[0] if available_months else None)
        selected_transactions = [
            transaction
            for transaction in transactions
            if selected_month is None or self._month_key_from_date(transaction["date"]) == selected_month
        ]

        monthly_plan = self.get_monthly_plan(user_id, selected_month)
        financial_profile = monthly_plan or self.get_financial_profile(user_id)
        category_totals = self._category_totals(selected_transactions)
        total_spent = round(sum(item["amount"] for item in selected_transactions), 2)
        category_breakdown = self._category_breakdown(
            category_totals=category_totals,
            total_spent=total_spent,
            transactions=transactions,
            selected_month=selected_month,
            monthly_income=float((financial_profile or {}).get("monthly_income") or 0),
        )
        recurring_expenses = self._annotate_subscription_recommendations(self._recurring_expenses(transactions))
        monthly_recurring_total = round(sum(item["monthly_equivalent"] for item in recurring_expenses), 2)

        monthly_summary = self.get_monthly_summary(user_id, selected_month)
        receipt_behavior_notes = self.list_receipt_behavior_insights(user_id, selected_month) if selected_month else []
        return {
            "transaction_count": len(selected_transactions),
            "total_spent": total_spent,
            "category_totals": category_totals,
            "category_breakdown": category_breakdown,
            "recent_transactions": selected_transactions[:12],
            "transactions": selected_transactions,
            "all_transactions": transactions,
            "available_months": [
                {"key": key, "label": self._month_label(key)}
                for key in available_months
            ],
            "selected_month": selected_month,
            "selected_month_label": self._month_label(selected_month) if selected_month else None,
            "subscriptions": recurring_expenses,
            "monthly_recurring_total": monthly_recurring_total,
            "messages": self.list_chat_messages(user_id),
            "subscription_decisions": self.list_subscription_decisions(user_id),
            "user_decisions": self.list_user_decisions(user_id),
            "pending_action": self.get_pending_action(user_id),
            "financial_profile": financial_profile,
            "monthly_plan_history": self.list_monthly_plans(user_id),
            "agent_notes": self.list_agent_notes(user_id, selected_month),
            "monthly_summary": monthly_summary,
            "goal_summary": self._goal_summary_from_profile(financial_profile),
            "spending_profile": self._spending_profile(
                category_breakdown=category_breakdown,
                monthly_summary=monthly_summary,
                financial_profile=financial_profile,
                recurring_expenses=recurring_expenses,
                transactions=transactions,
                selected_month=selected_month,
            ),
            "top_insights": self._top_insights(
                transactions=transactions,
                selected_month=selected_month,
                category_totals=category_totals,
                recurring_expenses=recurring_expenses,
                monthly_summary=monthly_summary,
                financial_profile=financial_profile,
                receipt_notes=receipt_behavior_notes,
            ),
            "behavioral_insights": self._behavioral_insights(
                transactions=transactions,
                selected_month=selected_month,
                monthly_income=float((financial_profile or {}).get("monthly_income") or 0),
            ),
            "recommended_actions": self._recommended_actions(
                category_totals=category_totals,
                recurring_expenses=recurring_expenses,
                monthly_summary=monthly_summary,
                financial_profile=financial_profile,
            ),
            "pending_receipts": self.list_pending_receipt_extractions(user_id),
        }

    @staticmethod
    def _build_goal_summary(
        goal_name: str,
        goal_target_amount: float,
        goal_target_date: str,
        current_saved_amount: float,
    ) -> str:
        if not goal_name and goal_target_amount <= 0 and not goal_target_date and current_saved_amount <= 0:
            return ""

        parts: list[str] = []
        if goal_name:
            parts.append(goal_name)
        if goal_target_amount > 0:
            parts.append(f"${goal_target_amount:,.0f}")
        if goal_target_date:
            parts.append(f"by {goal_target_date}")
        if current_saved_amount > 0:
            parts.append(f"saved so far: ${current_saved_amount:,.0f}")
        return " | ".join(parts)

    @classmethod
    def _goal_summary_from_profile(cls, financial_profile: dict[str, Any] | None) -> str:
        if not financial_profile:
            return ""
        return cls._build_goal_summary(
            str(financial_profile.get("goal_name") or "").strip(),
            float(financial_profile.get("goal_target_amount") or 0),
            str(financial_profile.get("goal_target_date") or "").strip(),
            float(financial_profile.get("current_saved_amount") or 0),
        )

    @staticmethod
    def _discretionary_categories() -> set[str]:
        return {
            "Dining",
            "Shopping",
            "Entertainment",
            "Travel",
            "Coffee",
            "Rideshare",
            "Delivery",
            "Subscriptions",
            "Other",
        }

    @classmethod
    def _is_discretionary_category(cls, category: str) -> bool:
        normalized = str(category or "").strip().lower()
        return normalized in {
            "dining",
            "shopping",
            "entertainment",
            "travel",
            "coffee",
            "rideshare",
            "delivery",
            "subscriptions",
            "other",
        }

    @staticmethod
    def _discretionary_cap_ratio(category: str) -> float:
        normalized = str(category or "").strip().lower()
        caps = BudgetRecommender.default_target_max_ratio()
        cap_key_map = {
            "dining": "Dining",
            "shopping": "Shopping",
            "entertainment": "Entertainment",
            "travel": "Travel",
            "subscriptions": "Subscriptions",
            "rideshare": "Transportation",
        }
        cap_key = cap_key_map.get(normalized)
        if cap_key:
            ratio = caps.get(cap_key)
            if ratio is not None:
                return float(ratio)
        if normalized in {"coffee", "delivery", "other"}:
            return 0.05
        return 0.05

    @classmethod
    def _discretionary_cap_amount(cls, category: str, monthly_income: float) -> float:
        if monthly_income <= 0:
            return 0.0
        return round(monthly_income * cls._discretionary_cap_ratio(category), 2)

    @classmethod
    def _current_discretionary_spend(cls, category_breakdown: list[dict[str, Any]]) -> float:
        return round(
            sum(
                float(item.get("amount") or 0)
                for item in category_breakdown
                if cls._is_discretionary_category(str(item.get("category") or ""))
            ),
            2,
        )

    @classmethod
    def _discretionary_cap_total(
        cls,
        category_breakdown: list[dict[str, Any]],
        financial_profile: dict[str, Any] | None,
    ) -> float:
        monthly_income = float((financial_profile or {}).get("monthly_income") or 0)
        if monthly_income <= 0:
            return 0.0

        relevant_categories = [
            str(item.get("category") or "")
            for item in category_breakdown
            if cls._is_discretionary_category(str(item.get("category") or ""))
        ]
        if not relevant_categories:
            return 0.0

        total_cap = sum(cls._discretionary_cap_amount(category, monthly_income) for category in relevant_categories)
        return round(total_cap, 2)

    @classmethod
    def _top_discretionary_category(
        cls,
        category_breakdown: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        for item in category_breakdown:
            if cls._is_discretionary_category(str(item.get("category") or "")):
                return item
        return None

    @staticmethod
    def _goal_pace_context(
        financial_profile: dict[str, Any] | None,
        monthly_summary: dict[str, Any] | None,
        transactions: list[dict[str, Any]],
        selected_month: str | None,
    ) -> dict[str, Any]:
        if not financial_profile:
            return {"status": None, "effective_discretionary_remaining": None}

        goal_name = str(financial_profile.get("goal_name") or "").strip()
        goal_target_amount = float(financial_profile.get("goal_target_amount") or 0)
        goal_target_date = str(financial_profile.get("goal_target_date") or "").strip()
        current_saved_amount = float(financial_profile.get("current_saved_amount") or 0)
        if not goal_name and goal_target_amount <= 0 and not goal_target_date and current_saved_amount <= 0:
            return {"status": None, "effective_discretionary_remaining": None}
        if goal_target_amount <= 0 or not goal_target_date:
            return {"status": None, "effective_discretionary_remaining": None}

        remaining_goal = max(0.0, goal_target_amount - current_saved_amount)
        if remaining_goal <= 0:
            return {"status": "on_track", "effective_discretionary_remaining": None}

        pace_month_key = selected_month or (Storage._month_key_from_date(transactions[0]["date"]) if transactions else None)
        if not pace_month_key and monthly_summary:
            pace_month_key = str(monthly_summary.get("month_key") or "").strip() or None
        if not pace_month_key:
            return {"status": None, "effective_discretionary_remaining": None}

        current_month_spend = round(
            sum(
                float(transaction.get("amount") or 0)
                for transaction in transactions
                if Storage._month_key_from_date(str(transaction.get("date") or "")) == pace_month_key
            ),
            2,
        )
        proxy_discretionary_remaining = None
        monthly_income = float(financial_profile.get("monthly_income") or 0)
        fixed_expenses = float(financial_profile.get("fixed_expenses") or 0)
        if monthly_income > 0:
            proxy_discretionary_remaining = round(monthly_income - fixed_expenses - current_month_spend, 2)

        summary_discretionary_remaining = None
        if monthly_summary:
            raw_discretionary_remaining = monthly_summary.get("discretionary_remaining")
            if raw_discretionary_remaining not in (None, ""):
                try:
                    summary_discretionary_remaining = float(raw_discretionary_remaining)
                except (TypeError, ValueError):
                    summary_discretionary_remaining = None
        discretionary_remaining = summary_discretionary_remaining
        if discretionary_remaining is None:
            discretionary_remaining = proxy_discretionary_remaining
        if discretionary_remaining is None:
            return {"status": None, "effective_discretionary_remaining": None}

        try:
            pace_month = datetime.strptime(f"{pace_month_key}-01", "%Y-%m-%d").date()
            target_date = datetime.strptime(goal_target_date, "%Y-%m-%d").date()
        except ValueError:
            return {"status": None, "effective_discretionary_remaining": discretionary_remaining}

        if target_date <= pace_month:
            return {"status": "behind", "effective_discretionary_remaining": discretionary_remaining}

        days_remaining = (target_date - pace_month).days
        months_remaining = days_remaining / 30.4375
        required_monthly_saving = remaining_goal / months_remaining
        if discretionary_remaining < required_monthly_saving:
            return {"status": "behind", "effective_discretionary_remaining": discretionary_remaining}

        return {"status": "on_track", "effective_discretionary_remaining": discretionary_remaining}

    @classmethod
    def _spending_profile(
        cls,
        category_breakdown: list[dict[str, Any]],
        monthly_summary: dict[str, Any] | None,
        financial_profile: dict[str, Any] | None,
        recurring_expenses: list[dict[str, Any]],
        transactions: list[dict[str, Any]],
        selected_month: str | None,
    ) -> dict[str, Any]:
        goal_summary = cls._goal_summary_from_profile(financial_profile)
        current_discretionary_spend = cls._current_discretionary_spend(category_breakdown)
        discretionary_cap_total = cls._discretionary_cap_total(category_breakdown, financial_profile)
        goal_pace_context = cls._goal_pace_context(financial_profile, monthly_summary, transactions, selected_month)
        goal_pace_status = goal_pace_context["status"]
        effective_discretionary_remaining = goal_pace_context["effective_discretionary_remaining"]
        top_discretionary_category = cls._top_discretionary_category(category_breakdown)
        top_category_name = str(top_discretionary_category.get("category") or "") if top_discretionary_category else ""
        top_category_amount = float(top_discretionary_category.get("amount") or 0) if top_discretionary_category else 0.0
        discretionary_remaining = float((monthly_summary or {}).get("discretionary_remaining") or 0)
        recurring_monthly_total = round(sum(float(item.get("monthly_equivalent") or 0) for item in recurring_expenses), 2)
        recurring_note = None
        if recurring_expenses:
            recurring_note = f"You have {len(recurring_expenses)} recurring subscriptions totaling ${recurring_monthly_total:,.2f}/month."
        elif monthly_summary and float(monthly_summary.get("recurring_monthly_total") or 0) > 0:
            recurring_note = (
                f"Your current summary includes ${float(monthly_summary.get('recurring_monthly_total') or 0):,.2f}/month in recurring charges."
            )

        if goal_pace_status == "behind":
            return {
                "name": "Goal-Focused but Behind",
                "description": "You have a clear goal, but your current spending pace may delay your progress.",
                "reasons": [
                    f"Goal summary: {goal_summary}.",
                    f"Current month spending leaves about ${effective_discretionary_remaining if effective_discretionary_remaining is not None else discretionary_remaining:,.2f} of discretionary room.",
                    *( [recurring_note] if recurring_note else [] ),
                    "The remaining goal balance needs more room than this month is currently leaving available.",
                ],
                "why_this": "Based on your current month transactions and structured goal data.",
            }

        if discretionary_cap_total > 0 and current_discretionary_spend > discretionary_cap_total * 1.25:
            return {
                "name": "Reactive Spender",
                "description": "You tend to overspend in discretionary categories, especially when expenses are not actively tracked.",
                "reasons": [
                    f"Current discretionary spending is ${current_discretionary_spend:,.2f} against a cap model of ${discretionary_cap_total:,.2f}.",
                    "That is more than 25% above the discretionary cap model.",
                    *( [recurring_note] if recurring_note else [] ),
                    "The current month is already under pressure, so spending is reacting to the balance left.",
                ],
                "why_this": "Based on your current month transactions and the discretionary cap model.",
            }

        if discretionary_cap_total > 0 and discretionary_cap_total * 0.9 <= current_discretionary_spend <= discretionary_cap_total * 1.1:
            top_category_cap = cls._discretionary_cap_amount(
                top_category_name,
                float((financial_profile or {}).get("monthly_income") or 0),
            )
            top_category_utilization = (
                round((top_category_amount / top_category_cap) * 100, 0)
                if top_category_cap > 0 and top_category_amount > 0
                else 0.0
            )
            return {
                "name": "Budget Optimizer",
                "description": "You are generally staying within budget and may benefit most from small optimizations.",
                "reasons": [
                    f"Your top discretionary category is {top_category_name or 'unknown'} at {top_category_utilization:.0f}% of its own cap.",
                    *( [recurring_note] if recurring_note else [] ),
                    f"Current discretionary spending is ${current_discretionary_spend:,.2f}, which stays within the +/-10% optimizer window around the cap model.",
                    "You are close enough to budget targets that small adjustments can matter.",
                ],
                "why_this": "Based on your current month transactions and the discretionary cap model.",
            }

        return {
            "name": "Flexible Spender",
            "description": "Your spending patterns are mixed, with room for more consistent planning.",
            "reasons": [
                "Current month spending is outside the tighter optimizer window but not far enough above the cap model to be reactive.",
                *( [recurring_note] if recurring_note else [] ),
                "There is room to make spending more consistent without immediate pressure.",
                "This month is still adaptable, but not especially tuned.",
            ],
            "why_this": "Based on your current month transactions and the discretionary cap model.",
        }

    @staticmethod
    def _category_totals(transactions: list[dict[str, Any]]) -> dict[str, float]:
        totals: dict[str, float] = defaultdict(float)
        for transaction in transactions:
            totals[transaction["category"]] += float(transaction["amount"])
        return dict(
            sorted(
                ((category, round(amount, 2)) for category, amount in totals.items()),
                key=lambda item: item[1],
                reverse=True,
            )
        )

    @staticmethod
    def _category_breakdown(
        category_totals: dict[str, float],
        total_spent: float,
        transactions: list[dict[str, Any]],
        selected_month: str | None,
        monthly_income: float,
    ) -> list[dict[str, float | str]]:
        if total_spent <= 0:
            return []

        ordered = list(category_totals.items())
        budget_caps = BudgetRecommender.default_target_max_ratio()
        previous_month_key = None
        if selected_month:
            available_months = sorted({str(item["date"])[:7] for item in transactions})
            try:
                index = available_months.index(selected_month)
                if index > 0:
                    previous_month_key = available_months[index - 1]
            except ValueError:
                previous_month_key = None

        previous_category_totals: dict[str, float] = defaultdict(float)
        if previous_month_key:
            for transaction in transactions:
                if str(transaction["date"])[:7] == previous_month_key:
                    previous_category_totals[transaction["category"]] += float(transaction["amount"])

        breakdown = []
        for index, (category, amount) in enumerate(ordered):
            percentage = round((amount / total_spent) * 100, 2)
            budget_ratio = budget_caps.get(category)
            budget_amount = round(monthly_income * budget_ratio, 2) if budget_ratio and monthly_income > 0 else None
            budget_pct = round((amount / budget_amount) * 100, 0) if budget_amount else None
            budget_status = (
                "OVER budget" if budget_amount is not None and amount > budget_amount else "within budget"
            ) if budget_amount is not None else "no budget cap"

            last_month_amount = round(previous_category_totals.get(category, 0.0), 2) if previous_month_key else None
            if last_month_amount and last_month_amount > 0:
                delta_pct = round(((amount - last_month_amount) / last_month_amount) * 100, 0)
                trend_prefix = "↑" if delta_pct >= 0 else "↓"
                last_month_text = f"{trend_prefix}{abs(int(delta_pct))}% vs last month"
            elif previous_month_key:
                last_month_text = "new vs last month"
            else:
                last_month_text = "no last-month comparison"

            if budget_amount is not None:
                budget_text = f"{int(budget_pct)}% vs budget" if budget_pct is not None else "vs budget unavailable"
            else:
                budget_text = "no budget cap"

            if index == 0 and budget_status == "OVER budget":
                shoutout = "This is your biggest leak this month."
            elif budget_status == "OVER budget":
                shoutout = "This category is over budget."
            elif percentage >= 20:
                shoutout = "This is still taking a noticeable bite this month."
            else:
                shoutout = "This is a smaller category, not the main problem."
            breakdown.append(
                {
                    "category": category,
                    "amount": round(amount, 2),
                    "percentage": percentage,
                    "budget_text": budget_text,
                    "last_month_text": last_month_text,
                    "budget_status": budget_status,
                    "overspending": budget_status == "OVER budget",
                    "shoutout": shoutout,
                    "tooltip": f"{category}: ${amount:.2f} ({last_month_text}, {budget_status}). {budget_text}. {shoutout}",
                }
            )
        return breakdown

    @staticmethod
    def _recurring_expenses(transactions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        analyzer = RecurringExpenseAnalyzer()
        modeled = [
            CategorizedTransaction(
                date=item["date"],
                description=item["description"],
                amount=float(item["amount"]),
                category=item["category"],
            )
            for item in transactions
        ]
        return [expense.to_dict() for expense in analyzer.analyze(modeled)]

    @staticmethod
    def _annotate_subscription_recommendations(recurring_expenses: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not recurring_expenses:
            return []

        annotated = [dict(item) for item in recurring_expenses]
        eligible_categories = {"subscriptions", "entertainment", "wellness", "fitness"}
        recommended_index = None
        for index, item in enumerate(annotated):
            category = str(item.get("category") or "").strip().lower()
            if category not in eligible_categories:
                continue
            if recommended_index is None or float(item.get("monthly_equivalent") or 0) > float(
                annotated[recommended_index].get("monthly_equivalent") or 0
            ):
                recommended_index = index

        if recommended_index is None:
            recommended_index = 0
        annotated[recommended_index]["recommended_to_cancel"] = True
        return annotated

    def _top_insights(
        self,
        transactions: list[dict[str, Any]],
        selected_month: str | None,
        category_totals: dict[str, float],
        recurring_expenses: list[dict[str, Any]],
        monthly_summary: dict[str, Any] | None,
        financial_profile: dict[str, Any] | None,
        receipt_notes: list[str] | None = None,
    ) -> list[str]:
        insights: list[str] = []
        if selected_month and category_totals:
            category_insight = self._category_average_insight(transactions, selected_month, category_totals)
            if category_insight:
                insights.append(category_insight)

        if recurring_expenses:
            recurring_total = round(sum(float(item.get("monthly_equivalent") or 0) for item in recurring_expenses), 2)
            insights.append(
                f"You have {len(recurring_expenses)} recurring subscriptions totaling ${recurring_total:.2f}/month."
            )

        goal_insight = self._goal_pacing_insight(monthly_summary, financial_profile)
        if goal_insight:
            insights.append(goal_insight)

        if receipt_notes:
            for note in receipt_notes:
                if note not in insights:
                    insights.append(note)

        if monthly_summary and len(insights) < 3:
            leftover_money = float(monthly_summary.get("leftover_money") or 0)
            month_label = monthly_summary.get("month_label") or "this month"
            if leftover_money < 0:
                insights.append(f"You are ${abs(leftover_money):.2f} over for {month_label} after fixed expenses.")
            else:
                insights.append(f"You still have ${leftover_money:.2f} left in {month_label} after fixed expenses.")

        return insights[:3]

    def _recommended_actions(
        self,
        category_totals: dict[str, float],
        recurring_expenses: list[dict[str, Any]],
        monthly_summary: dict[str, Any] | None,
        financial_profile: dict[str, Any] | None,
    ) -> list[str]:
        actions: list[str] = []
        goal_text = str((financial_profile or {}).get("budgeting_goal") or "").strip()

        if category_totals:
            top_category, amount = next(iter(category_totals.items()))
            reduction = max(25, round(amount * 0.33 / 5) * 5)
            if goal_text:
                actions.append(f"Reduce {top_category} by ${int(reduction)}/month to build more room for your goal.")
            else:
                actions.append(f"Reduce {top_category} by ${int(reduction)}/month to stop the biggest leak first.")

        if recurring_expenses:
            sorted_subs = sorted(
                recurring_expenses,
                key=lambda item: float(item.get("monthly_equivalent") or 0),
                reverse=True,
            )
            sub_count = min(2, len(sorted_subs))
            savings = round(sum(float(item.get("monthly_equivalent") or 0) for item in sorted_subs[:sub_count]), 2)
            label = "subscription" if sub_count == 1 else "subscriptions"
            actions.append(f"Cancel {sub_count} {label} -> save ${savings:.2f}/month.")

        if monthly_summary:
            available_before_fixed = float(monthly_summary.get("available_before_fixed") or 0)
            fixed_expenses = float(monthly_summary.get("fixed_expenses") or 0)
            weekly_cap = max(0, round((available_before_fixed - fixed_expenses) / 4 / 10) * 10)
            actions.append(f"Set weekly discretionary cap to ${int(weekly_cap)}/week.")

        return actions[:3]

    def _behavioral_insights(
        self,
        transactions: list[dict[str, Any]],
        selected_month: str | None,
        monthly_income: float,
    ) -> list[str]:
        if not selected_month:
            return []

        selected_transactions = [
            transaction for transaction in transactions if str(transaction["date"])[:7] == selected_month
        ]
        if not selected_transactions:
            return []

        insights: list[str] = []
        weekend_total = 0.0
        weekday_total = 0.0
        weekend_days: set[str] = set()
        weekday_days: set[str] = set()
        for transaction in selected_transactions:
            day = datetime.strptime(str(transaction["date"]), "%Y-%m-%d").weekday()
            if day >= 5:
                weekend_total += float(transaction["amount"])
                weekend_days.add(str(transaction["date"]))
            else:
                weekday_total += float(transaction["amount"])
                weekday_days.add(str(transaction["date"]))
        if weekend_days and weekday_days:
            weekend_avg = weekend_total / len(weekend_days)
            weekday_avg = weekday_total / len(weekday_days)
            if weekday_avg > 0 and weekend_avg > weekday_avg * 1.2:
                pct = round(((weekend_avg - weekday_avg) / weekday_avg) * 100)
                insights.append(f"You overspend on weekends (+{int(pct)}%).")

        travel_dates = [
            datetime.strptime(str(transaction["date"]), "%Y-%m-%d")
            for transaction in selected_transactions
            if str(transaction.get("category") or "").lower() == "travel"
        ]
        if travel_dates:
            post_travel_total = 0.0
            for transaction in selected_transactions:
                transaction_date = datetime.strptime(str(transaction["date"]), "%Y-%m-%d")
                if any(0 < (transaction_date - travel_date).days <= 3 for travel_date in travel_dates):
                    if str(transaction.get("category") or "").lower() != "travel":
                        post_travel_total += float(transaction["amount"])
            if post_travel_total >= 50:
                insights.append("Spending spikes occur after travel.")

        if monthly_income > 0:
            dining_budget = monthly_income * BudgetRecommender.default_target_max_ratio().get("Dining", 0.12)
            week3_dining = sum(
                float(transaction["amount"])
                for transaction in selected_transactions
                if str(transaction.get("category") or "").lower() == "dining"
                and 15 <= int(str(transaction["date"])[8:10]) <= 21
            )
            if week3_dining > dining_budget:
                insights.append("You consistently exceed dining budget in week 3.")

        return insights[:3]

    def _category_average_insight(
        self,
        transactions: list[dict[str, Any]],
        selected_month: str,
        category_totals: dict[str, float],
    ) -> str | None:
        top_category, current_amount = next(iter(category_totals.items()))
        monthly_totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for transaction in transactions:
            month = self._month_key_from_date(transaction["date"])
            monthly_totals[month][transaction["category"]] += float(transaction["amount"])

        prior_months = sorted(month for month in monthly_totals.keys() if month < selected_month)[-3:]
        if not prior_months:
            return None

        prior_amounts = [monthly_totals[month].get(top_category, 0.0) for month in prior_months]
        average = sum(prior_amounts) / len(prior_amounts)
        if average <= 0:
            return None

        change_pct = round(((current_amount - average) / average) * 100, 0)
        direction = "more" if change_pct >= 0 else "less"
        return (
            f"You are spending {abs(int(change_pct))}% {direction} on {top_category} compared to your 3-month average."
        )

    @staticmethod
    def _goal_pacing_insight(monthly_summary: dict[str, Any] | None, financial_profile: dict[str, Any] | None) -> str | None:
        if not monthly_summary or not financial_profile:
            return None

        goal_text = str(financial_profile.get("budgeting_goal") or "").strip()
        if not goal_text:
            return None

        match = re.search(r"(\d[\d,]*(?:\.\d{1,2})?)", goal_text)
        if not match:
            return None

        target_amount = float(match.group(1).replace(",", ""))
        leftover_money = float(monthly_summary.get("leftover_money") or 0)
        if leftover_money <= 0:
            return f"At your current pace, reaching your ${target_amount:,.0f} goal will keep slipping unless you free up room this month."

        months_needed = target_amount / leftover_money
        if months_needed < 1:
            weeks_needed = max(1, round(months_needed * 4.345))
            return f"At your current pace, you can reach your ${target_amount:,.0f} goal in about {weeks_needed} weeks."
        return f"At your current pace, reaching your ${target_amount:,.0f} goal will take about {months_needed:.1f} months."
