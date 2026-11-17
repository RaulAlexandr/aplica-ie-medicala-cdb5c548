# T05/T06 browser acceptance

Runs the integrated pilot browser scenarios against the already seeded fictional
candidate runtime. It uses a fresh headless Chromium profile per role. Credentials
are read from the local candidate credentials file and are never written to the
result summary.

```bash
cd /home/raul/Projects/worktrees/aplica-ie-pilot-candidate
export DENTACARE_PILOT_HOME="$HOME/.local/share/dentacare-pilot-candidate"
export DENTACARE_APP_SHA="$(git -C /home/raul/Projects/worktrees/aplica-ie-pilot-candidate rev-parse HEAD)"
export PILOT_WEB_URL=http://127.0.0.1:5199
export PILOT_API_URL=http://127.0.0.1:8029/api
export PILOT_CREDENTIALS="$DENTACARE_PILOT_HOME/credentials.json"
export PLAYWRIGHT_MODULE=/home/raul/Projects/dentacare-tools/browser-runner/node_modules/playwright/index.mjs
export CHROMIUM_EXECUTABLE=/home/raul/Projects/dentacare-tools/browsers/chromium-1234/chrome-linux64/chrome
"/home/raul/Projects/dentacare-tools/node/bin/node" scripts/pilot/browser-acceptance.mjs
```

Start the candidate runtime first with `scripts/pilot/start.sh --checkout
/home/raul/Projects/worktrees/aplica-ie-pilot-candidate --config
"$HOME/.config/dentacare-pilot-candidate/pilot.env"`. If it is already running,
leave it running. Evidence is written under
`$DENTACARE_PILOT_HOME/browser-evidence/`, outside the checkout.

The script covers doctor login, 32-tooth chart and persisted tooth status,
finding draft/edit/reload/finalize/correction history, patient switching,
T05 note finalization and automatic timeline projection, manager draft privacy
and correction access, and reception denial. API checks use separate in-memory
Playwright request contexts. The script does not inspect browser storage.

For T07, use the same authenticated doctor session after the timeline checks:
open patient A, choose a tooth and open “Add performed procedure for this
tooth”; cancel once and confirm the visit has no new procedure. Reopen, choose
an eligible visit for patient A, select a catalog procedure, and save once.
Confirm exactly one `pending` card appears, reload the visit, then click `Start`
on that card and verify its status changes to `running`. Reload again and
confirm the same procedure remains running. From the patient record, open the
same visit and save a second procedure through the visit form; verify exactly
one additional pending card. The T07 script automation is not included in this
file's existing scripted role/privacy scenarios; record these UI observations
separately with the browser run evidence.

The runner records the original locator/action error separately from its
finite API-response wait, plus visible alert text, console/page errors, and
API method/path/status. It never records request headers or credentials. The
2026-11-17 reproduction found two complete `.patient-odontogram` sections and
two `.og-new-finding` forms on one patient record. The first global save click
therefore failed Playwright strict mode before sending a POST. This is a
duplicate patient-record UI rendering issue; the runner now reports it as a
blocking check instead of choosing one duplicate instance.
