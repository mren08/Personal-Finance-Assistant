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
            sample = f.read(2048)
            f.seek(0)
            delimiter = self._detect_delimiter(sample)
            reader = csv.DictReader(f, delimiter=delimiter)
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

                entry_type = (row.get("Type", "") or "").strip().lower()
                if entry_type == "payment":
                    continue

                # Expenses are negative and credits/payments/returns are positive.
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
    def _detect_delimiter(sample: str) -> str:
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",\t")
            return dialect.delimiter
        except csv.Error:
            return "\t" if "\t" in sample else ","

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
