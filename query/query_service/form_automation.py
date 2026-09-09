"""No-submit generic form matching and safe Gemini-directed navigation."""

from __future__ import annotations

from typing import Any

from .config import AccountCredentials, ApplicantProfile
from .gemini_form_planner import NavigationPlan


class FormAutomationError(RuntimeError):
    """The provider page cannot safely be filled by the generic workflow."""


_SAFE_NAVIGATION_WORDS = (
    "contact", "reageer", "interesse", "bezichtig", "apply", "application", "viewing",
)
_UNSAFE_NAVIGATION_WORDS = (
    "submit", "verzenden", "verstuur", "login", "log in", "inloggen", "register",
    "registr", "account", "password", "wachtwoord", "captcha", "payment", "betalen",
    "consent", "akkoord", "accept",
)


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
    if any(word in text for word in ("bericht", "message", "comment")) or metadata.get("tag") == "textarea":
        return "message"
    if "naam" in text or "name" in text:
        return "full_name"
    return None


def visible_navigation_candidates(page: Any) -> list[dict[str, Any]]:
    """Return a small, redacted description of visible possible navigation."""
    return page.locator("button, a, [role=button]").evaluate_all(
        """els => els.map((element, dom_index) => ({
            dom_index,
            tag: element.tagName.toLowerCase(),
            text: (element.innerText || '').trim().slice(0, 160),
            aria: element.getAttribute('aria-label') || '',
            href: element.getAttribute('href') || '',
            visible: !!(element.offsetWidth || element.offsetHeight || element.getClientRects().length)
        })).filter(candidate => candidate.visible).slice(0, 40)"""
    )


def _contact_form(page: Any) -> Any:
    forms = page.locator("form")
    for index in range(forms.count()):
        form = forms.nth(index)
        if form.is_visible() and form.locator("input, textarea").count():
            return form
    raise FormAutomationError("no visible contact form")


def fill_generic_form(page: Any, profile: ApplicantProfile) -> list[str]:
    """Fill recognised visible fields without submitting or changing consent."""
    form = _contact_form(page)
    fields = form.locator("input, textarea").evaluate_all(
        """els => els.map((element, index) => ({
            index, tag: element.tagName.toLowerCase(), name: element.name, id: element.id,
            placeholder: element.placeholder, aria: element.getAttribute('aria-label') || '',
            label: element.labels?.[0]?.innerText || '', type: element.type || ''
        }))"""
    )
    filled: list[str] = []
    for field in fields:
        kind = field_kind(field)
        if not kind or field.get("type") in {"hidden", "checkbox", "radio", "submit", "password"}:
            continue
        value = (
            f"{profile.first_name} {profile.last_name}"
            if kind == "full_name"
            else getattr(profile, kind)
        )
        form.locator("input, textarea").nth(field["index"]).fill(value)
        filled.append(kind)
    if not filled:
        raise FormAutomationError("visible form has no recognised writable applicant fields")
    return filled


def apply_navigation_plan(page: Any, plan: NavigationPlan) -> int:
    """Execute one allow-listed navigation click; submission is impossible here."""
    controls = page.locator("button, a, [role=button]")
    clicked = 0
    for action in plan.actions:
        if action.action != "click":
            raise FormAutomationError("Gemini requested a non-navigation action")
        if action.candidate_index < 0 or action.candidate_index >= controls.count():
            raise FormAutomationError(f"Gemini chose unavailable control index {action.candidate_index}")
        control = controls.nth(action.candidate_index)
        if not control.is_visible():
            raise FormAutomationError(f"Gemini chose hidden control index {action.candidate_index}")
        text = " ".join(
            filter(None, [control.inner_text(), control.get_attribute("aria-label") or ""])
        ).lower()
        if any(word in text for word in _UNSAFE_NAVIGATION_WORDS):
            raise FormAutomationError("Gemini chose a prohibited navigation control")
        if not any(word in text for word in _SAFE_NAVIGATION_WORDS):
            raise FormAutomationError("Gemini chose a control that is not contact/application navigation")
        control.click(timeout=10_000)
        page.wait_for_load_state("domcontentloaded", timeout=10_000)
        clicked += 1
    if not clicked:
        raise FormAutomationError(f"Gemini found no safe navigation action: {plan.reason}")
    return clicked


def login_to_provider(page: Any, credentials: AccountCredentials) -> None:
    """Fill a conventional provider login form and submit only that login request."""
    username = page.locator("input[type=email], input[name*=email i], input[name*=user i]").first
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
