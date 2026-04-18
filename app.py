from __future__ import annotations

import json
import os
import tempfile
from collections import defaultdict
from datetime import datetime

from flask import Flask, current_app, jsonify, redirect, render_template, request, session, url_for

from coach import OverspendingCoach
from csv_parser import CategorizedTransaction, StatementCsvParser
from recurrence import RecurringExpenseAnalyzer
from recommender import BudgetRecommender
from storage import Storage

def _parse_float(form, field: str, default: float = 0.0) -> float:
    raw = form.get(field, str(default)).strip()
    if raw == "":
        return default
    return float(raw)


def _parse_int(form, field: str, default: int = 0) -> int:
    raw = form.get(field, str(default)).strip()
    if raw == "":
        return default
    return int(raw)


def _parse_history_bundle(
    parser: StatementCsvParser,
    files,
) -> tuple[list[CategorizedTransaction], dict[str, float]]:
    history_transactions: list[CategorizedTransaction] = []
    history_totals = defaultdict(float)
    valid_file_count = 0

    for history_file in files:
        if not history_file or not history_file.filename:
            continue
        if not history_file.filename.lower().endswith(".csv"):
            continue

        with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
            history_file.save(tmp.name)
            temp_path = tmp.name

        try:
            parsed = parser.parse(temp_path)
            if not parsed:
                continue
            history_transactions.extend(parsed)
            valid_file_count += 1
            for category, amount in parser.category_totals(parsed).items():
                history_totals[category] += amount
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    if valid_file_count == 0:
        return history_transactions, {}

    history_averages = {cat: round(total / valid_file_count, 2) for cat, total in history_totals.items()}
    return history_transactions, history_averages


def _parse_manual_expenses(raw_json: str) -> list[CategorizedTransaction]:
    if not raw_json or not raw_json.strip():
        return []

    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError:
        raise ValueError("Manual expenses payload is invalid JSON.")

    if not isinstance(payload, list):
        raise ValueError("Manual expenses payload must be a list.")

    manual_rows: list[CategorizedTransaction] = []
    for idx, row in enumerate(payload, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"Manual expense #{idx} is malformed.")

        date_value = str(row.get("date", "")).strip()
        description = str(row.get("description", "")).strip() or "Manual Expense"
        category = str(row.get("category", "")).strip() or "Other"
        amount_raw = row.get("amount", 0)

        try:
            datetime.strptime(date_value, "%Y-%m-%d")
        except ValueError:
            raise ValueError(f"Manual expense #{idx} has invalid date. Use YYYY-MM-DD.")

        try:
            amount = float(amount_raw)
        except (TypeError, ValueError):
            raise ValueError(f"Manual expense #{idx} has invalid amount.")

        if amount <= 0:
            raise ValueError(f"Manual expense #{idx} amount must be greater than 0.")

        manual_rows.append(
            CategorizedTransaction(
                date=date_value,
                description=description,
                amount=round(amount, 2),
                category=category,
            )
        )

    return manual_rows


