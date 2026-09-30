# Browser verification gap

The automated test suites cover session expiry and delayed in-flight responses, including refresh/session invalidation behavior. These scenarios have **not yet been fully verified end-to-end in a real browser**.

Before treating session handling as browser-verified, run a browser check that confirms:

- an expired access session redirects the user to authentication without restoring stale protected data;
- delayed responses that arrive after logout or session replacement cannot repopulate the previous authenticated view.

This gap is recorded on 2026-09-29 and is not evidence that the scenarios are failing; it distinguishes automated coverage from missing end-to-end browser evidence.

## T01 browser journey

The T01 browser journey reached the registration UI in the sandbox browser, but submission could not reach the API from the hosted frontend because the development backend was configured with its default `CORS_ORIGINS=http://localhost:5173`; the hosted frontend origin was therefore not returned in `access-control-allow-origin`. The API health endpoint was reachable independently. The journey is **not labeled passed**; backend/API behavior is covered by automated tests, while hosted browser verification requires running the frontend origin in the configured CORS allow-list.

## T01 correction status

The exact hosted frontend origin is now included in the backend test/development CORS allow-list. The prior hosted-browser failure was configuration-related, not a reason to use unrestricted CORS. A fresh end-to-end browser journey remains required after the corrected branch is running with this configuration.
