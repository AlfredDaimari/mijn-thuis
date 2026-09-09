"""No-submit evaluation of generic provider contact-form filling."""
import logging
import sqlite3
from pathlib import Path

import yaml
from playwright.sync_api import sync_playwright
from query_service.database import PipelineDatabase


def kind(meta: dict) -> str | None:
    text = " ".join(str(meta.get(key, "")) for key in ("name", "id", "placeholder", "aria", "label", "type")).lower()
    if "email" in text: return "email"
    if "telefoon" in text or "mobiel" in text or "phone" in text or "tel" in text: return "phone"
    if "voornaam" in text or "first" in text: return "first_name"
    if "achternaam" in text or "last" in text or "surname" in text: return "last_name"
    if "bericht" in text or "message" in text or "comment" in text or meta.get("tag") == "textarea": return "message"
    if "naam" in text or "name" in text: return "full_name"
    return None


def main() -> None:
    query_directory = Path(__file__).resolve().parent
    root = query_directory.parent
    values = yaml.safe_load((query_directory / "values.yaml").read_text())
    profile = values["applicant"]
    output = root / "tmp" / "form-dry-run"
    output.mkdir(parents=True, exist_ok=True)
    database = PipelineDatabase(query_directory / "data" / "pipeline.sqlite3")
    candidates = database.resolved_candidates_for_form_test()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        for candidate in candidates:
            number, url = candidate.id, candidate.resolved_url
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                form = next((page.locator("form").nth(i) for i in range(page.locator("form").count()) if page.locator("form").nth(i).is_visible() and page.locator("form").nth(i).locator("input, textarea").count()), None)
                if form is None: raise RuntimeError("no visible contact form")
                form.scroll_into_view_if_needed()
                before = f"{number}-before.png"; after = f"{number}-after.png"
                page.screenshot(path=str(output / before))
                fields = form.locator("input, textarea").evaluate_all("""els => els.map((e, i) => ({i, tag:e.tagName.toLowerCase(), name:e.name, id:e.id, placeholder:e.placeholder, aria:e.getAttribute('aria-label'), label:e.labels?.[0]?.innerText || '', type:e.type}))""")
                filled = []
                for field in fields:
                    field_kind = kind(field)
                    if not field_kind or field.get("type") in {"hidden", "checkbox", "radio", "submit"}: continue
                    value = f"{profile['first_name']} {profile['last_name']}" if field_kind == "full_name" else profile[field_kind]
                    form.locator("input, textarea").nth(field["i"]).fill(value)
                    filled.append(field_kind)
                page.screenshot(path=str(output / after))
                database.record_form_test_evidence(number, str(Path("tmp/form-dry-run") / before), str(Path("tmp/form-dry-run") / after))
                logging.info("Dry-run form filled | number=%d fields=%s url=%s", number, ",".join(filled) or "none", url)
            except Exception as error:
                logging.error("Dry-run form failed | number=%d error=%s url=%s", number, error, url)
            finally: page.close()
        browser.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
