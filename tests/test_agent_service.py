import sys
import types
import unittest
from unittest.mock import patch

from agent_service import AgentService, _parse_json_response, _summarize_context
from financial_state import build_monthly_summary


class FinancialStateTests(unittest.TestCase):
    def test_build_monthly_summary_computes_leftover_and_discretionary_remaining(self):
        summary = build_monthly_summary(
            monthly_income=4200,
            fixed_expenses=1800,
            tracked_spending=1450,
            recurring_monthly_total=90,
        )

        self.assertEqual(summary["available_before_fixed"], 2750)
        self.assertEqual(summary["leftover_money"], 950)
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
        self.assertEqual(summary["available_before_fixed"], 70.0)
        self.assertEqual(summary["leftover_money"], 49.99)
        self.assertEqual(
            summary["available_before_fixed"],
            round(summary["monthly_income"] - summary["tracked_spending"], 2),
        )
        self.assertEqual(
            summary["leftover_money"],
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

        self.assertEqual(summary["available_before_fixed"], -200)
        self.assertEqual(summary["leftover_money"], -1100)

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

        self.assertEqual(with_recurring["available_before_fixed"], baseline["available_before_fixed"])
        self.assertEqual(
            with_recurring["leftover_money"],
            baseline["leftover_money"],
        )
        self.assertEqual(with_recurring["recurring_monthly_total"], 250)


class AgentServiceTests(unittest.TestCase):
    def test_parse_json_response_accepts_fenced_json(self):
        parsed = _parse_json_response(
            """```json
            {"reply":"ok","actions":[]}
            ```"""
        )

        self.assertEqual(parsed, {"reply": "ok", "actions": []})

    def test_context_summary_includes_recent_messages_and_subscription_context(self):
        summary = _summarize_context(
            {
                "message": "Should I keep it?",
                "context": {
                    "selected_month_label": "April 2026",
                    "monthly_summary": {"leftover_money": 950},
                    "financial_profile": {"budgeting_goal": "Trim subscriptions"},
                    "category_breakdown": [{"category": "Dining", "amount": 120.0}],
                    "subscriptions": [{"merchant": "Netflix", "monthly_equivalent": 15.49}],
                    "agent_notes": [{"note_type": "April 2026 focus", "content": "Watch dining."}],
                    "messages": [
                        {"role": "assistant", "content": "I noticed Netflix."},
                        {"role": "user", "content": "Should I keep it?"},
                    ],
                },
                "allowed_action_types": ["mark_subscription_keep"],
            }
        )

        self.assertIn("Latest user message: Should I keep it?", summary)
        self.assertIn("Selected month: April 2026", summary)
        self.assertIn('"merchant": "Netflix"', summary)
        self.assertIn('"content": "I noticed Netflix."', summary)

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

    def test_agent_service_compacts_dense_reply_formatting(self):
        dense_reply = (
            "A good alternative depends on what you like most about Club Pilates: "
            "- If you want a similar low-impact strength workout: try YouTube or app-based Pilates classes. "
            "- If you mainly want structure and accountability: use a cheaper gym plus one class pack. "
            "- If you want the same core benefits for less money: mat Pilates and yoga are solid swaps. "
            "The best budget-friendly alternative is at-home Pilates plus one low-cost fitness option."
        )

        service = AgentService(llm_client=lambda payload: {"reply": dense_reply, "actions": []})
        result = service.run_chat_turn("what alternatives do i have?", {"subscriptions": []})

        self.assertIn("\n- If you want", result["reply"])
        self.assertLessEqual(len(result["reply"]), len(dense_reply))

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

    def test_agent_service_returns_safe_default_when_llm_client_raises(self):
        def exploding_llm(payload):
            raise RuntimeError("network failure")

        service = AgentService(llm_client=exploding_llm)

        result = service.run_chat_turn(
            message="Should I keep Netflix?",
            agent_context={"monthly_summary": {"discretionary_remaining": 25}},
        )

        self.assertEqual(
            result,
            {
                "reply": "I couldn't produce a reliable coaching response right now.",
                "actions": [],
            },
        )

    def test_openai_client_uses_conversational_system_prompt(self):
        captured = {}

        class FakeResponses:
            def create(self, **kwargs):
                captured.update(kwargs)
                return type("FakeResponse", (), {"output_text": '{"reply":"ok","actions":[]}'})()

        class FakeOpenAI:
            def __init__(self):
                self.responses = FakeResponses()

        fake_module = types.ModuleType("openai")
        fake_module.OpenAI = FakeOpenAI

        with patch.dict(sys.modules, {"openai": fake_module}):
            from agent_service import build_openai_llm_client

            client = build_openai_llm_client()
            client(
                {
                    "message": "Should I keep Netflix?",
                    "context": {"messages": [{"role": "assistant", "content": "Netflix costs $15.49"}]},
                    "allowed_action_types": ["mark_subscription_keep"],
                }
            )

        self.assertEqual(captured["model"], "gpt-5.4-mini")
        self.assertIn("Behave like a thoughtful, conversational assistant", captured["input"][0]["content"])
        self.assertIn("Keep replies concise and easy to scan", captured["input"][0]["content"])
        self.assertIn("ask which city they are in", captured["input"][0]["content"])
        self.assertIn("Latest user message: Should I keep Netflix?", captured["input"][1]["content"])

    def test_openai_client_falls_back_to_second_model_when_first_fails(self):
        calls = []

        class FakeResponses:
            def create(self, **kwargs):
                calls.append(kwargs["model"])
                if kwargs["model"] == "gpt-5.4-mini":
                    raise RuntimeError("model unavailable")
                return type("FakeResponse", (), {"output_text": '```json {"reply":"ok","actions":[]} ```'})()

        class FakeOpenAI:
            def __init__(self):
                self.responses = FakeResponses()

        fake_module = types.ModuleType("openai")
        fake_module.OpenAI = FakeOpenAI

        with patch.dict(sys.modules, {"openai": fake_module}), patch.dict("os.environ", {}, clear=False):
            from agent_service import build_openai_llm_client

            client = build_openai_llm_client()
            result = client({"message": "Hi", "context": {}, "allowed_action_types": []})

        self.assertEqual(calls, ["gpt-5.4-mini", "gpt-5-mini"])
        self.assertEqual(result, {"reply": "ok", "actions": []})
