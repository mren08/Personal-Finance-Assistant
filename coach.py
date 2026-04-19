from __future__ import annotations

import re
from datetime import date
from typing import Any


class OverspendingCoach:
    RESTAURANT_HINTS = {"howoo", "restaurant", "grill", "pizza", "sushi", "bbq", "cafe", "kitchen"}
    KNOWN_SUBSCRIPTIONS = {
        "netflix": "Netflix",
        "amazon prime": "Amazon Prime",
        "spotify": "Spotify",
        "hulu": "Hulu",
        "disney": "Disney+",
    }

    def process_message(self, message: str, profile: dict[str, Any]) -> dict[str, Any]:
        clean_message = message.strip()
        lower_message = clean_message.lower()
        pending_action = profile.get("pending_action")

        if pending_action and pending_action.get("type") == "confirm_transaction_match":
            if self._is_negative_confirmation(lower_message):
                transaction = pending_action["transaction"]
                return {
                    "reply": f"Noted. {transaction['description']} was not already in your statements, so I am adding it now.",
                    "action": {
                        "type": "add_transaction",
                        "transaction": transaction,
                    },
                }
            if self._is_positive_confirmation(lower_message):
                return {
                    "reply": "Good. I am not adding a duplicate transaction.",
                    "action": {"type": "clear_pending_action"},
                }

        subscription_action = self._subscription_action(lower_message, profile)
        if subscription_action:
            merchant, decision = subscription_action
            display_name = self._display_merchant(merchant)
            if decision == "keep":
                reply = f"Keep {display_name} if you actually use it. Just stop pretending every subscription is essential."
            else:
                reply = f"Cancel it: {display_name}. If you still pay for overlapping services after this, that is a choice, not an accident."
            return {
                "reply": reply,
                "action": {
                    "type": f"mark_subscription_{decision}",
                    "merchant": merchant,
                },
            }

        transaction = self._manual_transaction(clean_message)
        if transaction:
            merchant = transaction["description"]
            category = transaction["category"]
            if self._should_confirm_existing_match(transaction, profile.get("transactions", []), lower_message):
                return {
                    "reply": f"I found a similar {merchant} charge in your uploaded statements. Is this already included there, or should I add it as a separate transaction?",
                    "action": {
                        "type": "confirm_transaction_match",
                        "transaction": transaction,
                    },
                }
            return {
                "reply": f"{merchant} is being counted as {category.lower()} spending and added to your history.",
                "action": {
                    "type": "add_transaction",
                    "transaction": transaction,
                },
            }

        top_category = next(iter(profile.get("category_totals", {}).items()), None)
        if top_category:
            category, amount = top_category
            return {
                "reply": f"Your biggest leak right now is {category} at ${amount:.2f}. Stop negotiating with that pattern and decide what gets cut.",
                "action": {"type": "none"},
            }

        return {
            "reply": "Start with a real expense or a subscription decision. Vague intentions do not lower your spending.",
            "action": {"type": "none"},
        }

    def _subscription_action(self, lower_message: str, profile: dict[str, Any]) -> tuple[str, str] | None:
        decision = None
        if "cancel" in lower_message or "cut" in lower_message:
            decision = "cancel"
        elif "keep" in lower_message:
            decision = "keep"

        if not decision:
            return None

        for keyword, merchant in self.KNOWN_SUBSCRIPTIONS.items():
            if keyword in lower_message:
                return merchant, decision

        subscriptions = profile.get("subscriptions") or []
        for subscription in subscriptions:
            merchant = str(subscription.get("merchant") or "").strip()
            if not merchant:
                continue
            merchant_tokens = self._normalized(merchant)
            if merchant_tokens and merchant_tokens.intersection(self._normalized(lower_message)):
                return merchant, decision
        return None

    def _manual_transaction(self, message: str) -> dict[str, Any] | None:
        amount_match = re.search(r"\$?(\d+(?:\.\d{1,2})?)", message)
        merchant = None
        at_match = re.search(
            r"\bat\s+([A-Za-z][A-Za-z0-9 '&.-]+?)(?:\s+for\s+\$?\d|[.!?]|$)",
            message,
            re.IGNORECASE,
        )
        if at_match:
            merchant = at_match.group(1).strip()
        if merchant is None:
            for_match = re.search(
                r"\bfor\s+([A-Za-z][A-Za-z0-9 '&.-]+?)(?:[.!?]|$)",
                message,
                re.IGNORECASE,
            )
            if for_match and not amount_match:
                merchant = for_match.group(1).strip()

        if not amount_match:
            return None

        if merchant is None and re.search(r"\b(spent|paid|bought)\b", message, re.IGNORECASE):
            merchant = "Manual expense"
        if not merchant:
            return None

        merchant = merchant.rstrip(".")
        amount = round(float(amount_match.group(1)), 2)
        lowered = merchant.lower()
        category = "Dining" if any(token in lowered for token in self.RESTAURANT_HINTS) else "Other"

        return {
            "date": str(date.today()),
            "description": merchant,
            "amount": amount,
            "category": category,
            "source": "chat_manual",
        }

    @staticmethod
    def _normalized(text: str) -> set[str]:
        cleaned = re.sub(r"[^a-z0-9 ]+", " ", text.lower())
        return {token for token in cleaned.split() if len(token) > 1}

    @staticmethod
    def _display_merchant(merchant: str) -> str:
        merchant = merchant.strip()
        if merchant.isupper():
            return merchant.title()
        return merchant

    def _should_confirm_existing_match(
        self,
        transaction: dict[str, Any],
        existing_transactions: list[dict[str, Any]],
        lower_message: str,
    ) -> bool:
        if "not in my statement" in lower_message or "wasn't in my statement" in lower_message or "not in the statement" in lower_message:
            return False

        target_amount = float(transaction["amount"])
        target_tokens = self._normalized(transaction["description"])

        for existing in existing_transactions:
            if abs(float(existing.get("amount", 0)) - target_amount) > 0.01:
                continue
            existing_tokens = self._normalized(existing.get("description", ""))
            if target_tokens and existing_tokens and target_tokens.intersection(existing_tokens):
                return True
        return False

    @staticmethod
    def _is_negative_confirmation(lower_message: str) -> bool:
        return lower_message.startswith("no") or "not in" in lower_message or "not yet" in lower_message

    @staticmethod
    def _is_positive_confirmation(lower_message: str) -> bool:
        return lower_message.startswith("yes") or "already included" in lower_message or "already in" in lower_message
