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

        subscription_action = self._subscription_action(lower_message)
        if subscription_action:
            merchant, decision = subscription_action
            if decision == "keep":
                reply = f"Keep {merchant} if you actually use it. Just stop pretending every subscription is essential."
            else:
                reply = f"Cancel it: {merchant}. If you still pay for overlapping services after this, that is a choice, not an accident."
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
            return {
                "reply": f"{merchant} is being counted as {category.lower()} spending. That still counts even if it happened over Zelle.",
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

    def _subscription_action(self, lower_message: str) -> tuple[str, str] | None:
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
        return None

    def _manual_transaction(self, message: str) -> dict[str, Any] | None:
        amount_match = re.search(r"\$?(\d+(?:\.\d{1,2})?)", message)
        merchant_match = re.search(r"(?:at|for)\s+([A-Za-z][A-Za-z0-9 '&.-]+?)\s+(?:for\s+)?\$?\d", message, re.IGNORECASE)

        if not amount_match or not merchant_match:
            return None

        merchant = merchant_match.group(1).strip().rstrip(".")
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
