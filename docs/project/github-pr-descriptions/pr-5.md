# T04/T05 backend: performed procedures and clinical records

Adds performed procedures with catalog snapshots, explicit execution transitions and timer segments; clinical drafts/finalization/revisions; record/timeline endpoints; and separate appointment closure rules. PR #6 was merged into this backend branch with controlled PostgreSQL concurrency acceptance.

Verified on 2026-10-31: this PR remains open/draft, base feat/task-t03-procedure-catalog, head 5a09590ff0db5200140db454503280b51bfd3b4d. CI run 39873123285 and all three jobs are completed/success. Previously read logs: backend 72 passed, PostgreSQL 24 passed, frontend success.

Later work is on separate published branches: integration/t02-t05 at 430ebeed97e5eddf30649ff4cda270251a6b375a, and feat/t04-t05-clinical-ui at ede0446e03e46daf1dc128afcdf1b1d28acdb6a5. The UI candidate CI 40602872429 passed backend 78 / PostgreSQL 25 / frontend 27 plus build.

Manus demonstrated the main browser/API journey on ede0446 using SQLite demo data. S07 is partial; S08/S09 were not run; timer segment and finding evidence remain incomplete. The empty timeline projection is a known limitation, not demonstrated loss of the underlying records. This does not certify PostgreSQL browser acceptance or complete T04/T05.

Remaining product areas for independent Astra review include timeline automation, manual-duration semantics, addendum linkage/history, permissions and evidence coverage. No merge into main or deployment is claimed.
