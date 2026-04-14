from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List


@dataclass
class CategorizedTransaction:
    date: str
    description: str
    amount: float
    category: str

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "description": self.description,
            "amount": self.amount,
            "category": self.category,
        }


class StatementCsvParser:
    REQUIRED_COLUMNS = {"Transaction Date", "Description", "Category", "Amount"}

    def parse(self, file_path: str) -> List[CategorizedTransaction]:
        with open(file_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                raise ValueError("CSV is empty or missing headers.")

            missing = self.REQUIRED_COLUMNS - set(reader.fieldnames)
            if missing:
                missing_cols = ", ".join(sorted(missing))
                raise ValueError(f"CSV missing required columns: {missing_cols}")

            transactions: List[CategorizedTransaction] = []
            for row in reader:
                amount = self._parse_amount(row.get("Amount", ""))
                if amount is None:
                    continue

                # In your CSV format, expenses are negative and payments/credits are positive.
                if amount >= 0:
                    continue

                date_iso = self._parse_date(row.get("Transaction Date", ""))
                if not date_iso:
                    continue

                category = (row.get("Category", "") or "").strip() or "Other"
                description = (row.get("Description", "") or "").strip() or "Unknown"

                transactions.append(
                    CategorizedTransaction(
                        date=date_iso,
                        description=description,
                        amount=round(abs(amount), 2),
                        category=category,
                    )
                )

            return transactions

    @staticmethod
    def category_totals(transactions: List[CategorizedTransaction]) -> Dict[str, float]:
        totals: Dict[str, float] = {}
        for txn in transactions:
            totals[txn.category] = totals.get(txn.category, 0.0) + txn.amount
        return dict(sorted(((k, round(v, 2)) for k, v in totals.items()), key=lambda item: item[1], reverse=True))

    @staticmethod
    def _parse_amount(raw: str) -> float | None:
        cleaned = (raw or "").replace("$", "").replace(",", "").strip()
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None

    @staticmethod
    def _parse_date(raw: str) -> str:
        value = (raw or "").strip()
        if not value:
            return ""
        try:
            parsed = datetime.strptime(value, "%m/%d/%Y")
        except ValueError:
            return ""
        return parsed.strftime("%Y-%m-%d")
