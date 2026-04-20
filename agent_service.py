import json
import os
import re
from collections.abc import Mapping


FALLBACK_REPLY = "I couldn't produce a reliable coaching response right now."
SYSTEM_PROMPT = """
You are an agentic personal finance coach inside a budgeting app.
Behave like a thoughtful, conversational assistant, not a rigid classifier.

Your job:
- answer the user's actual question directly
- use the provided recent conversation and financial context
- be specific about the selected month when relevant
- give practical advice, tradeoffs, and a recommendation when the user asks what they should do
- suggest or emit structured actions when the user is clearly asking to save data or update the account

Action rules:
- only emit actions from the allowed_action_types list
- emit no action when the user is just asking for advice or explanation
- if the user says they spent money, you may add a transaction
- if the user asks to keep or cancel a subscription, you may mark that decision
- if the user asks about money left, categories, or trends, answer directly without forcing an action

Response format:
- return valid JSON only
- top-level object with:
  - "reply": string
  - "actions": array
""".strip()


def _summarize_context(payload: dict) -> str:
    context = payload.get("context") or {}
    message = str(payload.get("message") or "").strip()
    monthly_summary = context.get("monthly_summary") or {}
    financial_profile = context.get("financial_profile") or {}
    category_breakdown = context.get("category_breakdown") or []
    subscriptions = context.get("subscriptions") or []
    notes = context.get("agent_notes") or []
    recent_messages = (context.get("messages") or [])[-8:]
    selected_month = context.get("selected_month_label") or "Unknown month"

    lines = [
        f"Latest user message: {message}",
        f"Selected month: {selected_month}",
        "Monthly summary:",
        json.dumps(monthly_summary, ensure_ascii=True),
        "Financial profile:",
        json.dumps(financial_profile, ensure_ascii=True),
        "Top spending categories:",
        json.dumps(category_breakdown[:5], ensure_ascii=True),
        "Recurring subscriptions:",
        json.dumps(subscriptions[:5], ensure_ascii=True),
        "Agent notes:",
        json.dumps(notes[:5], ensure_ascii=True),
        "Recent conversation:",
        json.dumps(recent_messages, ensure_ascii=True),
        "Allowed action types:",
        json.dumps(payload.get("allowed_action_types") or [], ensure_ascii=True),
    ]
    return "\n".join(lines)


def _parse_json_response(text: str) -> dict:
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("Empty model response.")

    try:
        parsed = json.loads(raw)
        if isinstance(parsed, Mapping):
            return dict(parsed)
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.DOTALL)
    if fenced:
        parsed = json.loads(fenced.group(1))
        if isinstance(parsed, Mapping):
            return dict(parsed)

    object_match = re.search(r"(\{.*\})", raw, re.DOTALL)
    if object_match:
        parsed = json.loads(object_match.group(1))
        if isinstance(parsed, Mapping):
            return dict(parsed)

    raise ValueError("Model response did not contain valid JSON.")


def _candidate_models() -> list[str]:
    preferred = str(os.getenv("OPENAI_MODEL", "")).strip()
    models = [preferred] if preferred else []
    models.extend(["gpt-5.4-mini", "gpt-5-mini", "gpt-4.1-mini"])

    deduped = []
    for model in models:
        if model and model not in deduped:
            deduped.append(model)
    return deduped


def build_openai_llm_client():
    from openai import OpenAI

    client = OpenAI()

    def call_agent(payload: dict) -> dict:
        last_error = None
        for model in _candidate_models():
            try:
                response = client.responses.create(
                    model=model,
                    input=[
                        {
                            "role": "system",
                            "content": SYSTEM_PROMPT,
                        },
                        {"role": "user", "content": _summarize_context(payload)},
                    ],
                )
                return _parse_json_response(getattr(response, "output_text", ""))
            except Exception as exc:
                last_error = exc
                continue
        raise RuntimeError("OpenAI agent request failed.") from last_error

    return call_agent


class AgentService:
    ALLOWED_ACTION_TYPES = (
        "add_transaction",
        "confirm_transaction_match",
        "save_monthly_income",
        "save_fixed_expense",
        "update_goal",
        "mark_subscription_cancel",
        "mark_subscription_keep",
        "save_agent_note",
    )

    def __init__(self, llm_client):
        self.llm_client = llm_client

    def run_chat_turn(self, message: str, agent_context: dict) -> dict:
        try:
            response = self.llm_client(
                {
                    "message": message,
                    "context": agent_context,
                    "allowed_action_types": list(self.ALLOWED_ACTION_TYPES),
                }
            )
        except Exception:
            return {"reply": FALLBACK_REPLY, "actions": []}
        if not isinstance(response, Mapping):
            return {"reply": FALLBACK_REPLY, "actions": []}

        return {
            "reply": str(response.get("reply", "")).strip() or FALLBACK_REPLY,
            "actions": self._normalize_actions(response.get("actions", [])),
        }

    def _normalize_actions(self, raw_actions) -> list[dict]:
        if not isinstance(raw_actions, list):
            return []

        normalized_actions = []
        for action in raw_actions:
            normalized = self._normalize_action(action)
            if normalized is not None:
                normalized_actions.append(normalized)
        return normalized_actions

    def _normalize_action(self, action) -> dict | None:
        if not isinstance(action, Mapping):
            return None

        action_type = str(action.get("type", "")).strip()
        if action_type not in self.ALLOWED_ACTION_TYPES:
            return None

        if action_type in {"mark_subscription_cancel", "mark_subscription_keep"}:
            merchant = str(action.get("merchant", "")).strip()
            if not merchant:
                return None
            return {"type": action_type, "merchant": merchant}

        if action_type == "save_agent_note":
            note_type = str(action.get("note_type", "")).strip()
            content = str(action.get("content", "")).strip()
            if not note_type or not content:
                return None
            return {
                "type": action_type,
                "note_type": note_type,
                "content": content,
            }

        if action_type in {"save_monthly_income", "save_fixed_expense"}:
            value = action.get("value")
            try:
                normalized_value = round(float(value), 2)
            except (TypeError, ValueError):
                return None
            return {"type": action_type, "value": normalized_value}

        if action_type == "update_goal":
            goal = str(action.get("goal", "")).strip()
            if not goal:
                return None
            return {"type": action_type, "goal": goal}

        transaction = action.get("transaction")
        if not isinstance(transaction, Mapping):
            return None

        description = str(transaction.get("description", "")).strip()
        category = str(transaction.get("category", "")).strip()
        date = str(transaction.get("date", "")).strip()
        source = str(transaction.get("source", "chat_manual")).strip() or "chat_manual"
        try:
            amount = round(float(transaction.get("amount")), 2)
        except (TypeError, ValueError):
            return None

        if not description or not category or not date:
            return None

        return {
            "type": action_type,
            "transaction": {
                "date": date,
                "description": description,
                "amount": amount,
                "category": category,
                "source": source,
            },
        }
