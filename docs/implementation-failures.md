# Implementation failures and unresolved work

Historical record of the 4 October implementation. For the subsequent repair and current limitations, see [12 October repair handoff](2026-10-12-repair-handoff.md).

This page records the concrete failures found during the observability and provider-application work. It is intentionally candid so the next implementation can start from verified facts rather than assumptions.

## Executive summary

The system is an observability/review pipeline, not an application-submission system. The worker can open provider pages, fill some recognizable fields, and capture screenshots, but its normal production path never submits a provider application. Every successful worker path calls `mark_provider_listing_processed`, which writes `awaiting_review`; there is no production call that transitions a listing to `submitted`.

As a result, the headline requirement—actually applying for homes—has not been achieved. No confirmed provider application was created by the worker.

## Verified failures

### 1. The worker was designed not to submit

`query/query_service/application_worker.py` explicitly documents a no-submit worker. `process_once()` fills/captures evidence and calls `mark_provider_listing_processed()`. `query/query_service/database.py` then sets the provider listing to `awaiting_review`.

The only submit click in the code is the separate provider-login helper. There is no generic application submit path, no post-submit confirmation check, and no durable `submitted` evidence flow in the worker.

This was the primary architectural mismatch: the requested outcome was “apply”, while the implemented outcome was “prepare for human review”.

### 2. The real Schep submission attempt did not submit

The supplied Schep URL was tested against the live page. The matcher filled four text fields successfully. A one-off production attempt then:

- filled the configured applicant fields;
- clicked the visible custom consent wrapper;
- clicked `Verzenden` once;
- stayed on the same page;
- produced no provider application POST;
- showed client-side validation errors.

The provider form requires a custom `Huidige woonsituatie` control. The generic matcher only handles `input` and `textarea` fields, so it never selected that dropdown. The form also has custom controls for Netherlands registration and consent. The hidden native checkbox cannot be clicked directly; the visible Quasar wrapper must be used.

The provider page also creates an account and performs a credit check. Those are consequential actions and must be explicitly configured and evidenced before enabling an automated submission path.

### 3. Generic field matching was too narrow

The initial matcher recognized common Dutch/English text fields (`voornaam`, `achternaam`, `e-mail`, `telefoon`, `bericht`) but did not handle:

- Quasar/Vue custom inputs;
- comboboxes and dropdowns;
- custom checkboxes;
- provider-specific required fields;
- validation/blur behavior in reactive forms;
- post-fill verification that the provider’s application model actually received the value.

DOM values appearing populated was incorrectly treated as proof that the provider form state was valid.

### 4. The navigation planner had an index-contract bug

The planner originally received indexes derived from a filtered visible candidate list while Playwright indexed an unfiltered DOM locator. Hidden controls could therefore shift the selected control. This was fixed by adding an explicit visible `candidate_index` and using the same `:visible` locator for execution, but the bug existed in production tests before the fix.

### 5. Dutch navigation was initially under-specified

The first prompt treated navigation as a small English-oriented click decision. It did not adequately model Dutch state transitions such as:

- `Ik heb interesse`;
- `Interesse tonen`;
- `Reageer op deze woning`;
- `Bezichtiging aanvragen`;
- `Huidige woonsituatie`;
- `Inschrijven` versus `Inloggen`;
- validation and confirmation states.

The prompt has since been expanded to be Dutch-first and state-aware. It now receives only non-identifying context such as `Huurwoning` and `registered_in_netherlands: true`; it must not receive applicant contact details.

### 6. Cookie overlays blocked provider navigation

Production logs showed Playwright click attempts being intercepted by cookie overlays. This was a real failure for some providers, although it was not the reason that zero applications were submitted—the no-submit architecture was.

A deterministic handler was added to choose only explicit reject/essential-cookie controls. It must never accept all cookies as an application prerequisite.

### 7. Provider layouts were incorrectly treated as generic

Live sample logs showed different classes of provider failure:

