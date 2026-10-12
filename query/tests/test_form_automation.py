"""Safety rules for generic provider-form navigation."""

import pytest

from query_service.form_automation import (
    FormAutomationError,
    account_creation_candidates,
    apply_navigation_plan,
    dismiss_cookie_banner,
)
from query_service.openrouter_form_planner import NavigationPlan, PlannedAction


pytestmark = pytest.mark.unit


def test_account_creation_controls_are_detected_in_dutch_and_english() -> None:
    """Registration is stopped, while existing-account login remains separately possible."""
    candidates = [
        {"dom_index": 12, "text": "Inloggen", "aria": ""},
        {"dom_index": 150, "text": "INSCHRIJVEN & BEZICHTIGEN", "aria": ""},
        {"dom_index": 151, "text": "", "aria": "Create account"},
    ]

    assert [candidate["dom_index"] for candidate in account_creation_candidates(candidates)] == [150, 151]


def test_openrouter_cannot_click_a_registration_control() -> None:
    """Even a model-selected registration button is rejected before click()."""
    control = _FakeControl("INSCHRIJVEN & BEZICHTIGEN")
    page = _FakePage(control)
    plan = NavigationPlan("Register first", (PlannedAction("click", 0),))

    with pytest.raises(FormAutomationError, match="account creation.*not automated"):
        apply_navigation_plan(page, plan)

    assert not control.clicked


def test_openrouter_can_click_a_dutch_viewing_request_control() -> None:
    """A Dutch request-viewing action is safe navigation, not registration."""
    control = _FakeControl("Bezichtiging aanvragen")
    page = _FakePage(control)
    plan = NavigationPlan("Open the viewing request form", (PlannedAction("click", 0),))

    assert apply_navigation_plan(page, plan) == 1
    assert control.clicked
    assert page.selector == "button:visible, a:visible, [role=button]:visible"


def test_cookie_overlay_uses_only_reject_or_necessary_option() -> None:
    reject = _FakeControl("Alleen noodzakelijke cookies")
    accept = _FakeControl("Alle cookies accepteren")
    page = _FakePage(reject, accept)

    assert dismiss_cookie_banner(page)
    assert reject.clicked
    assert not accept.clicked


class _FakeControl:
    def __init__(self, text: str) -> None:
        self.text = text
        self.clicked = False

    def inner_text(self) -> str:
        return self.text

    def get_attribute(self, _name: str) -> str | None:
        return None

    def is_visible(self) -> bool:
        return True

    def click(self, **_kwargs) -> None:
        self.clicked = True


class _FakeControls:
    def __init__(self, *controls: _FakeControl) -> None:
        self.controls = controls

    def count(self) -> int:
        return len(self.controls)

    def nth(self, index: int) -> _FakeControl:
        return self.controls[index]


class _FakePage:
    def __init__(self, *controls: _FakeControl) -> None:
        self.controls = _FakeControls(*controls)
        self.selector = None

    def locator(self, selector: str) -> _FakeControls:
        self.selector = selector
        return self.controls

    def wait_for_load_state(self, *_args, **_kwargs) -> None:
        return None
