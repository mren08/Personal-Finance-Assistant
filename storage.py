from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from typing import Any

from csv_parser import CategorizedTransaction, StatementCsvParser
from recurrence import RecurringExpenseAnalyzer


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
                """
            )

    @staticmethod
    def _hash_password(password: str) -> str:
        return hashlib.sha256(password.encode("utf-8")).hexdigest()

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

    def authenticate_user(self, email: str, password: str) -> int | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, password_hash FROM users WHERE email = ?",
                (email.strip().lower(),),
            ).fetchone()
        if not row or row["password_hash"] != self._hash_password(password):
            return None
        return int(row["id"])

    def get_user(self, user_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT id, email FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return None
        return {"id": int(row["id"]), "email": row["email"]}

    def add_transactions(self, user_id: int, transactions: list[dict[str, Any]]) -> None:
        if not transactions:
            return

        rows = [
            (
                user_id,
                item["date"],
                item["description"],
                round(float(item["amount"]), 2),
                item["category"],
                item["source"],
            )
            for item in transactions
        ]
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO transactions (user_id, date, description, amount, category, source)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

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
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO financial_profiles (user_id, monthly_income, fixed_expenses, budgeting_goal)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    monthly_income = excluded.monthly_income,
                    fixed_expenses = excluded.fixed_expenses,
                    budgeting_goal = excluded.budgeting_goal,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (user_id, round(float(monthly_income), 2), round(float(fixed_expenses), 2), budgeting_goal),
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
                SELECT monthly_income, fixed_expenses, budgeting_goal, updated_at
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
            "updated_at": row["updated_at"],
        }

    def list_agent_notes(self, user_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT note_type, content, created_at
                FROM agent_notes
                WHERE user_id = ?
                ORDER BY id DESC
                """,
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_latest_monthly_summary(self, user_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
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
            "income": round(float(row["income"]), 2),
            "fixed_expenses": round(float(row["fixed_expenses"]), 2),
            "tracked_spending": round(float(row["tracked_spending"]), 2),
            "recurring_monthly_total": round(float(row["recurring_monthly_total"]), 2),
            "leftover_money": round(float(row["leftover_money"]), 2),
            "discretionary_remaining": round(float(row["discretionary_remaining"]), 2),
            "summary_text": row["summary_text"],
            "created_at": row["created_at"],
        }

    def get_dashboard_data(self, user_id: int) -> dict[str, Any]:
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
        category_totals = self._category_totals(transactions)
        recurring_expenses = self._recurring_expenses(transactions)
        monthly_recurring_total = round(sum(item["monthly_equivalent"] for item in recurring_expenses), 2)

        return {
            "transaction_count": len(transactions),
            "total_spent": round(sum(item["amount"] for item in transactions), 2),
            "category_totals": category_totals,
            "recent_transactions": transactions[:12],
            "transactions": transactions,
            "subscriptions": recurring_expenses,
            "monthly_recurring_total": monthly_recurring_total,
            "messages": self.list_chat_messages(user_id),
            "subscription_decisions": self.list_subscription_decisions(user_id),
            "pending_action": self.get_pending_action(user_id),
            "financial_profile": self.get_financial_profile(user_id),
            "agent_notes": self.list_agent_notes(user_id),
            "monthly_summary": self.get_latest_monthly_summary(user_id),
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
