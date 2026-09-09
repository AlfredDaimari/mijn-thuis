# Provider adapter guide

## Purpose

Provider sites do not expose contact/application forms consistently. The
pipeline therefore has one generic Playwright form strategy and optional,
hostname-specific adapters. An adapter improves one provider without changing
how other providers work.

The generic strategy is the first attempt. It matches visible fields using
Dutch identifiers first (`voornaam`, `achternaam`, `telefoon`, `e-mail`,
`bericht`, `naam`) and then English identifiers. If no usable form is visible,
the optional GPT-5.6 Luna fallback receives the deterministic failure reason
and a redacted, numbered list of visible navigation controls. It can only
suggest a small set of clicks; Playwright re-validates each suggestion. It
never receives applicant values, cookies, passwords, or full page HTML, and it
must never click submit during a dry-run evaluation.

## Choosing the next provider

The resolver records every newly resolved provider URL in the SQLite
`provider_sources` table. Rank providers by `resolved_count` before adding an
adapter: frequent domains deserve custom work first.

Use the `PipelineDatabase.provider_source_tally()` method, rather than placing
SQL in a worker. The tally is incremented in the same transaction that saves
a new resolved URL, so retries do not inflate it.

## Adapter contract

Register an adapter in `query_service/provider_adapters.py` using the exact
lowercase hostname:

```python
from query_service.provider_adapters import ProviderAdapter, register

register(ProviderAdapter("www.example-provider.nl", "example-provider-contact"))
```

`adapter_for(url)` returns that adapter for matching URLs and returns
`GENERIC_ADAPTER` for every other host. Do not make a provider adapter the
default path.

Place provider-specific Playwright actions in a small module named after the
host, for example `query_service/adapters/example_provider.py`. It should
receive a Playwright page and applicant profile, then return structured
results: fields filled, screenshot paths, and a failure reason when relevant.
It must not open SQLite connections or execute SQL.

## Safe implementation procedure

1. Inspect representative resolved URLs for one host and confirm the tally
   justifies an adapter.
2. Implement only navigation needed to reveal the contact form: cookie banner,
   contact/interest button, login boundary, modal, iframe, or next page.
3. Prefer accessible locator APIs, labels, placeholders, and stable `name` or
   `autocomplete` attributes. Do not depend on CSS classes or positions.
4. Fill only configured applicant fields. Do not guess consent, marketing,
   password, identity-document, payment, or CAPTCHA inputs.
5. Run the adapter on several saved/resolved URLs in no-submit mode. Save a
   full viewport screenshot after scrolling to the form before filling and a
   second screenshot after filling. Record both against `resolved_listings.id`.
6. Log the adapter name, hostname, navigation step, fields filled, and error
   reason without logging personal values, cookies, or API keys.
7. Add pytest coverage using fake Playwright pages for successful form reveal,
   an absent form, and an unexpected provider layout.
8. Only after the no-submit results are reviewed may a separately authorised
   submission path click the provider submit control and capture post-submit
   evidence.

## Failure handling

If a form is absent, hidden, gated by login, inside an unsupported iframe, or
protected by CAPTCHA, return a clear failure reason. When the Luna fallback is
attempted, concatenate its planning or execution failure with that original
generic reason and store it in `applications.error`. Do not bypass access
controls or CAPTCHA. Leave the record observable for a future site-specific
adapter or manual workflow.

## Agent checklist

- Keep the generic adapter working for every unknown domain.
- Make changes only for the selected hostname.
- Keep browser/network work outside SQLite transactions.
- Store evidence through `PipelineDatabase`, not direct SQL in adapter code.
- Add tests and run `python3 -m pytest -m "not integration" -q` from `query/`.
- Do not submit forms during development or dry-run evaluation.