- no visible contact/application form;
- account creation required;
- login required;
- cookie overlay interception;
- model selecting a control that was not application navigation;
- custom form controls and validation;
- security/CAPTCHA/provider protection.

These cannot all be solved by one prompt. A generic fallback is useful for navigation, but high-volume providers need tested provider adapters and provider-specific post-submit confirmation logic.

### 8. Retry/requeue handling was initially wrong

`claim_next_provider_listing()` claims only `status = 'pending'`. Terminal `error` listings were therefore never retried. A manual database requeue was required.

The first requeue command targeted the wrong filename (`pipeline.sqlite3`) on the VPS and changed no production rows. The actual database was `mijn-thuis.sqlite3`. After correcting this, 52 failed provider listings were backed up and requeued to `pending`, with workers restarted.

Some rows reached `awaiting_review`; others failed again for genuine provider blockers. Requeueing the same account/security failures indefinitely is not a fix.

### 9. Email polling had no date window

The original Yahoo IMAP query searched every matching Stekkies message on every poll. It did not use `SINCE`, so the initial load and steady-state poll were indistinguishable.

The poller now searches ten days on its first service poll and one day thereafter. The SQLite signature still deduplicates messages. The current database contained 519 stored messages with Date headers spanning 8 September through 4 October; dashboard `accessed_at` is insertion time, not the email’s original received date, which made backfilled messages look like today’s messages.

The ten-day/one-day switch is currently process-lifetime state. A future implementation should persist the initial-sync marker if restarts must never repeat the ten-day query.

### 10. Dashboard summaries were repeatedly wrong or misleading

Several frontend observability issues were corrected incrementally:

- Stekkies links showed a meaningless website summary even though they came from one source site.
- Website grouping initially used full URLs, then was corrected to hostname only.
- Error classes were initially absent or disconnected from website counts.
- Error summaries were shown together instead of per source tab.
- Provider URLs and Stekkies URLs were confused in listings/evidence views.

The current direction is to keep source-tab errors separate and join provider website totals with inline error-class counts in the same table.

## What was actually verified

- The two supplied Schep URLs expose one visible form and the generic matcher fills first name, last name, email, and phone.
- The Schep form still rejects submission because its custom housing-situation field is empty.
- The one real submit attempt produced no provider submission POST.
- The worker’s applicant profile is configured in production, and OpenRouter is configured, but this does not enable submission.
- The local non-integration suite passes (`51 passed` at the time this page was written).
- The VPS stack was repeatedly rebuilt from secret-free archives; secrets and SQLite data were excluded from uploaded bundles.

## Required redesign before claiming “applications work”

1. Add an explicit submission mode separate from review mode. Default must remain review-only until configured.
2. Extend the applicant configuration with provider-field data, at minimum:
   - `current_housing_situation` (for example `Huurwoning`);
   - `registered_in_netherlands` (`true`/`false`);
   - an explicit consent/submission authorization flag.
3. Implement provider-control adapters for custom comboboxes, Quasar checkboxes, and reactive validation—not only native inputs.
4. Add a submit routine that:
   - verifies every required field before clicking;
   - captures `before_submit` evidence;
   - clicks only the provider’s identified final control;
   - verifies a provider confirmation or known success response;
   - captures `after_submit` evidence;
   - writes `submitted` only after confirmation.
5. Keep account creation, credit-check consent, CAPTCHA, payment, and security checkpoints behind explicit provider/user configuration. Do not infer these from a generic LLM response.
6. Test several real providers in three separate modes: navigation-only, fill-and-validate, and authorized submit. Do not call the first two “applied”.
7. Add persistent poller sync state and expose original email Date headers separately from database insertion timestamps.

## Current truth

The dashboard and pipeline observability layer are usable for showing queued, failed, awaiting-review, and evidence states. The application layer is not complete. Until the submission path and provider-specific required fields are implemented and confirmed, the system should not report a listing as applied.
