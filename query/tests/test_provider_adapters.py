"""Unit tests for deterministic provider-flow selection."""

import pytest

from query_service.provider_adapters import flow_for, register_flow


pytestmark = pytest.mark.unit


def test_registered_provider_flow_wins_over_the_generic_llm_path() -> None:
    """An exact provider hostname selects its deterministic Playwright flow."""
    flow = lambda _page, _applicant, _credentials: ["email"]
    register_flow("flow-test.example", flow)

    assert flow_for("https://flow-test.example/listing/1") is flow
    assert flow_for("https://unregistered.example/listing/1") is None
