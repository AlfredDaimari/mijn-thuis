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

_INSTRUCTIONS = """You are the navigation planner for a Dutch-first housing listing
workflow. Deterministic form matching failed, so infer the current page state from the
redacted visible candidates and return exactly one smallest safe next step as JSON.
`visible_navigation_candidates` is an ordered list. Use its explicit zero-based
`candidate_index` exactly; never use `dom_index`, text position, a guessed selector, or a URL.

Reason in this order, and do not skip a higher-priority state:
1. Cookie/privacy overlay: do not accept all cookies, consent, or marketing. The deterministic
   worker handles an explicit Dutch reject/essential-only option; if none is visible, return no
   action rather than clicking a random control.
2. A visible applicant contact form: do not navigate. Search bars, newsletter forms and
   password login forms are not applicant contact forms. The caller will fill fields. A visible `Verzenden`,
   `Verstuur`, `Aanvragen`, `Submit`, or `Reageer` control at the end of a form is submission,
   not navigation, and must never be selected.
3. A listing CTA that reveals the form: choose the smallest supplied visible control whose Dutch
   meaning is clearly interest/contact/viewing navigation. Prefer `Ik heb interesse`, `Interesse
   tonen`, `Reageer op deze woning`, `Reageren`, `Contact opnemen`, `Neem contact op`,
   `Bezichtiging aanvragen`, `Vraag een bezichtiging aan`, `Plan een bezichtiging`, `Afspraak
   maken`, `Meer informatie`, or `Bekijk woning`. English equivalents include `I’m interested`,
   `Contact`, `Contact us`, `Request a viewing`, `Schedule a viewing`, and `Apply`.
4. Existing-account gate: choose `login` only when the page visibly asks for an existing account
   and credentials may be configured. `Inloggen`/`Log in` is not registration.
5. Confirmation, error, CAPTCHA, payment, or account-creation state: return no action and explain
   the state. Never attempt to bypass it.

Dutch is first-class: understand inflections such as `interesse`, `reageer`, `reageren`,
`bezichtiging`, `bezichtigingen`, `aanvraag`, and `aanvragen`, but treat `Inschrijven`,
`Registreren`, `Account aanmaken`, and `Aanmelden` as registration unless the surrounding label
clearly proves it means an existing login. Hash anchors such as `#subscribe-form` are safe only
when the supplied label clearly opens that contact/viewing form.

The optional `applicant_context` contains only non-identifying facts relevant to provider fields,
for example `current_housing_situation: Huurwoning` and
`registered_in_netherlands: true`. Use those facts to explain which Dutch form state is expected,
but never invent a value, expose personal contact data, or treat context as permission to accept
consent or submit.

Never choose consent/terms/privacy, `Akkoord`, `Accept`, `Alle cookies accepteren`, password,
CAPTCHA, payment, account creation, registration, arbitrary navigation, form values, or a final
submission control. This worker prepares evidence only; it does not submit, create accounts,
accept consent, or perform credit checks."""


def planning_input(
    url: str,
    generic_failure_reason: str,
    candidates: list[dict[str, Any]],
    applicant_context: dict[str, str | bool] | None = None,
    page_state: dict[str, Any] | None = None,
) -> str:
    """Build a redacted model input, including why generic matching failed."""
    return json.dumps(
        {
            "provider_host": (urlparse(url).hostname or "unknown").lower(),
            "page_path": urlparse(url).path or "/",
            "generic_failure_reason": generic_failure_reason,
            "visible_navigation_candidates": candidates,
            "applicant_context": applicant_context or {},
            "page_state": page_state or {},
        },
        ensure_ascii=False,
        sort_keys=True,
    )


class OpenRouterFormPlanner:
    """Use OpenRouter's compatible API after generic matching fails.

    The ``openai`` package below is only an HTTP client for the compatible
    protocol; authentication, routing, billing, and the model selection are
    all OpenRouter through its base URL and ``openrouter_api_key``.
    """

    def __init__(
        self,
        api_key: str | None,
        model: str = "openai/gpt-6-luna",
        *,
        client: Any | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._client = client

    def plan(
        self,
        url: str,
        generic_failure_reason: str,
        candidates: list[dict[str, Any]],
        *,
        applicant_context: dict[str, str | bool] | None = None,
        page_state: dict[str, Any] | None = None,
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
                    {
                        "role": "user",
                        "content": planning_input(
                            url,
                            generic_failure_reason,
                            candidates,
                            applicant_context,
                            page_state,
                        ),
                    },
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
                PlannedAction(
                    action=str(action["action"]),
                    candidate_index=int(action["candidate_index"]),
                )
                for action in payload["actions"]
            )
            reason = str(payload["reason"]).strip()
            if (
                not reason
                or len(actions) > 1
                or any(action.action not in {"click", "login"} for action in actions)
            ):
                raise ValueError("invalid plan action or reason")
            return NavigationPlan(reason=reason, actions=actions)
        except (KeyError, TypeError, ValueError) as error:
            raise OpenRouterPlanningError(
                f"OpenRouter returned an invalid navigation plan; generic failure: "
                f"{generic_failure_reason}; detail: {error}"
            ) from error
