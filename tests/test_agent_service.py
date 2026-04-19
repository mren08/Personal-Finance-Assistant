import unittest

from agent_service import AgentService
from financial_state import build_monthly_summary


class FinancialStateTests(unittest.TestCase):
    def test_build_monthly_summary_computes_leftover_and_discretionary_remaining(self):
        summary = build_monthly_summary(
            monthly_income=4200,
            fixed_expenses=1800,
            tracked_spending=1450,
            recurring_monthly_total=90,
        )

        self.assertEqual(summary["leftover_money"], 2750)
        self.assertEqual(summary["discretionary_remaining"], 950)
        self.assertEqual(summary["recurring_monthly_total"], 90)

    def test_build_monthly_summary_reconciles_derived_values_with_rounded_inputs(self):
        summary = build_monthly_summary(
            monthly_income=100.005,
            fixed_expenses=20.005,
            tracked_spending=30.005,
            recurring_monthly_total=7.777,
        )

        self.assertEqual(summary["monthly_income"], 100.01)
        self.assertEqual(summary["fixed_expenses"], 20.01)
        self.assertEqual(summary["tracked_spending"], 30.01)
        self.assertEqual(summary["leftover_money"], 70.0)
        self.assertEqual(summary["discretionary_remaining"], 49.99)
        self.assertEqual(
            summary["leftover_money"],
            round(summary["monthly_income"] - summary["tracked_spending"], 2),
        )
        self.assertEqual(
            summary["discretionary_remaining"],
            round(
                summary["monthly_income"]
                - summary["fixed_expenses"]
                - summary["tracked_spending"],
                2,
            ),
        )

    def test_build_monthly_summary_allows_negative_values_when_month_is_overspent(self):
        summary = build_monthly_summary(
            monthly_income=1200,
            fixed_expenses=900,
            tracked_spending=1400,
            recurring_monthly_total=85,
        )

        self.assertEqual(summary["leftover_money"], -200)
        self.assertEqual(summary["discretionary_remaining"], -1100)

    def test_build_monthly_summary_treats_recurring_monthly_total_as_informational(self):
        baseline = build_monthly_summary(
            monthly_income=4200,
            fixed_expenses=1800,
            tracked_spending=1450,
            recurring_monthly_total=0,
        )
        with_recurring = build_monthly_summary(
            monthly_income=4200,
            fixed_expenses=1800,
            tracked_spending=1450,
            recurring_monthly_total=250,
        )

        self.assertEqual(with_recurring["leftover_money"], baseline["leftover_money"])
        self.assertEqual(
            with_recurring["discretionary_remaining"],
            baseline["discretionary_remaining"],
        )
        self.assertEqual(with_recurring["recurring_monthly_total"], 250)


class AgentServiceTests(unittest.TestCase):
    def test_agent_service_parses_llm_actions_and_notes(self):
        fake_response = {
            "reply": "You have $950 left after fixed costs. Dining is still the weak spot.",
            "actions": [
                {
                    "type": "save_agent_note",
                    "note_type": "behavior_pattern",
                    "content": "Dining remains the main pressure category.",
                }
            ],
        }

        service = AgentService(llm_client=lambda payload: fake_response)
        result = service.run_chat_turn(
            message="How much money do I have left this month?",
            agent_context={"monthly_summary": {"discretionary_remaining": 950}},
        )

        self.assertEqual(result["reply"], fake_response["reply"])
        self.assertEqual(result["actions"][0]["type"], "save_agent_note")

    def test_agent_service_includes_allowed_actions_and_filters_invalid_results(self):
        captured_payload = {}

        def fake_llm(payload):
            captured_payload.update(payload)
            return {
                "reply": "  Cancel Hulu if you are serious about creating more room. ",
                "actions": [
                    {"type": "mark_subscription_cancel", "merchant": "  Hulu  "},
                    {
                        "type": "save_agent_note",
                        "note_type": "behavior_pattern",
                        "content": "  Subscription count is crowding the monthly buffer.  ",
                    },
                    {"type": "unknown_action", "merchant": "Ignore me"},
                    {"type": "save_agent_note", "note_type": "", "content": "Missing type"},
                    "not-a-dict",
                ],
            }

        service = AgentService(llm_client=fake_llm)

        result = service.run_chat_turn(
            message="Should I keep Hulu?",
            agent_context={"monthly_summary": {"discretionary_remaining": 190}},
        )

        self.assertEqual(captured_payload["message"], "Should I keep Hulu?")
        self.assertEqual(
            captured_payload["context"],
            {"monthly_summary": {"discretionary_remaining": 190}},
        )
        self.assertIn("save_agent_note", captured_payload["allowed_action_types"])
        self.assertIn("mark_subscription_cancel", captured_payload["allowed_action_types"])
        self.assertEqual(result["reply"], "Cancel Hulu if you are serious about creating more room.")
        self.assertEqual(
            result["actions"],
            [
                {"type": "mark_subscription_cancel", "merchant": "Hulu"},
                {
                    "type": "save_agent_note",
                    "note_type": "behavior_pattern",
                    "content": "Subscription count is crowding the monthly buffer.",
                },
            ],
        )

    def test_agent_service_returns_safe_default_for_non_mapping_llm_response(self):
        service = AgentService(llm_client=lambda payload: "not-json")

        result = service.run_chat_turn(
            message="How bad is it?",
            agent_context={"monthly_summary": {"discretionary_remaining": -80}},
        )

        self.assertEqual(
            result,
            {
                "reply": "I couldn't produce a reliable coaching response right now.",
                "actions": [],
            },
        )
