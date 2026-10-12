# Application pipeline and dashboard repair — 12 October 2026

## Production diagnosis

The initial audit found 702 provider listings: 545 errors, 157 awaiting review, and zero submitted. The old worker had no application-submission path. Its success path saved a screenshot and stopped. Its tests verified that review-only behavior, so a green suite did not demonstrate successful applications.

Other verified implementation problems were hidden CAPTCHA textareas being filled as messages, the first search form being mistaken for a contact form, header registration links being treated as listing gates, limited cookie rejection labels, premature inspection before client rendering, and custom Dutch controls not being filled. Expired 404 pages are normal in housing; they must be classified as unavailable rather than sent to the model as navigation puzzles.

## Implemented repair

- Production Compose starts the application worker with `--submit`.
- Form discovery scores applicant forms across the page and frames. It excludes CAPTCHA/honeypot fields, handles Dutch labels, native selects, Quasar housing comboboxes and custom checkboxes, and verifies that filled values survive client initialization.
- The worker records a durable submission intent before the one final click. It marks `submitted` only after observing a new provider confirmation. An uncertain outcome stays an error with its intent intact and cannot be automatically claimed again.
- `before_submit` and `after_submit` screenshots are recorded for submitted attempts. Earlier failures capture `failure` evidence. No screenshot or click alone counts as an application.
- The obsolete review transition is removed. Startup migrates old `awaiting_review` rows to `pending` and preserves their evidence. The old on-disk CHECK constraint is retained for compatibility; no active code writes that state.
- Failed unsent rows may be requeued after a database backup. Attempt numbers and old screenshots are retained. Rows with a submission intent are excluded from requeue/claim to prevent duplicates.
- Browser errors no longer log Playwright call traces containing personal fill values. Legacy diagnostic output is redacted by the API.

## Dashboard

- Dedicated sign-in page using the existing `frontend_auth` values.
- Signed HttpOnly cookies, HTTPS `Secure` flag, SameSite protection, rate-limited sign-in, remember-me and logout. Nginx no longer uses a browser Basic Auth popup.
- Screenshot access uses an authenticated API subrequest.
- Combined Inbox: expandable emails with extracted source links, provider URLs and separate email/link failures.
- Listings, Applications and Evidence have server pagination, totals, search and status filters. Nested email links, evidence images and failure details are paginated too.
- Website totals and error-class counts share one table. Clicking a count opens matching full URLs and exact reasons.
- Evidence includes all attempts with screenshots, including failed/unsent listings, instead of only submitted listings.
- The UI shows a visible submission-profile blocker rather than hiding it in worker logs.

## Screenshot audit

Before repair deployment: 157 screenshot records, 157 real PNG files, 157 listings with evidence, zero missing references. All were `after_fill` records attached to old review-state listings. There were no historical before/after-submit screenshots.

VPS directory:

```bash
cd /srv/house-bot/screenshots
ls -lh
```

Container mount: `/app/screenshots`. The SQLite `screenshots` table links each file to listing, attempt and stage.

## Configuration blocker

The narrowly authorized name query found `Test Applicant` in both local and VPS applicant configuration. The other secrets were not displayed. A subsequent instruction prohibits inspecting the VPS configuration again; deployment preserves it, and normal application code loads it at runtime.

Production submissions pause while the configured name is a known test placeholder. Set real `applicant.first_name` and `applicant.last_name` in the ignored VPS `query/values.yaml`. The worker reloads its profile each cycle. The user's already confirmed context (`Huurwoning`, Netherlands registration true) is supplied through Compose environment defaults. Credentials remain in the existing ignored configuration and are excluded from deployment archives and Git.

Credit-check consent is limited to the explicitly authorized Schep provider. Marketing checkboxes are never selected. Other credit checks, missing required profile fields, login credentials and CAPTCHA/security challenges remain explicit blockers.

## Verification and operating limits

Browser tests exercise real local Chromium forms: custom controls, hidden CAPTCHA exclusion, reactive-value resets, required-field rejection, provider confirmation, before/after PNG capture and duplicate-send protection. API tests exercise cookie login/logout, tampering, cross-origin rejection and disjoint paginated results with full totals.

Live no-submit checks validate current public provider forms using dummy data. Schep and Nationaal Grondbezit reached validated form state. Vastgoed Nederland reached populated form state and correctly stopped at CAPTCHA. Woonzeker still clears values after blur on the sampled page despite bounded refill and keyboard-event retries; it remains an explicit provider validation blocker requiring further adapter investigation. A current VBT sample returned 404; Funda served a browser verification page.

Final local verification: 65 backend/browser tests passed, one live mailbox integration test deselected. The production React build passed. A Chromium UI check passed sign-in, combined Inbox expansion, page navigation, Listings and Evidence, with no JavaScript errors.

A live successful application must not be claimed until the real applicant profile is configured and a provider confirmation is recorded. Security checkpoints are not bypassed, expired listings cannot be revived, and unsupported income or identity fields are not guessed.

## Deployment and Git

Build images on the VPS from a source/template-only archive. Exclude `values.yaml`, `accounts.yaml`, SQLite data, node_modules, virtualenvs, generated assets and TypeScript build caches. Back up the live `mijn-thuis.sqlite3` before migration/requeue. Preserve screenshots and the existing VPS configuration. Inspect `docker compose ps`, authenticated pagination and screenshot access after deployment.

Changes are committed locally for the user to push; deployment does not push the repository to a Git remote.
