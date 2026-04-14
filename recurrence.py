from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from statistics import median
from typing import Dict, Iterable, List

from csv_parser import CategorizedTransaction


@dataclass
class RecurringExpense:
    merchant: str
    category: str
    frequency: str
    average_amount: float
    monthly_equivalent: float
    occurrences: int
    last_seen: str

    def to_dict(self) -> dict:
        return {
            "merchant": self.merchant,
            "category": self.category,
            "frequency": self.frequency,
            "average_amount": round(self.average_amount, 2),
            "monthly_equivalent": round(self.monthly_equivalent, 2),
            "occurrences": self.occurrences,
            "last_seen": self.last_seen,
        }


class RecurringExpenseAnalyzer:
    NOISE_TOKENS = {
        "tst",
        "sq",
        "inc",
        "llc",
        "co",
        "corp",
        "web",
        "online",
        "purchase",
    }

    def analyze(self, transactions: Iterable[CategorizedTransaction]) -> List[RecurringExpense]:
        grouped: Dict[str, List[CategorizedTransaction]] = {}
        merchant_labels: Dict[str, str] = {}

        for txn in transactions:
            key = self._merchant_key(txn.description)
            if not key:
                continue
            grouped.setdefault(key, []).append(txn)
            merchant_labels.setdefault(key, self._merchant_label(txn.description))

        recurring: List[RecurringExpense] = []
        for key, items in grouped.items():
            if len(items) < 2:
                continue

            sorted_items = sorted(items, key=lambda item: item.date)
            dates = [datetime.strptime(item.date, "%Y-%m-%d").date() for item in sorted_items]
            gaps = [(dates[idx] - dates[idx - 1]).days for idx in range(1, len(dates))]
            if not gaps:
                continue

            frequency = self._infer_frequency(gaps)
            if frequency == "one-off":
                continue

            span_days = (dates[-1] - dates[0]).days
            if not self._passes_recurrence_threshold(sorted_items, frequency, span_days):
                continue

            avg_amount = sum(item.amount for item in sorted_items) / len(sorted_items)
            monthly_equivalent = self._monthly_equivalent(avg_amount, frequency)
            if monthly_equivalent < 5:
                continue

            recurring.append(
                RecurringExpense(
                    merchant=merchant_labels[key],
                    category=sorted_items[-1].category,
                    frequency=frequency,
                    average_amount=avg_amount,
                    monthly_equivalent=monthly_equivalent,
                    occurrences=len(sorted_items),
                    last_seen=sorted_items[-1].date,
                )
            )

        recurring.sort(key=lambda item: item.monthly_equivalent, reverse=True)
        return recurring

    def monthly_recurring_total(self, recurring: Iterable[RecurringExpense]) -> float:
        return round(sum(item.monthly_equivalent for item in recurring), 2)

    def _merchant_key(self, description: str) -> str:
        cleaned = description.lower()
        cleaned = re.sub(r"[^a-z0-9 ]+", " ", cleaned)
        tokens = [
            token
            for token in cleaned.split()
            if token not in self.NOISE_TOKENS and not token.isdigit() and len(token) > 1
        ]
        return " ".join(tokens[:4])

    def _merchant_label(self, description: str) -> str:
        label = re.sub(r"\s+", " ", description).strip()
        return label[:48]

    def _infer_frequency(self, gaps: List[int]) -> str:
        typical_gap = median(gaps)
        if typical_gap <= 10:
            return "weekly"
        if typical_gap <= 45:
            return "monthly"
        if typical_gap <= 120:
            return "quarterly"
        if typical_gap <= 420:
            return "annual"
        return "one-off"

    def _monthly_equivalent(self, amount: float, frequency: str) -> float:
        if frequency == "weekly":
            return amount * 52 / 12
        if frequency == "monthly":
            return amount
        if frequency == "quarterly":
            return amount / 3
        if frequency == "annual":
            return amount / 12
        return 0.0

    def _passes_recurrence_threshold(
        self,
        items: List[CategorizedTransaction],
        frequency: str,
        span_days: int,
    ) -> bool:
        amounts = [item.amount for item in items]
        avg_amount = sum(amounts) / len(amounts)
        max_deviation = max(abs(amount - avg_amount) for amount in amounts) / avg_amount if avg_amount else 0.0

        if frequency == "weekly":
            return len(items) >= 3 and span_days >= 14 and max_deviation <= 0.25
        if frequency == "monthly":
            return len(items) >= 2 and span_days >= 25 and max_deviation <= 0.2
        if frequency == "quarterly":
            return len(items) >= 2 and span_days >= 70 and max_deviation <= 0.2
        if frequency == "annual":
            return len(items) >= 2 and span_days >= 300 and max_deviation <= 0.15
        return False
