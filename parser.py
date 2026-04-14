from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import List

import pdfplumber


@dataclass
class Transaction:
    date: str
    description: str
    amount: float

    def to_dict(self) -> dict:
        return asdict(self)


class StatementParser:
    TRANSACTION_PATTERNS = [
        re.compile(
            r"^\s*(\d{1,2}/\d{1,2}(?:/\d{2,4})?)\s+(.+?)\s+(-?\$?\(?\d[\d,]*\.\d{2}\)?)\s*$"
        ),
        re.compile(
            r"^\s*(\d{1,2}/\d{1,2}(?:/\d{2,4})?)\s+\d{1,2}/\d{1,2}(?:/\d{2,4})?\s+(.+?)\s+(-?\$?\(?\d[\d,]*\.\d{2}\)?)\s*$"
        ),
    ]
    IGNORE_WORDS = {
        "payment",
        "autopay",
        "credit",
        "adjustment",
        "late fee reversal",
        "interest charged",
    }

    @staticmethod
    def _parse_amount(raw: str) -> float | None:
        cleaned = raw.replace("$", "").replace(",", "").strip()
        negative = False
        if cleaned.startswith("(") and cleaned.endswith(")"):
            negative = True
            cleaned = cleaned[1:-1]
        if cleaned.startswith("-"):
            negative = True
            cleaned = cleaned[1:]

        try:
            value = float(cleaned)
        except ValueError:
            return None

        return -value if negative else value

    @staticmethod
    def _normalize_date(date_text: str) -> str:
        parts = date_text.split("/")
        if len(parts[-1]) == 4:
            parsed = datetime.strptime(date_text, "%m/%d/%Y")
        elif len(parts[-1]) == 2 and len(parts) == 3:
            parsed = datetime.strptime(date_text, "%m/%d/%y")
        else:
            parsed = datetime.strptime(f"{date_text}/{datetime.now().year}", "%m/%d/%Y")
        return parsed.strftime("%Y-%m-%d")

    def parse_pdf(self, file_path: str) -> List[Transaction]:
        transactions: List[Transaction] = []

        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                for raw_line in text.splitlines():
                    line = re.sub(r"\s+", " ", raw_line).strip()
                    if not line:
                        continue

                    matched = None
                    for pattern in self.TRANSACTION_PATTERNS:
                        matched = pattern.match(line)
                        if matched:
                            break
                    if not matched:
                        continue

                    date_text, description, amount_text = matched.groups()
                    amount = self._parse_amount(amount_text)
                    if amount is None:
                        continue

                    normalized_desc = description.lower()
                    if any(word in normalized_desc for word in self.IGNORE_WORDS):
                        continue

                    # Most statements represent spending as positive charges.
                    # If amount is negative it usually means a refund/credit.
                    if amount <= 0:
                        continue

                    try:
                        normalized_date = self._normalize_date(date_text)
                    except ValueError:
                        continue

                    transactions.append(
                        Transaction(
                            date=normalized_date,
                            description=description.strip(),
                            amount=round(amount, 2),
                        )
                    )

        return transactions