def _parse_budget_caps(raw_json: str, defaults: dict[str, float]) -> dict[str, float]:
    if not raw_json or not raw_json.strip():
        return dict(defaults)

    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError:
        raise ValueError("Budget caps payload is invalid JSON.")

    if not isinstance(payload, dict):
        raise ValueError("Budget caps payload must be an object.")

    caps: dict[str, float] = {}
    for category, raw_ratio in payload.items():
        category_name = str(category).strip()
        if not category_name:
            continue
        try:
            ratio = float(raw_ratio)
        except (TypeError, ValueError):
            raise ValueError(f"Budget cap for '{category_name}' must be numeric.")
        if ratio <= 0 or ratio > 1:
            raise ValueError(f"Budget cap for '{category_name}' must be between 0 and 1.")
        caps[category_name] = round(ratio, 4)

    if not caps:
        return dict(defaults)
    return caps


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.getenv("SECRET_KEY", "dev-secret")
    app.config["storage"] = Storage(os.getenv("APP_DB_PATH", "budget_app.db"))
    app.config["coach"] = OverspendingCoach()

    def get_storage() -> Storage:
        return current_app.config["storage"]

    def get_coach() -> OverspendingCoach:
        return current_app.config["coach"]

    def current_user_id() -> int | None:
        raw = session.get("user_id")
        return int(raw) if raw is not None else None

    def require_user_id() -> int:
        user_id = current_user_id()
        if user_id is None:
            raise PermissionError("Sign in first.")
        return user_id

    @app.route("/")
    def index():
        user_id = current_user_id()
        profile = None
        user = None
        if user_id is not None:
            user = get_storage().get_user(user_id)
            if user:
                profile = get_storage().get_dashboard_data(user_id)
            else:
                session.pop("user_id", None)

        return render_template(
            "index.html",
            default_budget_caps=BudgetRecommender.default_target_max_ratio(),
            logged_in=profile is not None,
            user=user,
            profile=profile,
        )

    @app.route("/healthz")
    def healthcheck():
        return jsonify({"status": "ok"}), 200

    @app.route("/signup", methods=["POST"])
    def signup():
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        try:
            user_id = get_storage().create_user(email, password)
        except ValueError as exc:
            return str(exc), 400
        session["user_id"] = user_id
        return redirect(url_for("index"))

    @app.route("/login", methods=["POST"])
    def login():
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        user_id = get_storage().authenticate_user(email, password)
        if user_id is None:
            return "Invalid email or password.", 401
        session["user_id"] = user_id
        return redirect(url_for("index"))

    @app.route("/logout", methods=["POST"])
    def logout():
        session.pop("user_id", None)
        return redirect(url_for("index"))

    @app.route("/api/analyze", methods=["POST"])
    def analyze_statement():
        if "statement" not in request.files:
            return jsonify({"error": "Missing CSV file input named 'statement'."}), 400

        upload = request.files["statement"]
        if not upload.filename.strip():
            return jsonify({"error": "Please choose a CSV file before submitting."}), 400
        if not upload.filename.lower().endswith(".csv"):
            return jsonify({"error": "Only CSV statements are supported for this flow."}), 400

        try:
            monthly_budget = _parse_float(request.form, "monthly_budget", 0.0)
            fixed_costs = _parse_float(request.form, "fixed_costs", 0.0)
            goal_name = request.form.get("goal_name", "").strip()
            goal_amount = _parse_float(request.form, "goal_amount", 0.0)
            goal_timeline_months = _parse_int(request.form, "goal_timeline_months", 6)
            manual_expenses = _parse_manual_expenses(request.form.get("manual_expenses_json", "[]"))
            budget_caps = _parse_budget_caps(
                request.form.get("budget_caps_json", "{}"),
                defaults=BudgetRecommender.default_target_max_ratio(),
            )
        except ValueError as e:
            return jsonify({"error": str(e) or "Invalid input values."}), 400

        with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
            upload.save(tmp.name)
            temp_path = tmp.name

        try:
            parser = StatementCsvParser()
            recommender = BudgetRecommender()
            recurring_analyzer = RecurringExpenseAnalyzer()

            categorized = parser.parse(temp_path)
            categorized.extend(manual_expenses)
            history_transactions, history_averages = _parse_history_bundle(
                parser=parser,
                files=request.files.getlist("history_statements"),
            )
            recurring_expenses = recurring_analyzer.analyze([*history_transactions, *categorized])
            monthly_recurring_total = recurring_analyzer.monthly_recurring_total(recurring_expenses)
            category_totals = parser.category_totals(categorized)
            total_spent = round(sum(item.amount for item in categorized), 2)
            recommendation_payload = recommender.build_recommendations(
                category_totals=category_totals,
                monthly_budget=monthly_budget,
                total_spent=total_spent,
                fixed_costs=fixed_costs,
                normalized_recurring_monthly_total=monthly_recurring_total,
                goal_name=goal_name,
                goal_amount=goal_amount,
                goal_timeline_months=goal_timeline_months,
                history_category_averages=history_averages,
                target_max_ratio_override=budget_caps,
            )

            return jsonify(
                {
                    "total_spent": total_spent,
                    "monthly_budget": monthly_budget,
                    "fixed_costs": fixed_costs,
                    "transaction_count": len(categorized),
                    "category_totals": category_totals,
                    "history_category_averages": history_averages,
                    "recurring_expenses": [item.to_dict() for item in recurring_expenses[:8]],
                    "monthly_recurring_total": monthly_recurring_total,
                    "transactions": [item.to_dict() for item in categorized],
                    **recommendation_payload,
                }
            )
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    @app.route("/api/upload-statement", methods=["POST"])
    def upload_statement():
        try:
            user_id = require_user_id()
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 401

        if "statement" not in request.files:
            return jsonify({"error": "Missing CSV file input named 'statement'."}), 400

        upload = request.files["statement"]
        if not upload.filename.strip():
            return jsonify({"error": "Please choose a CSV file before submitting."}), 400
        if not upload.filename.lower().endswith(".csv"):
            return jsonify({"error": "Only CSV statements are supported for this flow."}), 400

        with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
            upload.save(tmp.name)
            temp_path = tmp.name

        try:
            parser = StatementCsvParser()
            transactions = parser.parse(temp_path)
            get_storage().add_transactions(
                user_id,
                [
                    {
                        "date": item.date,
                        "description": item.description,
                        "amount": item.amount,
                        "category": item.category,
                        "source": "statement",
                    }
                    for item in transactions
                ],
            )
            return jsonify(
                {
                    "saved_transactions": len(transactions),
                    "profile": get_storage().get_dashboard_data(user_id),
                }
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    @app.route("/api/chat", methods=["POST"])
    def chat():
        try:
            user_id = require_user_id()
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 401

        payload = request.get_json(silent=True) or {}
        message = str(payload.get("message", "")).strip()
        if not message:
            return jsonify({"error": "Message is required."}), 400

        storage = get_storage()
        coach = get_coach()
        profile = storage.get_dashboard_data(user_id)
        result = coach.process_message(message, profile)

        storage.add_chat_message(user_id, "user", message)
        storage.add_chat_message(user_id, "assistant", result["reply"])

        action = result["action"]
        if action["type"] == "add_transaction":
            storage.clear_pending_action(user_id)
            storage.add_transactions(user_id, [action["transaction"]])
        elif action["type"] == "confirm_transaction_match":
            storage.set_pending_action(
                user_id,
                "confirm_transaction_match",
                {"transaction": action["transaction"]},
            )
        elif action["type"] == "clear_pending_action":
            storage.clear_pending_action(user_id)
        elif action["type"] == "mark_subscription_cancel":
            storage.clear_pending_action(user_id)
            storage.save_subscription_decision(user_id, action["merchant"], "cancel")
        elif action["type"] == "mark_subscription_keep":
            storage.clear_pending_action(user_id)
            storage.save_subscription_decision(user_id, action["merchant"], "keep")

        updated_profile = storage.get_dashboard_data(user_id)
        return jsonify(
            {
                "reply": result["reply"],
                "action": action,
                "messages": updated_profile["messages"],
                "profile": updated_profile,
            }
        )

    return app


def get_runtime_config() -> dict[str, object]:
    port = int(os.getenv("PORT", "5055"))
    debug = os.getenv("FLASK_DEBUG", "1") == "1" and "PORT" not in os.environ
    return {"host": "0.0.0.0", "port": port, "debug": debug}


app = create_app()


if __name__ == "__main__":
    app.run(**get_runtime_config())
