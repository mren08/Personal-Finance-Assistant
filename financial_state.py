from decimal import Decimal, ROUND_HALF_UP


_CENT = Decimal("0.01")


def _round_money(amount: float) -> Decimal:
    return Decimal(str(amount)).quantize(_CENT, rounding=ROUND_HALF_UP)


def build_monthly_summary(
    monthly_income: float,
    fixed_expenses: float,
    tracked_spending: float,
    recurring_monthly_total: float,
) -> dict[str, float]:
    rounded_income = _round_money(monthly_income)
    rounded_fixed_expenses = _round_money(fixed_expenses)
    rounded_tracked_spending = _round_money(tracked_spending)
    rounded_recurring_total = _round_money(recurring_monthly_total)
    leftover_money = rounded_income - rounded_tracked_spending
    discretionary_remaining = (
        rounded_income - rounded_fixed_expenses - rounded_tracked_spending
    )

    return {
        "monthly_income": float(rounded_income),
        "fixed_expenses": float(rounded_fixed_expenses),
        "tracked_spending": float(rounded_tracked_spending),
        "recurring_monthly_total": float(rounded_recurring_total),
        "leftover_money": float(leftover_money),
        "discretionary_remaining": float(discretionary_remaining),
    }
