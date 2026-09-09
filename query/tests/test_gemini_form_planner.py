"""Unit tests for the constrained Gemini navigation fallback."""

import json

import pytest

from query_service.gemini_form_planner import GeminiFormPlanner, planning_input


pytestmark = pytest.mark.unit


class FakeModels:
    def __init__(self) -> None:
        self.request = None

    def generate_content(self, **kwargs):
        self.request = kwargs
        return type(
            "Response",
            (),
            {"text": json.dumps({"reason": "Open contact form", "actions": [{"action": "click", "candidate_index": 2}]})},
        )()


class FakeClient:
    def __init__(self) -> None:
        self.models = FakeModels()


def test_planner_prompt_retains_the_deterministic_failure_reason() -> None:
    """Gemini is told exactly why generic matching failed, without applicant data."""
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


def test_planner_uses_gemini_structured_plan_and_no_unrestricted_actions() -> None:
    """The model can return only one typed Playwright step for validation."""
    client = FakeClient()
    planner = GeminiFormPlanner("test-key", client=client)

    plan = planner.plan(
        "https://provider.example/listing/4",
        "no visible contact form",
        [{"dom_index": 2, "text": "Contact opnemen", "visible": True}],
    )

    assert plan.reason == "Open contact form"
    assert [(action.action, action.candidate_index) for action in plan.actions] == [("click", 2)]
    assert client.models.request["model"] == "gemini-2.5-flash"
    assert client.models.request["config"]["response_mime_type"] == "application/json"
