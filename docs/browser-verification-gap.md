# Browser verification gap

The automated test suites cover session expiry and delayed in-flight responses, including refresh/session invalidation behavior. These scenarios have **not yet been fully verified end-to-end in a real browser**.

Before treating session handling as browser-verified, run a browser check that confirms:

- an expired access session redirects the user to authentication without restoring stale protected data;
- delayed responses that arrive after logout or session replacement cannot repopulate the previous authenticated view.

This gap is recorded on 2026-09-29 and is not evidence that the scenarios are failing; it distinguishes automated coverage from missing end-to-end browser evidence.
