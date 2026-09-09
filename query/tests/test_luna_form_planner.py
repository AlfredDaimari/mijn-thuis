"""Unit tests for the constrained Luna navigation fallback."""

import json

import pytest

from query_service.luna_form_planner import LunaFormPlanner, planning_input


pytestmark = pytest.mark.unit


class FakeResponses:
    def __init__(self) -> None:
        self.request = None

    def create(self, **kwargs):
        self.request = kwargs
        return type(
            "Response",
            (),
            {"output_text": json.dumps({"reason": "Open contact form", "actions": [{"action": "click", "candidate_index": 2}]})},
        )()


class FakeClient:
    def __init__(self) -> None:
        self.responses = FakeResponses()


def test_planner_prompt_retains_the_deterministic_failure_reason() -> None:
    """Luna is told exactly why generic matching failed, without applicant data."""
    prompt = json.loads(
        planning_input(
            "https://provider.example/listing/4",
            "no visible contact form",
            [{"dom_index": 2, "text": "Contact opnemen", "visible": True}],
        )
    )

    assert prompt["generic_failure_reason"] == "no visible contact form"
    assert prompt["provider_host"] == "provider.example"
    assert "applicant" not in prompt


def test_planner_uses_luna_structured_plan_and_no_unrestricted_actions() -> None:
    """Only a numbered click action from the strict response schema is returned."""
    client = FakeClient()
    planner = LunaFormPlanner("test-key", client=client)

    plan = planner.plan(
        "https://provider.example/listing/4",
        "no visible contact form",
        [{"dom_index": 2, "text": "Contact opnemen", "visible": True}],
    )

    assert plan.reason == "Open contact form"
    assert [action.candidate_index for action in plan.actions] == [2]
    assert client.responses.request["model"] == "gpt-5.6-luna"
    assert client.responses.request["store"] is False
    assert client.responses.request["text"]["format"]["type"] == "json_schema"
