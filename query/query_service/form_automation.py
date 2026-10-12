"""No-submit generic form matching and safe OpenRouter-directed navigation."""

from __future__ import annotations

from typing import Any

from .config import AccountCredentials, ApplicantProfile
from .openrouter_form_planner import NavigationPlan
from .provider_forms import ProviderFormError, prepare_form


class FormAutomationError(RuntimeError):
    """The provider page cannot safely be filled by the generic workflow."""


_SAFE_NAVIGATION_WORDS = (
    "contact",
    "reageer",
    "interesse",
    "bezichtig",
    "bezichtiging",
    "aanvraag",
    "aanvragen",
    "viewing",
    "request a viewing",
    "schedule a viewing",
    "apply",
    "application",
    "reageren",
    "informatie",
    "afspraak",
    "bezichting",
)
_UNSAFE_NAVIGATION_WORDS = (
    "submit",
    "verzenden",
    "verstuur",
    "login",
    "log in",
    "inloggen",
    "register",
    "registr",
    "account",
    "password",
    "wachtwoord",
    "captcha",
    "payment",
    "betalen",
    "consent",
    "akkoord",
    "accept",
)
_ACCOUNT_CREATION_WORDS = (
    "create account",
    "create an account",
    "sign up",
    "signup",
    "register",
    "registr",
    "account aanmaken",
    "maak een account",
    "nieuw account",
    "nieuwe account",
    "inschrijven",
)
_COOKIE_REJECT_WORDS = (
    "weiger",
    "afwij",
    "weigeren",
    "alleen noodzakelijke",
    "alleen functionele",
    "reject",
    "decline",
    "essential cookies",
    "necessary cookies",
    "only necessary",
    "essentiële",
    "essentiele",
    "enkel essenti",
    "alleen vereist",
)


def dismiss_cookie_banner(page: Any) -> bool:
    """Dismiss a cookie overlay only through a reject/necessary option.

    Cookie overlays commonly intercept provider navigation clicks.  Accepting
    all cookies is not an application prerequisite, so the worker never does
    that; it may choose an explicit reject/essential-only control instead.
    """
    controls = page.locator("button:visible, a:visible, [role=button]:visible")
    for index in range(controls.count()):
        control = controls.nth(index)
        text = (
            " ".join(
                filter(
                    None,
                    [control.inner_text(), control.get_attribute("aria-label") or ""],
                )
            )
            .strip()
            .lower()
        )
        if not any(word in text for word in _COOKIE_REJECT_WORDS):
            continue
        if "cookie" not in text and text not in {
            "weigeren",
            "alles weigeren",
            "afwijzen",
            "reject all",
            "reject",
            "decline",
        }:
            continue
        control.click(timeout=10_000)
        return True
    return False


def field_kind(metadata: dict[str, Any]) -> str | None:
    """Recognise Dutch labels first, then English labels."""
    text = " ".join(
        str(metadata.get(key, ""))
        for key in ("name", "id", "placeholder", "aria", "label", "type")
    ).lower()
    if "email" in text or "e-mail" in text:
        return "email"
    if any(word in text for word in ("telefoon", "mobiel", "phone", "tel")):
        return "phone"
    if "voornaam" in text or "first" in text:
        return "first_name"
    if any(word in text for word in ("achternaam", "last", "surname")):
        return "last_name"
    if (
        any(word in text for word in ("bericht", "message", "comment"))
        or metadata.get("tag") == "textarea"
    ):
        return "message"
    if "naam" in text or "name" in text:
        return "full_name"
    return None


def visible_navigation_candidates(page: Any) -> list[dict[str, Any]]:
    """Return a small, redacted description of visible possible navigation."""
    return page.locator(
        "button:visible, a:visible, [role=button]:visible"
    ).evaluate_all(
        """els => els.map((element, dom_index) => ({
            dom_index,
            id: element.id || '',
            tag: element.tagName.toLowerCase(),
            role: element.getAttribute('role') || '',
            type: element.getAttribute('type') || '',
            text: (element.innerText || '').trim().slice(0, 160),
            aria: element.getAttribute('aria-label') || '',
            title: element.getAttribute('title') || '',
            href: element.getAttribute('href') || '',
            disabled: !!element.disabled,
            visible: !!(element.offsetWidth || element.offsetHeight || element.getClientRects().length)
        })).slice(0, 120)
          .map((candidate, candidate_index) => ({...candidate, candidate_index}))"""
    )


