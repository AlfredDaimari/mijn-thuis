"""Constrained Gemini 2.5 Flash planning for unsupported provider forms.

Gemini receives no cookies, credentials, applicant values, or full page HTML.
It returns only a tiny typed plan; Playwright validates and executes it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


class GeminiPlanningError(RuntimeError):
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
    """Build the redacted Gemini input, including why generic matching failed."""
    return json.dumps(
        {
            "provider_host": (urlparse(url).hostname or "unknown").lower(),
            "generic_failure_reason": generic_failure_reason,
            "visible_navigation_candidates": candidates,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


class GeminiFormPlanner:
    """Call Gemini only after generic matching fails, using typed JSON output."""

    def __init__(
        self, api_key: str | None, model: str = "gemini-2.5-flash", *, client: Any | None = None
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._client = client

    def plan(
        self, url: str, generic_failure_reason: str, candidates: list[dict[str, Any]]
    ) -> NavigationPlan:
        if not self._api_key:
            raise GeminiPlanningError(
                "Gemini fallback unavailable: gemini_api_key is not configured; "
                f"generic failure: {generic_failure_reason}"
            )
        try:
            if self._client is None:
                from google import genai
                from google.genai import types

                self._client = genai.Client(api_key=self._api_key)
                config: Any = types.GenerateContentConfig(
                    response_mime_type="application/json", response_schema=_PLAN_SCHEMA
                )
            else:
                config = {
                    "response_mime_type": "application/json",
                    "response_schema": _PLAN_SCHEMA,
                }
            response = self._client.models.generate_content(
                model=self._model,
                contents=f"{_INSTRUCTIONS}\n\n{planning_input(url, generic_failure_reason, candidates)}",
                config=config,
            )
            payload = json.loads(response.text)
        except Exception as error:
            raise GeminiPlanningError(
                f"Gemini fallback request failed; generic failure: {generic_failure_reason}; "
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
            raise GeminiPlanningError(
                f"Gemini returned an invalid navigation plan; generic failure: "
                f"{generic_failure_reason}; detail: {error}"
            ) from error
