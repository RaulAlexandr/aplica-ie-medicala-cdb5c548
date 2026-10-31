# MANUS T04/T05 Browser QA Report

**Stage verdict:** PASS for the executed browser journey S01–S07, with S07 coverage limited to the patient-switch branch; this is **not** a declaration that all T04/T05 acceptance is complete. S08–S09 were not run because no same-clinic invited colleague account was prepared. S12 observed the known empty timeline projection.

## Test identity and environment

- **Repository:** https://github.com/RaulAlexandr/aplica-ie-medicala-cdb5c548
- **Branch requested:** `feat/t04-t05-clinical-ui`
- **Exact tested SHA:** `ede0446e03e46daf1dc128afcdf1b1d28acdb6a5`
- **Checkout:** `/home/ubuntu/dentacare-qa`
- **Date:** 2026-10-31
- **Browser:** Manus Sandbox browser, Chromium-based; browser timezone verified as `UTC`
- **Clinic timezone:** `Europe/Bucharest`
- **Python:** 3.12.3
- **Node:** v22.23.4; npm 10.9.9
- **Database:** new SQLite database at `/home/ubuntu/dentacare-qa-db.sqlite3`, fictitious data only. This does not validate PostgreSQL migrations or PostgreSQL concurrency.
- **API:** `http://127.0.0.1:8010`; `GET /health` returned HTTP 200 and `{"status":"ok"}`.
- **Frontend:** `http://127.0.0.1:5178`; served HTTP 200.
- **Startup:** API via `uvicorn app.main:app --host 0.0.0.0 --port 8010`; frontend via `VITE_API_URL=http://127.0.0.1:8010/api npm run dev -- --host 0.0.0.0 --port 5178 --strictPort`.
- **Bootstrap:** `Base.metadata.create_all(checkfirst=True)` completed with 19 mapped tables. Alembic was not run against SQLite.
- **Credentials/data:** manager account and patient/contact values were fictitious. No password, token, cookie, or invite secret is included in the evidence.

## Test data and IDs

- Clinic `8eb58d6c-de88-4268-bb72-8defa9ad68fb`, `Cabinet Demo`, timezone `Europe/Bucharest`.
- Room `e0453b77-a776-4c37-aaf0-462220239f8d`, `Cabinet Demo 1`.
- Ion Demo patient `e6bf6ec2-08ab-471d-ab30-7df5788b8085`.
- Ana Demo patient `29128d85-933e-498f-8f4f-34d3f17c1b80`.
- Ion appointment `f5eeb8df-706d-4a46-a1e7-dc8d46128e56`, created at clinic-local 2026-11-01 10:00, final status `completed`.
- Ana appointment `da450587-f032-4d3a-b426-3dbef0b748fc`, created at clinic-local 2026-11-01 11:00, final status `in_progress`.
- Catalog procedure `UI-EXAM`, `Consultație demo`, 30 minutes, `125.50 RON`, catalog ID `d12aa5ef-d3a7-4fc1-bcfc-69aef404fdc7`.
- Ion completed procedure ID `489a1cb2-6772-466e-8502-9b49ca8ad508`, snapshot `125.50 RON`, FDI 26, surfaces `occlusal,mesial`, active time 37 seconds. Second Ion procedure ID `7cbe081f-84b7-4d7d-9352-25dd25198350` was explicitly cancelled after the close-block check. Ana procedure ID `8ad11375-cfbc-4880-9abe-4cefee13c2f5` is pending after the S10 recovery test.
- Finalized clinical note ID `622c7113-a363-4acf-bd31-5e4bc639ca90`; revision ID `7c7d09c7-0dfa-458a-98b0-b4926bf83a8a`.

## Scenario results

