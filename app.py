from __future__ import annotations

import json
import os
import tempfile
from collections import defaultdict
from datetime import UTC, datetime

from flask import Flask, current_app, jsonify, redirect, render_template, request, session, url_for

from agent_service import AgentService, FALLBACK_REPLY, build_openai_llm_client
from coach import OverspendingCoach
from csv_parser import CategorizedTransaction, StatementCsvParser
from financial_state import build_monthly_summary
from recurrence import RecurringExpenseAnalyzer
from recommender import BudgetRecommender
from storage import Storage


class _FallbackAgentClient:
    def __call__(self, payload: dict) -> dict:
        context = payload.get("context", {})
        monthly_summary = context.get("monthly_summary") or {}
        financial_profile = context.get("financial_profile") or {}
        goal = str(financial_profile.get("budgeting_goal") or "").strip()
        available_before_fixed = monthly_summary.get("available_before_fixed")
        leftover_money = monthly_summary.get("leftover_money")
        if isinstance(leftover_money, (int, float)):
            reply = f"You have ${float(leftover_money):.2f} left this month after fixed expenses."
            if goal:
                reply = f"{reply} That only matters if you still act on: {goal}."
        elif isinstance(available_before_fixed, (int, float)):
            reply = f"You have ${float(available_before_fixed):.2f} available before fixed expenses this month."
        else:
            reply = FALLBACK_REPLY
        return {"reply": reply, "actions": []}


def build_agent_service() -> AgentService:
    if os.getenv("OPENAI_API_KEY"):
        try:
            return AgentService(llm_client=build_openai_llm_client())
        except Exception:
            pass
    return AgentService(llm_client=_FallbackAgentClient())


def _current_month_key() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


def _current_month_label() -> str:
    return datetime.now(UTC).strftime("%B %Y")


def _display_merchant(name: str) -> str:
    cleaned = str(name).replace(".COM", "").replace(".com", "").strip()
    if cleaned.isupper():
        return cleaned.title()
    return cleaned


def _build_month_focus_note(profile: dict, summary: dict) -> str:
    segments = [
        f"{_current_month_label()}: left after fixed expenses is ${summary['leftover_money']:.2f}."
    ]

    category_breakdown = profile.get("category_breakdown") or []
    if category_breakdown:
        biggest = category_breakdown[0]
        segments.append(
            f"Biggest category is {biggest['category']} at ${biggest['amount']:.2f} ({biggest['percentage']:.2f}% of tracked spending)."
        )

    subscriptions = profile.get("subscriptions") or []
    recurring_total = float(profile.get("monthly_recurring_total") or 0)
    if subscriptions:
        top_names = ", ".join(_display_merchant(item["merchant"]) for item in subscriptions[:3])
        segments.append(
            f"Recurring charges are ${recurring_total:.2f}/month across {len(subscriptions)} services, led by {top_names}."
        )

    goal = str((profile.get("financial_profile") or {}).get("budgeting_goal") or "").strip()
    if goal:
        segments.append(f"Budget goal: {goal}.")

    return " ".join(segments)


def _build_proactive_chat_message(profile: dict) -> str | None:
    category_breakdown = profile.get("category_breakdown") or []
    subscriptions = profile.get("subscriptions") or []
    monthly_summary = profile.get("monthly_summary") or {}
    if not category_breakdown and not subscriptions and not monthly_summary:
        return None

    parts = []
    leftover_money = monthly_summary.get("leftover_money")
    if isinstance(leftover_money, (int, float)):
        parts.append(f"You have ${float(leftover_money):.2f} left this month after fixed expenses.")

    if category_breakdown:
        biggest = category_breakdown[0]
        parts.append(
            f"Your biggest category is {biggest['category']} at ${biggest['amount']:.2f} ({biggest['percentage']:.2f}%)."
        )

    if subscriptions:
        recurring_total = float(profile.get("monthly_recurring_total") or 0)
        top_names = ", ".join(_display_merchant(item["merchant"]) for item in subscriptions[:3])
        parts.append(
            f"I noticed recurring charges totaling ${recurring_total:.2f}/month across {len(subscriptions)} services, including {top_names}. Are you sure you want to keep those?"
        )

    if not parts:
        return None
    return " ".join(parts)


