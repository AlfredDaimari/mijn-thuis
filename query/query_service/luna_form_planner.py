"""Constrained GPT-5.6 Luna planning for unsupported provider forms.

Luna receives no cookies, credentials, applicant values, or full page HTML. It
can only select a numbered, visible navigation control. Playwright validates
the result and remains responsible for all browser actions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


class LunaPlanningError(RuntimeError):
    """A safe, human-readable explanation for a failed fallback plan."""


@dataclass(frozen=True)
class NavigationAction:
    candidate_index: int


@dataclass(frozen=True)
class NavigationPlan:
    reason: str
    actions: tuple[NavigationAction, ...]


_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["reason", "actions"],
    "properties": {
        "reason": {"type": "string"},
        "actions": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["action", "candidate_index"],
                "properties": {
                    "action": {"type": "string", "const": "click"},
                    "candidate_index": {"type": "integer", "minimum": 0},
                },
            },
        },
    },
}

_INSTRUCTIONS = """You plan the smallest safe navigation step for a Dutch or English
housing-provider page. A deterministic Playwright form matcher already failed.
Return JSON matching the schema. You may choose only a candidate index supplied
by the caller, and only when it appears to reveal a contact, interest, viewing,
or application form. Do not choose login, registration, account, consent,
payment, CAPTCHA, password, or submit controls. Do not invent selectors,
URLs, form values, or actions. If no safe action is available, return actions
as an empty array and explain why. This is a no-submit evaluation."""


def planning_input(url: str, generic_failure_reason: str, candidates: list[dict[str, Any]]) -> str:
    """Build the redacted planner input, retaining the original failure reason."""
    return json.dumps(
        {
            "provider_host": (urlparse(url).hostname or "unknown").lower(),
            "generic_failure_reason": generic_failure_reason,
            "visible_navigation_candidates": candidates,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


class LunaFormPlanner:
    """Ask Luna for a tightly validated navigation plan only when configured."""

    def __init__(
        self, api_key: str | None, model: str = "gpt-5.6-luna", *, client: Any | None = None
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._client = client

    def plan(
        self, url: str, generic_failure_reason: str, candidates: list[dict[str, Any]]
    ) -> NavigationPlan:
        if not self._api_key:
            raise LunaPlanningError(
                "Luna fallback unavailable: openai_api_key is not configured; "
                f"generic failure: {generic_failure_reason}"
            )
        if not candidates:
            raise LunaPlanningError(
                "Luna fallback skipped: no visible navigation candidates; "
                f"generic failure: {generic_failure_reason}"
            )
        try:
            if self._client is None:
                from openai import OpenAI

                self._client = OpenAI(api_key=self._api_key)
            response = self._client.responses.create(
                model=self._model,
                store=False,
                instructions=_INSTRUCTIONS,
                input=planning_input(url, generic_failure_reason, candidates),
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "provider_navigation_plan",
                        "strict": True,
                        "schema": _PLAN_SCHEMA,
                    }
                },
            )
            payload = json.loads(response.output_text)
        except LunaPlanningError:
            raise
        except Exception as error:
            raise LunaPlanningError(
                f"Luna fallback request failed; generic failure: {generic_failure_reason}; "
                f"detail: {error}"
            ) from error

        try:
            actions = tuple(
                NavigationAction(candidate_index=int(action["candidate_index"]))
                for action in payload["actions"]
                if action["action"] == "click"
            )
            reason = str(payload["reason"]).strip()
            if not reason:
                raise ValueError("empty plan reason")
            if len(actions) != len(payload["actions"]):
                raise ValueError("unsupported action")
            return NavigationPlan(reason=reason, actions=actions)
        except (KeyError, TypeError, ValueError) as error:
            raise LunaPlanningError(
                f"Luna returned an invalid navigation plan; generic failure: "
                f"{generic_failure_reason}; detail: {error}"
            ) from error
