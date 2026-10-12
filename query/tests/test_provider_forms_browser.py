"""Chromium fixtures exercising provider forms rather than fake browser methods."""
import pytest
from playwright.sync_api import sync_playwright
from query_service.config import ApplicantProfile
from query_service.database import PipelineDatabase
from query_service.application_worker import _submit_prepared_form
from query_service.provider_forms import (
    ProviderFormError,
    prepare_form,
    authorize_contact_consent,
    validate_form,
    submit_control,
    confirmation_state,
)

pytestmark = pytest.mark.service


@pytest.fixture
def page():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        yield browser.new_page()
        browser.close()


@pytest.fixture
def profile():
    return ApplicantProfile(
        "Ada",
        "Lovelace",
        "+31612345678",
        "ada@example.invalid",
        "Graag een bezichtiging",
        "Graag een bezichtiging voor ons",
        "Huurwoning",
        True,
    )


FORM = """<form id="contact">
<label>Voornaam<input name="first_name" required></label>
<label>Achternaam<input name="last_name" required></label>
<label>E-mailadres<input name="email" type="email" required></label>
<label>Telefoonnummer<input name="phone" type="tel" required></label>
<textarea name="message" required></textarea><button type="button">Verzenden</button></form>"""


def test_contact_form_wins_over_search_and_skips_hidden_captcha(page, profile):
    page.set_content('<form><input name="query"><button>Zoeken</button></form>' + FORM)
    page.locator("#contact").evaluate(
        "f=>f.insertAdjacentHTML('beforeend','<textarea name=\"g-recaptcha-response\" style=\"display:none\"></textarea>')"
    )
    prepared = prepare_form(page, profile, profile.message_single_person)
    assert prepared.form.get_attribute("id") == "contact"
    assert page.locator("[name=first_name]").input_value() == "Ada"
    assert page.locator("[name=g-recaptcha-response]").input_value() == ""
    with pytest.raises(ProviderFormError, match="CAPTCHA"):
        validate_form(prepared.form)


def test_native_housing_select_and_custom_registered_checkbox(page, profile):
    page.set_content(
        FORM.replace(
            "</form>",
            """
    <select aria-label="Huidige woonsituatie *"><option value="">Kies</option><option value="rental">Huurwoning</option><option>Koopwoning</option></select>
    <div role="checkbox" aria-checked="false" onclick="this.setAttribute('aria-checked','true');this.querySelector('input').checked=true">
    <input name="tags.RegisteredInNetherlands" type="checkbox" style="display:none">Ben je ingeschreven in Nederland</div></form>""",
        )
    )
    prepared = prepare_form(page, profile, profile.message_single_person)
    assert "current_housing_situation" in prepared.fields
    assert page.locator("select").input_value() == "rental"
    assert page.locator("[role=checkbox]").get_attribute("aria-checked") == "true"
    validate_form(prepared.form)


def test_custom_privacy_consent_updates_visible_wrapper(page, profile):
    page.set_content(
        FORM.replace(
            "</form>",
            """<div role="checkbox" aria-checked="false"
    onclick="this.setAttribute('aria-checked','true');this.querySelector('input').checked=true">
    <input type="checkbox" name="consent" style="display:none">Ik ga akkoord met het verwerken van persoonsgegevens</div></form>""",
        )
    )
    prepared = prepare_form(page, profile, profile.message_single_person)
    authorize_contact_consent(prepared.form)
    assert page.locator("[name=consent]").is_checked()


def test_unknown_required_field_blocks_submission(page, profile):
    page.set_content(
        FORM.replace(
            "</form>",
            '<label>Bruto inkomen<input name="income" required></label></form>',
        )
    )
    prepared = prepare_form(page, profile, profile.message_single_person)
    with pytest.raises(ProviderFormError, match="missing_profile_field"):
        validate_form(prepared.form)


def test_only_provider_confirmation_is_success(page, profile):
    page.set_content(FORM)
    prepared = prepare_form(page, profile, profile.message_single_person)
    assert confirmation_state(page, prepared.form)[0] == "unknown"
    page.locator("button").evaluate(
        "b=>b.onclick=()=>document.body.insertAdjacentHTML('beforeend','<p>Bedankt voor uw aanvraag. Uw bericht is verzonden.</p>')"
    )
    validate_form(prepared.form)
    submit_control(prepared.form).click()
    assert confirmation_state(page, prepared.form)[0] == "submitted"


def test_late_form_initialization_does_not_erase_applicant_fields(page, profile):
    page.set_content(FORM)
    page.locator("[name=first_name]").evaluate(
        "e=>e.oninput=()=>{if(!window.erased){window.erased=true;setTimeout(()=>e.value='',80)}}"
    )
    prepare_form(page, profile, profile.message_single_person)
    assert page.locator("[name=first_name]").input_value() == profile.first_name


def test_keyboard_bound_field_model_receives_real_typing_events(page, profile):
    page.set_content(FORM)
    page.locator("[name=first_name]").evaluate(
        "e=>{e.onkeyup=()=>window.savedName=e.value;e.onblur=()=>e.value=window.savedName||''}"
    )
    prepare_form(page, profile, profile.message_single_person)
    assert page.locator("[name=first_name]").input_value() == profile.first_name


def test_provider_validation_is_not_a_submission(page, profile):
    page.set_content(FORM)
    prepared = prepare_form(page, profile, profile.message_single_person)
    page.locator("button").evaluate(
        "b=>b.onclick=()=>document.querySelector('[name=email]').setAttribute('aria-invalid','true')"
    )
    submit_control(prepared.form).click()
    assert confirmation_state(page, prepared.form)[0] == "validation_failed"


def test_submission_saves_real_before_and_confirmation_screenshots(
    page, profile, tmp_path
):
    db = PipelineDatabase(tmp_path / "pipeline.sqlite3")
    db.add_listing_if_new("fixture", "https://stekkies.test/1", "Fixture", "Fixture")
    source = db.claim_next_stekkies_link()
    db.add_resolved_listing_and_mark_source_read(source, "https://provider.example/1")
    work = db.claim_next_provider_listing()
    page.set_content(FORM)
    prepare_form(page, profile, profile.message_single_person)
    page.locator("button").evaluate(
        "b=>b.onclick=()=>document.body.insertAdjacentHTML('beforeend','<p>Bedankt voor uw aanvraag. Uw bericht is verzonden.</p>')"
    )
    _submit_prepared_form(
        page, work, db, tmp_path, "result.png", allow_credit_check=False
    )
    assert db.provider_listing_status(work.resolved_url) == "submitted"
    assert (tmp_path / "before-result.png").stat().st_size > 0
    assert (tmp_path / "result.png").stat().st_size > 0
    assert {s.stage for s in db.screenshots_for_resolved_url(work.resolved_url)} == {
        "before_submit",
        "after_submit",
    }