def _extract_monthly_focus_content(actions: list[dict]) -> str | None:
    for action in reversed(actions):
        if action.get("type") == "save_agent_note" and action.get("note_type") == "monthly_focus":
            return action.get("content")
    return None

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

    def get_agent_service() -> AgentService:
        return build_agent_service()

    def current_user_id() -> int | None:
        raw = session.get("user_id")
        return int(raw) if raw is not None else None

    def require_user_id() -> int:
        user_id = current_user_id()
        if user_id is None:
            raise PermissionError("Sign in first.")
        return user_id

    def maybe_seed_proactive_chat(user_id: int, profile: dict) -> None:
        storage = get_storage()
        if storage.list_chat_messages(user_id):
            return

        if not profile.get("transaction_count"):
            return

        proactive_message = _build_proactive_chat_message(profile)
        if proactive_message:
            storage.add_chat_message(user_id, "assistant", proactive_message)

    def refresh_user_summary(user_id: int) -> dict:
        storage = get_storage()
        profile = storage.get_dashboard_data(user_id)
        financial_profile = profile.get("financial_profile") or {
            "monthly_income": 0,
            "fixed_expenses": 0,
            "budgeting_goal": "",
        }
        summary = build_monthly_summary(
            monthly_income=float(financial_profile.get("monthly_income") or 0),
            fixed_expenses=float(financial_profile.get("fixed_expenses") or 0),
            tracked_spending=float(profile.get("total_spent") or 0),
            recurring_monthly_total=float(profile.get("monthly_recurring_total") or 0),
        )
        storage.save_monthly_summary(
            user_id,
            month_key=_current_month_key(),
            income=summary["monthly_income"],
            fixed_expenses=summary["fixed_expenses"],
            tracked_spending=summary["tracked_spending"],
            recurring_monthly_total=summary["recurring_monthly_total"],
            leftover_money=summary["leftover_money"],
            discretionary_remaining=summary["discretionary_remaining"],
            summary_text=(
                f"Left this month after fixed expenses: ${summary['leftover_money']:.2f}. "
                f"Available before fixed expenses: ${summary['available_before_fixed']:.2f}."
            ),
        )
        return storage.get_dashboard_data(user_id)

    def update_current_month_focus_note(user_id: int, profile: dict, content: str | None = None) -> dict:
        summary = profile.get("monthly_summary") or {}
        if not summary:
            return profile

        get_storage().replace_agent_note(
            user_id,
            note_type=f"{_current_month_label()} focus",
            content=content or _build_month_focus_note(profile, summary),
        )
        return get_storage().get_dashboard_data(user_id)

    def apply_agent_actions(user_id: int, actions: list[dict]) -> None:
        storage = get_storage()
        for action in actions:
            action_type = action.get("type")
            if action_type == "save_agent_note":
                if action["note_type"] != "monthly_focus":
                    storage.save_agent_note(
                        user_id,
                        note_type=action["note_type"],
                        content=action["content"],
                    )
            elif action_type == "save_monthly_income":
                current_profile = storage.get_financial_profile(user_id) or {}
                storage.upsert_financial_profile(
                    user_id,
                    monthly_income=float(action["value"]),
                    fixed_expenses=float(current_profile.get("fixed_expenses") or 0),
                    budgeting_goal=str(current_profile.get("budgeting_goal") or ""),
                )
            elif action_type == "save_fixed_expense":
                current_profile = storage.get_financial_profile(user_id) or {}
                storage.upsert_financial_profile(
                    user_id,
                    monthly_income=float(current_profile.get("monthly_income") or 0),
                    fixed_expenses=float(action["value"]),
                    budgeting_goal=str(current_profile.get("budgeting_goal") or ""),
                )
            elif action_type == "update_goal":
                current_profile = storage.get_financial_profile(user_id) or {}
                storage.upsert_financial_profile(
                    user_id,
                    monthly_income=float(current_profile.get("monthly_income") or 0),
                    fixed_expenses=float(current_profile.get("fixed_expenses") or 0),
                    budgeting_goal=str(action["goal"]),
                )
            elif action_type == "mark_subscription_cancel":
                storage.clear_pending_action(user_id)
                storage.save_subscription_decision(user_id, action["merchant"], "cancel")
            elif action_type == "mark_subscription_keep":
                storage.clear_pending_action(user_id)
                storage.save_subscription_decision(user_id, action["merchant"], "keep")
            elif action_type == "confirm_transaction_match":
                storage.set_pending_action(
                    user_id,
                    "confirm_transaction_match",
                    {"transaction": action["transaction"]},
                )
            elif action_type == "add_transaction":
                storage.clear_pending_action(user_id)
                storage.add_transactions(user_id, [action["transaction"]])

    @app.route("/")
    def index():
        user_id = current_user_id()
        profile = None
        user = None
        if user_id is not None:
            user = get_storage().get_user(user_id)
            if user:
                profile = refresh_user_summary(user_id)
                profile = update_current_month_focus_note(user_id, profile)
                maybe_seed_proactive_chat(user_id, profile)
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

    @app.route("/api/profile", methods=["POST"])
    def update_profile():
        try:
            user_id = require_user_id()
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 401

        payload = request.get_json(silent=True) or {}
        get_storage().upsert_financial_profile(
            user_id,
            monthly_income=float(payload.get("monthly_income", 0)),
            fixed_expenses=float(payload.get("fixed_expenses", 0)),
            budgeting_goal=str(payload.get("budgeting_goal", "")).strip(),
        )
        profile = refresh_user_summary(user_id)
        profile = update_current_month_focus_note(user_id, profile)
        return jsonify({"profile": profile})

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
            profile = refresh_user_summary(user_id)
            agent_result = get_agent_service().run_chat_turn(
                message="I uploaded a new statement. Update my coaching notes.",
                agent_context=profile,
            )
            monthly_focus_content = _extract_monthly_focus_content(agent_result["actions"])
            apply_agent_actions(user_id, agent_result["actions"])
            profile = refresh_user_summary(user_id)
            profile = update_current_month_focus_note(user_id, profile, monthly_focus_content)
            maybe_seed_proactive_chat(user_id, profile)
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
        profile = refresh_user_summary(user_id)
        heuristic_result = get_coach().process_message(message, profile)
        action = heuristic_result["action"]
        if action["type"] != "none":
            apply_agent_actions(user_id, [action])

        profile = refresh_user_summary(user_id)
        agent_result = get_agent_service().run_chat_turn(message=message, agent_context=profile)
        monthly_focus_content = _extract_monthly_focus_content(agent_result["actions"])
        apply_agent_actions(user_id, agent_result["actions"])

        reply = agent_result["reply"] or heuristic_result["reply"]
        storage.add_chat_message(user_id, "user", message)
        storage.add_chat_message(user_id, "assistant", reply)

        updated_profile = refresh_user_summary(user_id)
        updated_profile = update_current_month_focus_note(user_id, updated_profile, monthly_focus_content)
        return jsonify(
            {
                "reply": reply,
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
