"""Dutch provider forms: discover, fill, validate, submit and verify outcomes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .config import ApplicantProfile


class ProviderFormError(RuntimeError):
    """A bounded diagnostic that never contains applicant values or browser traces."""


@dataclass
class PreparedForm:
    form: Any
    fields: list[str]


_META = """els => els.map((e, index) => {
    e.setAttribute('data-house-field', String(index));
    const style = getComputedStyle(e);
    return {index, tag:e.tagName.toLowerCase(), type:e.type || '', name:e.name || '',
        id:e.id || '', placeholder:e.getAttribute('placeholder') || '',
        autocomplete:e.getAttribute('autocomplete') || '', aria:e.getAttribute('aria-label') || '',
        label:[...(e.labels || [])].map(l=>l.innerText).join(' '),
        visible:!!e.getClientRects().length && style.visibility !== 'hidden' && style.display !== 'none',
        disabled:!!e.disabled, readonly:!!e.readOnly, required:!!e.required,
        role:e.getAttribute('role') || ''};
})"""


def field_kind(meta: dict[str, Any]) -> str | None:
    text = " ".join(
        str(meta.get(k) or "")
        for k in ("name", "id", "placeholder", "aria", "label", "autocomplete", "type")
    ).lower()
    if any(
        s in text
        for s in ("captcha", "honeypot", "_additional_info", "website_url", "fax")
    ):
        return None
    if any(
        s in text for s in ("woonsituatie", "currentsituation", "housing_situation")
    ):
        return "current_housing_situation"
    if any(s in text for s in ("registeredinnetherlands", "ingeschreven in nederland")):
        return "registered_in_netherlands"
    if any(s in text for s in ("email", "e-mail", "e-mailadres")) or re.search(
        r"\S+@\S+\.\S+", text
    ):
        return "email"
    if (
        any(s in text for s in ("telefoon", "mobiel", "phone", "telephone"))
        or meta.get("type") == "tel"
        or re.search(r"\b06\s", text)
    ):
        return "phone"
    if any(s in text for s in ("voornaam", "first", "given-name")):
        return "first_name"
    if any(s in text for s in ("achternaam", "last", "surname", "family-name")):
        return "last_name"
    if any(s in text for s in ("bericht", "message", "comment", "opmerking", "vraag")):
        return "message"
    if meta.get("tag") == "textarea":
        return "message"
    if any(s in text for s in ("naam", "name")):
        return "full_name"
    return None


def find_contact_form(page: Any) -> Any:
    """Score every visible form, including frames; search/newsletter forms lose."""
    ranked = []
    for frame in page.frames:
        forms = frame.locator("form:visible")
        for i in range(forms.count()):
            form = forms.nth(i)
            metadata = form.locator("input, textarea, select").evaluate_all(_META)
            usable = [
                m
                for m in metadata
                if m["visible"]
                and not m["disabled"]
                and m["type"]
                not in {"hidden", "submit", "password", "checkbox", "radio"}
            ]
            kinds = {field_kind(m) for m in usable} - {None}
            if "email" not in kinds or not kinds.intersection(
                {"phone", "first_name", "full_name", "message"}
            ):
                continue
            ranked.append((len(kinds), form))
    if not ranked:
        raise ProviderFormError(
            "contact_form_missing: no visible applicant contact form"
        )
    return max(ranked, key=lambda item: item[0])[1]


def set_boolean(control: Any, value: bool) -> None:
    """Use the visible ARIA wrapper for Quasar and other custom checkboxes."""
    if control.is_visible():
        control.set_checked(value, timeout=5000)
    else:
        wrapper = control.locator('xpath=ancestor::*[@role="checkbox"][1]')
        if wrapper.count() and wrapper.is_visible():
            if (wrapper.get_attribute("aria-checked") == "true") != value:
                wrapper.click(timeout=5000)
            if (wrapper.get_attribute("aria-checked") == "true") != value:
                raise ProviderFormError(
                    "form_validation: custom checkbox did not update"
                )
        else:
            labels = control.evaluate(
                "e => [...(e.labels||[])].map(l=>l.getAttribute('for'))"
            )
            if not labels or not control.get_attribute("id"):
                raise ProviderFormError(
                    "form_validation: no accessible checkbox control"
                )
            # Locator escaping is delegated to a generated attribute selector.
            import json

            label = control.page.locator(
                "label[for=" + json.dumps(control.get_attribute("id")) + "]"
            ).first
            if control.is_checked() != value:
                label.click(timeout=5000)
            if control.is_checked() != value:
                raise ProviderFormError("form_validation: checkbox did not update")


def select_housing(control: Any, value: str) -> None:
    wanted = value.casefold().strip()
    aliases = (
        {"huurwoning", "ik huur een woning", "huur", "huurder", "ik huur", "renting"}
        if wanted in {"huurwoning", "renting"}
        else {wanted}
    )
    if control.evaluate("e => e.tagName") == "SELECT":
        options = control.locator("option").evaluate_all(
            "els=>els.map(e=>({label:e.textContent.trim(),value:e.value}))"
        )
        matches = [o for o in options if o["label"].casefold() in aliases]
        if len(matches) != 1:
            raise ProviderFormError(
                "missing_profile_field: housing situation has no matching option"
            )
        control.select_option(value=matches[0]["value"])
    else:
        from .form_automation import dismiss_cookie_banner

        control.scroll_into_view_if_needed()
        control.page.wait_for_timeout(300)
        dismiss_cookie_banner(control.page)
        scope = control.locator(
            'xpath=ancestor::*[contains(concat(" ",normalize-space(@class)," ")," q-select ")][1]'
        )
        opener = scope.locator(".q-field__control").first if scope.count() else control
        opener.click(timeout=5000)
        options = control.page.locator("[role=option]:visible, .q-menu:visible .q-item")
        options.first.wait_for(state="visible", timeout=5000)
        dismiss_cookie_banner(control.page)
        matches = [
            options.nth(i)
            for i in range(options.count())
            if options.nth(i).inner_text().strip().casefold() in aliases
        ]
        if len(matches) != 1:
            raise ProviderFormError(
                "missing_profile_field: housing situation has no matching option"
            )
        matches[0].click(timeout=5000)
        selected = (
            scope.inner_text().casefold()
            if scope.count()
            else control.input_value().casefold()
        )
        if not any(alias in selected for alias in aliases):
            raise ProviderFormError(
                "form_validation: housing situation selection did not persist"
            )


def prepare_form(page: Any, profile: ApplicantProfile, message: str) -> PreparedForm:
    form = find_contact_form(page)
    metadata = form.locator("input, textarea, select").evaluate_all(_META)
    filled = []
    expected = []
    for meta in metadata:
        kind = field_kind(meta)
        if (
            not kind
            or meta["disabled"]
            or meta["type"] in {"hidden", "password", "submit", "radio"}
        ):
            continue
        control = form.locator('[data-house-field="' + str(meta["index"]) + '"]')
        if kind == "registered_in_netherlands":
            if profile.registered_in_netherlands is not None:
                set_boolean(control, profile.registered_in_netherlands)
                filled.append(kind)
            continue
        if not meta["visible"] or meta["type"] == "checkbox":
            continue
        if kind == "current_housing_situation":
            if not profile.current_housing_situation:
                raise ProviderFormError(
                    "missing_profile_field: current_housing_situation"
                )
            select_housing(control, profile.current_housing_situation)
            filled.append(kind)
            continue
        if meta["readonly"]:
            continue
        value = (
            f"{profile.first_name} {profile.last_name}"
            if kind == "full_name"
            else message
            if kind == "message"
            else getattr(profile, kind)
        )
        try:
            from .form_automation import dismiss_cookie_banner

            control.scroll_into_view_if_needed()
            dismiss_cookie_banner(page)
            control.fill(value, timeout=5000)
            control.press("Tab", timeout=5000)
            expected.append((control, value, kind))
            filled.append(kind)
        except Exception:
            raise ProviderFormError(
                "form_matching_failed: could not fill " + kind
            ) from None
    # Some Vue pages finish initializing only after the form scrolls into
    # view. Refill erased values with normal browser events, then verify again.
    for attempt in range(3):
        page.wait_for_timeout(300)
        changed = [
            (control, value, kind)
            for control, value, kind in expected
            if control.input_value() != value
        ]
        if not changed:
            break
        if attempt == 2:
            raise ProviderFormError(
                "form_validation: value did not persist for " + changed[0][2]
            )
        for control, value, _ in changed:
            control.fill("", timeout=5000)
            # Some provider controls update their model on keyboard events,
            # not input alone. Use normal typing for the bounded retry.
            control.press_sequentially(value, delay=5, timeout=15000)
            control.press("Tab", timeout=5000)
    if "email" not in filled:
        raise ProviderFormError("form_matching_failed: contact email was not filled")
    return PreparedForm(form, filled)


def authorize_contact_consent(form: Any, *, allow_credit_check: bool = False) -> None:
    for i in range(form.locator("input[type=checkbox]").count()):
        control = form.locator("input[type=checkbox]").nth(i)
        text = control.evaluate(
            "e => ([...(e.labels||[])].map(l=>l.innerText).join(' ')+' '+(e.closest('[role=checkbox]')?.innerText||'')).trim()"
        ).lower()
        name = (control.get_attribute("name") or "").lower()
        if "newsletter" in name or any(
            w in text for w in ("nieuwsbrief", "marketing", "reclame")
        ):
            continue
        if (
            any(w in text for w in ("credit check", "krediet", "edr"))
            and not allow_credit_check
        ):
            raise ProviderFormError(
                "consent_required: provider requires an authorized credit check"
            )
        if name in {"consent", "avg"} or any(
            w in text
            for w in ("privacy", "persoonsgegevens", "verwerken", "contactaanvraag")
        ):
            set_boolean(control, True)


def validate_form(form: Any) -> None:
    missing = form.locator("input,textarea,select").evaluate_all(
        """els=>els.filter(e=>
        e.type!=='hidden' && !e.disabled && e.getClientRects().length &&
        (e.required || (e.getAttribute('aria-label')||'').includes('*') ||
         [...(e.labels||[])].some(l=>l.innerText.includes('*'))) &&
        (e.type==='checkbox' ? !e.checked : e.readOnly && e.getAttribute('role')==='combobox' ?
          !(e.closest('.q-select')?.querySelector('.q-field__native span')?.textContent||e.value).trim() : !e.value.trim())).map(e=>
        e.getAttribute('aria-label')||e.name||e.id||'unknown required field')"""
    )
    if missing:
        raise ProviderFormError("missing_profile_field: " + ", ".join(missing))
    invalid = form.locator("input,textarea,select").evaluate_all(
        "els=>els.some(e=>!e.disabled && !e.readOnly && e.getClientRects().length && !e.checkValidity())"
    )
    if invalid:
        raise ProviderFormError(
            "form_validation: provider field constraints were not satisfied"
        )
    captcha = form.locator("textarea[name*=captcha], input[name*=captcha]")
    if captcha.count() and not any(
        captcha.nth(i).input_value().strip() for i in range(captcha.count())
    ):
        raise ProviderFormError("security_check: CAPTCHA requires human completion")


def submit_control(form: Any) -> Any:
    controls = form.locator(
        "button:visible, input[type=submit]:visible, [role=button]:visible"
    )
    matches = []
    for i in range(controls.count()):
        control = controls.nth(i)
        text = (
            (control.inner_text() or control.get_attribute("value") or "")
            .strip()
            .lower()
        )
        if re.search(
            r"verzend|verstuur|verstu|aanvragen|aanvraag versturen|reageren|reageer|submit|send|bezichtig",
            text,
        ):
            matches.append(control)
    if len(matches) != 1:
        raise ProviderFormError(
            "submission_control_missing: expected one contact-form submit control"
        )
    return matches[0]


def confirmation_state(page: Any, form: Any) -> tuple[str, str]:
    """Read only visible response UI; never count a click or generic HTTP 200 as success."""
    text = page.locator("body").inner_text().lower()
    successes = (
        r"(?:bericht|reactie|aanvraag|formulier|inschrijving) (?:is )?(?:succesvol )?(?:verzonden|ontvangen|verstuurd)",
        r"bedankt voor (?:je|uw|jouw) (?:reactie|aanvraag|interesse|bericht|inschrijving)",
        r"we hebben (?:je|uw|jouw) (?:aanvraag|reactie|bericht) ontvangen",
        r"(?:message|request|application) (?:has been |was )?(?:successfully )?(?:sent|received|submitted)",
        r"thank you for (?:your )?(?:message|request|application|interest)",
    )
    for pattern in successes:
        match = re.search(pattern, text)
        if match:
            # Confirmation must be new (the caller checks state before sending).
            return "submitted", match.group(0)
    errors = form.locator(
        "[aria-invalid=true]:visible, .q-field--error:visible, .invalid-feedback:visible, [role=alert]:visible"
    )
    if errors.count():
        labels = errors.evaluate_all(
            "els=>els.map(e=>e.getAttribute('aria-label')||e.querySelector('input')?.getAttribute('aria-label')||'provider validation error')"
        )
        return "validation_failed", ", ".join(labels)[:250]
    return (
        "unknown",
        "No provider confirmation after submit; inspect evidence before retrying",
    )
