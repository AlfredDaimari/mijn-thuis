"""Constrained OpenRouter planning for unsupported provider forms.

The selected OpenRouter model receives no cookies, credentials, applicant values,
or full page HTML. It returns a tiny typed plan; Playwright validates it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


class OpenRouterPlanningError(RuntimeError):
    """A safe, human-readable explanation for a failed fallback plan."""


@dataclass(frozen=True)
class PlannedAction:
    action: str
    candidate_index: int


@dataclass(frozen=True)
class NavigationPlan:
    reason: str
    actions: tuple[PlannedAction, ...]


_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["reason", "actions"],
    "properties": {
        "reason": {"type": "string"},
        "actions": {
            "type": "array",
            "maxItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["action", "candidate_index"],
                "properties": {
                    "action": {"type": "string", "enum": ["click", "login"]},
                    "candidate_index": {"type": "integer", "minimum": 0},
                },
            },
        },
    },
}

_INSTRUCTIONS = """You plan exactly one smallest safe next step for a Dutch or English
housing-provider page. A deterministic Playwright form matcher has failed.
Return JSON matching the supplied schema. Choose `click` only for a supplied,
visible contact, interest, viewing, or application-navigation control. Choose
`login` only when the page visibly requires a provider account before its form;
the caller may have no credentials. Never choose submit, registration, consent,
payment, CAPTCHA, password, account-management, arbitrary selectors, URLs, or
form values. This worker fills a form for review but does not submit it."""


def planning_input(url: str, generic_failure_reason: str, candidates: list[dict[str, Any]]) -> str:
    """Build a redacted model input, including why generic matching failed."""
    return json.dumps(
        {
            "provider_host": (urlparse(url).hostname or "unknown").lower(),
            "generic_failure_reason": generic_failure_reason,
            "visible_navigation_candidates": candidates,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


class OpenRouterFormPlanner:
    """Use OpenRouter's OpenAI-compatible API after generic matching fails."""

    def __init__(
        self,
        api_key: str | None,
        model: str = "~openai/gpt-luna-latest",
        *,
        client: Any | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._client = client

    def plan(
        self, url: str, generic_failure_reason: str, candidates: list[dict[str, Any]]
    ) -> NavigationPlan:
        if not self._api_key:
            raise OpenRouterPlanningError(
                "OpenRouter fallback unavailable: openrouter_api_key is not configured; "
                f"generic failure: {generic_failure_reason}"
            )
        try:
            if self._client is None:
                from openai import OpenAI

                self._client = OpenAI(
                    api_key=self._api_key,
                    base_url="https://openrouter.ai/api/v1",
                )
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _INSTRUCTIONS},
                    {"role": "user", "content": planning_input(url, generic_failure_reason, candidates)},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "provider_navigation_plan",
                        "strict": True,
                        "schema": _PLAN_SCHEMA,
                    },
                },
                extra_body={"provider": {"require_parameters": True}},
            )
            payload = json.loads(response.choices[0].message.content)
        except Exception as error:
            raise OpenRouterPlanningError(
                f"OpenRouter fallback request failed; generic failure: {generic_failure_reason}; "
                f"detail: {error}"
            ) from error

        try:
            actions = tuple(
                PlannedAction(action=str(action["action"]), candidate_index=int(action["candidate_index"]))
                for action in payload["actions"]
            )
            reason = str(payload["reason"]).strip()
            if not reason or len(actions) > 1 or any(action.action not in {"click", "login"} for action in actions):
                raise ValueError("invalid plan action or reason")
            return NavigationPlan(reason=reason, actions=actions)
        except (KeyError, TypeError, ValueError) as error:
            raise OpenRouterPlanningError(
                f"OpenRouter returned an invalid navigation plan; generic failure: "
                f"{generic_failure_reason}; detail: {error}"
            ) from error