def account_creation_candidates(
    candidates: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Return visible controls that start a new-account/registration flow.

    Existing-account login is handled separately when the user supplies a
    provider credential. Registration is deliberately never automated.
    """
    return [
        candidate
        for candidate in candidates
        if any(
            word
            in " ".join(
                (str(candidate.get("text", "")), str(candidate.get("aria", "")))
            ).lower()
            for word in _ACCOUNT_CREATION_WORDS
        )
    ]


def _contact_form(page: Any) -> Any:
    forms = page.locator("form")
    for index in range(forms.count()):
        form = forms.nth(index)
        if form.is_visible() and form.locator("input, textarea").count():
            return form
    raise FormAutomationError("no visible contact form")


def fill_generic_form(page: Any, profile: ApplicantProfile, message: str) -> list[str]:
    """Fill recognised visible fields without submitting or changing consent."""
    try:
        return prepare_form(page, profile, message).fields
    except ProviderFormError as error:
        raise FormAutomationError(str(error)) from None


def apply_navigation_plan(page: Any, plan: NavigationPlan) -> int:
    """Execute one allow-listed navigation click; submission is impossible here."""
    # This selector must match the candidate list above.  The model receives
    # the ordinal among visible candidates, not an index into hidden DOM
    # controls; using the unfiltered locator here can click the wrong element.
    controls = page.locator("button:visible, a:visible, [role=button]:visible")
    clicked = 0
    for action in plan.actions:
        if action.action != "click":
            raise FormAutomationError("OpenRouter requested a non-navigation action")
        if action.candidate_index < 0 or action.candidate_index >= controls.count():
            raise FormAutomationError(
                f"OpenRouter chose unavailable control index {action.candidate_index}"
            )
        control = controls.nth(action.candidate_index)
        if hasattr(control, "evaluate") and control.evaluate(
            "e => !!e.form && ((e.tagName==='BUTTON' && e.type==='submit') || (e.tagName==='INPUT' && e.type==='submit'))"
        ):
            raise FormAutomationError(
                "OpenRouter chose a form submission control during navigation"
            )
        text = " ".join(
            filter(
                None, [control.inner_text(), control.get_attribute("aria-label") or ""]
            )
        ).lower()
        label = text.strip()[:160] or "unlabelled control"
        if not control.is_visible():
            raise FormAutomationError(
                f"OpenRouter chose hidden control index {action.candidate_index} ({label})"
            )
        if any(word in text for word in _ACCOUNT_CREATION_WORDS):
            raise FormAutomationError(
                f"provider requires account creation before application ({label}); registration is not automated"
            )
        if any(word in text for word in _UNSAFE_NAVIGATION_WORDS):
            raise FormAutomationError(
                "OpenRouter chose a prohibited navigation control"
            )
        if not any(word in text for word in _SAFE_NAVIGATION_WORDS):
            raise FormAutomationError(
                "OpenRouter chose a control that is not contact/application navigation"
            )
        control.click(timeout=10_000)
        page.wait_for_load_state("domcontentloaded", timeout=10_000)
        clicked += 1
    if not clicked:
        raise FormAutomationError(
            f"OpenRouter found no safe navigation action: {plan.reason}"
        )
    return clicked


def login_to_provider(page: Any, credentials: AccountCredentials) -> None:
    """Fill a conventional provider login form and submit only that login request."""
    username = page.locator(
        "input[type=email], input[name*=email i], input[name*=user i]"
    ).first
    password = page.locator("input[type=password]").first
    if not username.is_visible() or not password.is_visible():
        raise FormAutomationError(
            "provider login was requested but no visible username/password fields were found"
        )
    username.fill(credentials.username)
    password.fill(credentials.password)
    submit = page.locator("button[type=submit], input[type=submit]").first
    if not submit.is_visible():
        raise FormAutomationError(
            "provider login was requested but no visible login submit control was found"
        )
    submit.click(timeout=10_000)
    page.wait_for_load_state("domcontentloaded", timeout=10_000)
