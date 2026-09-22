"""Unit tests for the constrained OpenRouter navigation fallback."""

import json

import pytest

from query_service.openrouter_form_planner import OpenRouterFormPlanner, planning_input


pytestmark = pytest.mark.unit


class FakeCompletions:
    def __init__(self) -> None:
        self.request = None

    def create(self, **kwargs):
        self.request = kwargs
        message = type(
            "Message",
            (),
            {"content": json.dumps({"reason": "Open contact form", "actions": [{"action": "click", "candidate_index": 2}]})},
        )()
        return type("Response", (), {"choices": [type("Choice", (), {"message": message})()]})()


class FakeClient:
    def __init__(self) -> None:
        self.chat = type("Chat", (), {"completions": FakeCompletions()})()


def test_planner_prompt_retains_the_deterministic_failure_reason() -> None:
    """OpenRouter receives the generic failure, never applicant data."""
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


def test_planner_uses_openrouter_structured_plan_and_no_unrestricted_actions() -> None:
    """The OpenRouter model can return only one typed Playwright step."""
    client = FakeClient()
    planner = OpenRouterFormPlanner("test-key", client=client)

    plan = planner.plan(
        "https://provider.example/listing/4",
        "no visible contact form",
        [{"dom_index": 2, "text": "Contact opnemen", "visible": True}],
    )

    assert plan.reason == "Open contact form"
    assert [(action.action, action.candidate_index) for action in plan.actions] == [("click", 2)]
    request = client.chat.completions.request
    assert request["model"] == "~openai/gpt-luna-latest"
    assert request["response_format"]["type"] == "json_schema"
    assert request["extra_body"]["provider"]["require_parameters"] is True
