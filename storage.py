from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime
from typing import Any

from csv_parser import CategorizedTransaction, StatementCsvParser
from recurrence import RecurringExpenseAnalyzer
from recommender import BudgetRecommender


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

    def save_monthly_plan(
        self,
        user_id: int,
        month_key: str,
        monthly_income: float,
        fixed_expenses: float,
        budgeting_goal: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO monthly_plans (user_id, month_key, monthly_income, fixed_expenses, budgeting_goal)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, month_key) DO UPDATE SET
                    monthly_income = excluded.monthly_income,
                    fixed_expenses = excluded.fixed_expenses,
                    budgeting_goal = excluded.budgeting_goal,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    user_id,
                    month_key,
                    round(float(monthly_income), 2),
                    round(float(fixed_expenses), 2),
                    budgeting_goal,
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

    def get_monthly_plan(self, user_id: int, month_key: str | None) -> dict[str, Any] | None:
        if not month_key:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT month_key, monthly_income, fixed_expenses, budgeting_goal, updated_at
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
            "updated_at": row["updated_at"],
        }

    def list_monthly_plans(self, user_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT month_key, monthly_income, fixed_expenses, budgeting_goal, updated_at
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
        recurring_expenses = self._recurring_expenses(transactions)
        monthly_recurring_total = round(sum(item["monthly_equivalent"] for item in recurring_expenses), 2)

        monthly_summary = self.get_monthly_summary(user_id, selected_month)
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
            "top_insights": self._top_insights(
                transactions=transactions,
                selected_month=selected_month,
                category_totals=category_totals,
                recurring_expenses=recurring_expenses,
                monthly_summary=monthly_summary,
                financial_profile=financial_profile,
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

    def _top_insights(
        self,
        transactions: list[dict[str, Any]],
        selected_month: str | None,
        category_totals: dict[str, float],
        recurring_expenses: list[dict[str, Any]],
        monthly_summary: dict[str, Any] | None,
        financial_profile: dict[str, Any] | None,
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
