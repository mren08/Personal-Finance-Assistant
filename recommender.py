from __future__ import annotations

from datetime import date
from typing import Dict, List


class BudgetRecommender:
    TARGET_MAX_RATIO = {
        "Housing": 0.35,
        "Utilities": 0.10,
        "Groceries": 0.14,
        "Food & Drink": 0.12,
        "Dining": 0.12,
        "Transportation": 0.12,
        "Travel": 0.10,
        "Shopping": 0.08,
        "Entertainment": 0.07,
        "Health & Wellness": 0.08,
        "Subscriptions": 0.04,
    }
    LOW_CONTROL_CATEGORIES = {"Housing", "Utilities", "Insurance"}

    @classmethod
    def default_target_max_ratio(cls) -> Dict[str, float]:
        return dict(cls.TARGET_MAX_RATIO)

    def build_recommendations(
        self,
        category_totals: Dict[str, float],
        monthly_budget: float,
        total_spent: float,
        fixed_costs: float = 0.0,
        normalized_recurring_monthly_total: float = 0.0,
        goal_name: str = "",
        goal_amount: float = 0.0,
        goal_timeline_months: int = 6,
        history_category_averages: Dict[str, float] | None = None,
        target_max_ratio_override: Dict[str, float] | None = None,
    ) -> Dict[str, object]:
        remaining = round(monthly_budget - total_spent, 2)
        discretionary_remaining = round(monthly_budget - fixed_costs - total_spent, 2)
        normalized_weekly_spend = round(
            max(0.0, monthly_budget - fixed_costs - normalized_recurring_monthly_total) / 4.33,
            2,
        )
        applied_caps = target_max_ratio_override or self.default_target_max_ratio()
        recs = self._category_recommendations(
            category_totals,
            monthly_budget,
            history_category_averages or {},
            applied_caps,
        )
        tip_details = self._actionable_tips(
            category_totals=category_totals,
            remaining=remaining,
            discretionary_remaining=discretionary_remaining,
            normalized_weekly_spend=normalized_weekly_spend,
            normalized_recurring_monthly_total=normalized_recurring_monthly_total,
            goal_name=goal_name.strip(),
            goal_amount=goal_amount,
            goal_timeline_months=goal_timeline_months,
        )
        return {
            "remaining_money": remaining,
            "discretionary_remaining": discretionary_remaining,
            "normalized_weekly_spend": normalized_weekly_spend,
            "normalized_recurring_monthly_total": round(normalized_recurring_monthly_total, 2),
            "recommendations": recs,
            "actionable_tips": [tip["text"] for tip in tip_details],
            "actionable_tips_details": tip_details,
            "applied_budget_caps": applied_caps,
        }

    def _category_recommendations(
        self,
        category_totals: Dict[str, float],
        budget: float,
        history_avg: Dict[str, float],
        target_max_ratio: Dict[str, float],
    ) -> List[str]:
        if budget <= 0:
            return ["Set a realistic monthly budget above $0 to receive ratio-based guidance."]

        recommendations: List[str] = []
        for category, spent in category_totals.items():
            max_ratio = target_max_ratio.get(category)
            if not max_ratio:
                continue
            ratio = spent / budget
            if ratio > max_ratio:
                overspend = round(spent - (budget * max_ratio), 2)
                recommendations.append(
                    f"{category}: reduce by about ${overspend:.2f} to align with a {int(max_ratio * 100)}% budget cap."
                )

        for category, spent in category_totals.items():
            baseline = history_avg.get(category, 0.0)
            if baseline <= 0:
                continue
            delta = spent - baseline
            if delta > 20 and (delta / baseline) > 0.12:
                pct = round((delta / baseline) * 100)
                recommendations.append(
                    f"{category}: up {pct}% vs your historical average (+${delta:.2f}). Review recent purchases in this category."
                )

        if not recommendations:
            recommendations.append("Spending distribution looks balanced against common budget thresholds.")
        return recommendations

    def _actionable_tips(
        self,
        category_totals: Dict[str, float],
        remaining: float,
        discretionary_remaining: float,
        normalized_weekly_spend: float,
        normalized_recurring_monthly_total: float,
        goal_name: str,
        goal_amount: float,
        goal_timeline_months: int,
    ) -> List[Dict[str, object]]:
        if remaining <= 0:
            overspend = abs(remaining)
            cut_candidates = [
                (cat, amt)
                for cat, amt in sorted(category_totals.items(), key=lambda item: item[1], reverse=True)
                if cat not in self.LOW_CONTROL_CATEGORIES
            ][:3]
            targeted_cuts: List[Dict[str, object]] = []
            if cut_candidates:
                each_cut = overspend / len(cut_candidates)
                for category, amount in cut_candidates:
                    suggested = min(each_cut, amount * 0.2)
                    targeted_cuts.append(
                        self._tip(
                            text=f"Cut about ${suggested:.2f} from {category} this cycle.",
                            impact=suggested,
                            data_source="Current month category totals",
                            why=f"{category} is among the largest controllable categories this cycle.",
                        )
                    )

            return [
                self._tip(
                    text=f"You are over budget by ${overspend:.2f}. Freeze new non-essential spending for the next 7 days.",
                    impact=overspend,
                    data_source="Budget vs total spend",
                    why=f"Total spend exceeds budget by ${overspend:.2f}.",
                ),
                *targeted_cuts,
                self._tip(
                    text="Pause one low-value recurring subscription until spending is back under budget.",
                    impact=max(10.0, min(60.0, overspend * 0.1)),
                    data_source="Budget pressure heuristic",
                    why="Subscription pauses are usually quick to apply and reversible.",
                ),
            ][:3]

        tips: List[Dict[str, object]] = []
        if normalized_recurring_monthly_total > 0:
            tips.append(
                self._tip(
                    text=f"After averaging recurring expenses, your planning baseline is about ${normalized_weekly_spend:.2f} per week.",
                    impact=normalized_weekly_spend,
                    data_source="Recurring-expense normalization",
                    why=f"Includes about ${normalized_recurring_monthly_total:.2f}/month in recurring charges.",
                )
            )

        if discretionary_remaining < 0:
            deficit = abs(discretionary_remaining)
            tips.append(
                self._tip(
                    text=f"After fixed costs, discretionary spending is negative by ${deficit:.2f}. Trim variable categories before adding new purchases.",
                    impact=deficit,
                    data_source="Budget, fixed costs, and spend inputs",
                    why="Available money after fixed obligations is below zero.",
                )
            )
        else:
            weeks_left = max(1, (30 - date.today().day) // 7 + 1)
            weekly_cap = discretionary_remaining / weeks_left
            tips.append(
                self._tip(
                    text=f"Set a weekly variable-spend cap of about ${weekly_cap:.2f} for the rest of this month.",
                    impact=max(0.0, remaining - discretionary_remaining),
                    data_source="Remaining discretionary cash flow",
                    why=f"Spreads ${discretionary_remaining:.2f} across ~{weeks_left} week(s) left this month.",
                )
            )

        top_variable = [
            (cat, amt)
            for cat, amt in sorted(category_totals.items(), key=lambda item: item[1], reverse=True)
            if cat not in self.LOW_CONTROL_CATEGORIES
        ]
        if top_variable:
            cat, amt = top_variable[0]
            optimize = max(10.0, round(amt * 0.12, 2))
            tips.append(
                self._tip(
                    text=f"Your largest controllable category is {cat}. Aim to cut ${optimize:.2f} there next cycle.",
                    impact=optimize,
                    data_source="Top controllable category this month",
                    why=f"{cat} currently has the highest variable spend (${amt:.2f}).",
                )
            )

        if goal_name and goal_amount > 0 and goal_timeline_months > 0:
            monthly_target = goal_amount / goal_timeline_months
            suggested = min(max(0.0, remaining * 0.6), monthly_target)
            if suggested > 0:
                tips.append(
                    self._tip(
                        text=f"Put ${suggested:.2f} toward {goal_name} this month (target: ${monthly_target:.2f}/month).",
                        impact=suggested,
                        data_source="User-defined goal inputs",
                        why=f"Tracks toward {goal_name} within the stated timeline.",
                    )
                )
        elif remaining > 0:
            reserve = max(25.0, round(remaining * 0.3, 2))
            tips.append(
                self._tip(
                    text=f"Auto-transfer ${reserve:.2f} to savings now to lock in progress.",
                    impact=reserve,
                    data_source="Remaining monthly balance",
                    why="Immediate transfer reduces the chance of unplanned spending.",
                )
            )

        return tips[:3]

    @staticmethod
    def _tip(
        text: str,
        impact: float,
        data_source: str,
        why: str,
    ) -> Dict[str, object]:
        return {
            "text": text,
            "impact": round(max(0.0, impact), 2),
            "data_source": data_source,
            "why": why,
        }
