import json
from collections.abc import Mapping


FALLBACK_REPLY = "I couldn't produce a reliable coaching response right now."


def build_openai_llm_client():
    from openai import OpenAI

    client = OpenAI()

    def call_agent(payload: dict) -> dict:
        response = client.responses.create(
            model="gpt-5-mini",
            input=[
                {
                    "role": "system",
                    "content": "You are a personal finance coaching agent. Return JSON only.",
                },
                {"role": "user", "content": json.dumps(payload)},
            ],
        )
        return json.loads(response.output_text)

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
        response = self.llm_client(
            {
                "message": message,
                "context": agent_context,
                "allowed_action_types": list(self.ALLOWED_ACTION_TYPES),
            }
        )
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