| ID | Status | Executed steps and observed result | Evidence |
|---|---|---|---|
| S01 | PASS | Registered and logged in through UI; clinic was `Europe/Bucharest`; created active room; created `UI-EXAM`; created Ion Demo and Ana Demo. Reloaded catalog and patient list; data reappeared. | `screenshots/S01-room-created.webp`, `S01-catalog-reload.webp`, `S01-patients.webp`; `logs/persisted-sanitized.json` |
| S02 | PASS | Created Ion appointment; selected 2026-11-01 in calendar; executed `scheduled → confirmed → arrived → in progress`; opened visit and reloaded direct visit route. Patient, appointment, room and clinic timezone were correct. | `screenshots/S02-visit-open.webp`; persisted appointment in `logs/persisted-sanitized.json` |
| S03 | PASS | Added UI-EXAM with FDI 26, `occlusal,mesial`, fictitious note. UI showed `pending`, catalog snapshot `125.50 RON`, and no active time before Start. | Visit captures from browser session; `logs/persisted-sanitized.json` |
| S04 | PASS | Started procedure, waited approximately 10 seconds, paused, reloaded, resumed, waited approximately 5 seconds, completed, then reloaded. UI showed completed, 37 seconds active time, no duplicate active segment, and appointment remained `in progress` before S05 close. | `screenshots/S04-completed-after-reload.webp`; persisted procedure row and appointment row |
| S05 | PASS | Added second pending procedure. Close attempt left visit open and showed `Resolve every pending, running or paused procedure before closing this visit.` Cancelled second procedure explicitly, then Close visit succeeded; reload showed visit/appointment `completed`. | `screenshots/S05-close-blocked-pending.webp`, `S05-visit-completed.webp`; persisted rows |
| S06 | PASS | From Ion visit opened patient record; created draft; reloaded; reopened draft; changed content; saved; finalized; verified finalized record no longer exposed edit/reopen controls and opened revisions. Revision showed old and new content. | `screenshots/S06-finalized-note.webp`, `S06-revision-visible.webp`; persisted note/revision rows |
| S07 | PASS (patient-switch branch) | Entered an unsaved Ion draft, navigated through Patients to Ana, and verified Ana’s record had no form/content from Ion. Ana’s separate in-progress visit was also created and opened during the run. The explicit unsaved-draft switch while changing appointment context A → B was not separately repeated after the patient-switch check. | `screenshots/S07-unsaved-Ion-draft.webp`, `S07-Ana-isolated.webp` |
| S08 | NOT RUN | No distinct same-clinic invited doctor/second-user session was prepared, so colleague draft ownership and direct-access rejection were not tested. | None |
| S09 | NOT RUN | No same-clinic reception/assistant account was prepared. | None |
| S10 | PASS | On Ana visit, FDI `99` was rejected by the browser’s controlled input validation (`Please match requested format.`); no procedure was created. Replaced it with valid FDI `26`; save then succeeded. | `screenshots/S10-invalid-FDI.webp`, `S10-valid-FDI-recovered.webp`; persisted Ana procedure |
| S11 | PASS | Browser console reported `timeZone: UTC`; visit/calendar UI displayed clinic context `Europe/Bucharest` and the clinic-local appointment time. | `console-timezone.txt`; S02/S10 visit captures |
| S12 | PASS with known limitation | Read patient timeline and clinical records/revisions after operations. Clinical note revision was readable. Persisted server timeline UI was empty; no timeline refresh POST was called. This matches the plan’s documented limitation and is not treated as note/procedure loss. | `S06-revision-visible.webp`; `logs/persisted-sanitized.json` |

## Defects and observations

### No P0/P1 product defect reproduced in S01–S07

The mandatory journey completed through the browser against the real API and new demo database. The evidence shows persistence across reload for the visit, performed procedures, finalized clinical note and revision.

### P2 observation: persisted timeline projection empty

- **Scenario:** S12
- **Steps:** Opened Ion patient record after appointment/procedure/note operations; read the server timeline section.
- **Expected:** Existing appointment/procedure/note events available in the persisted timeline.
- **Observed:** `No server timeline events match these filters.` The clinical note and revision were independently present and readable.
- **Impact:** Timeline view cannot be used as the sole reconstruction source in this SHA; underlying note/procedure/appointment records remain present.
- **Classification:** Known limitation explicitly listed in the QA plan, not treated as data loss.
- **No artificial refresh:** The manager-only timeline refresh endpoint was not called.

### Environment observation: first fictitious `.test` email rejected

The initial address using `example.test` was rejected by the browser/email validator as a reserved special-use domain. Replacing it with `example.com` allowed registration. This was test-data setup behavior, not a product defect; no production or real contact data was used.

## Environment vs application separation

The service startup and SQLite bootstrap succeeded in this run. The historical handoff’s loopback/SQLite blocker was not reproduced. The browser initially routed to an unavailable My Browser connection; switching to the Sandbox browser resolved that environment routing issue and the application then loaded normally. No source logic, manifest, lockfile, migration, or tracked project file was changed.

The SQLite demo cannot establish PostgreSQL migration correctness, PostgreSQL exclusion constraints, or production concurrency behavior. S08/S09 remain unexecuted because the required same-clinic role accounts were not prepared; they are not silently counted as passes.

## Git integrity

`git status --short` was empty at the end of the run. No commit, push, merge, deploy, or GitHub write operation was performed. The evidence and report were written outside `/home/ubuntu/dentacare-qa`.

## Next steps

1. Repeat S07’s explicit unsaved-draft test while changing both patient and appointment context A → B.
2. Prepare same-clinic invited doctor and reception/assistant accounts, then execute S08 and S09 with separate browser sessions.
3. Investigate/populate the persisted timeline projection in a separate product task; do not infer failure from the empty timeline alone because the underlying note/procedure rows were present.
4. Repeat the browser journey against PostgreSQL when PostgreSQL test infrastructure is available; this report is not PostgreSQL acceptance.
