# T02: operational calendar

Adds the daily/weekly clinic calendar, room/doctor/assistant/status/patient filters, appointment details/edit/status/reschedule actions, working hours, unavailability, clinic timezone conversion and explicit DST occurrence handling.

Verified on 2026-10-31: this PR is open and targets main; head adcdc820aef4f61f24879c7675c0658d35db4788. CI run 39798043917 and all backend/frontend/PostgreSQL jobs are completed/success.

T02 is also included in the separate integration/t02-t05 candidate 430ebeed97e5eddf30649ff4cda270251a6b375a and in clinical UI ede0446e03e46daf1dc128afcdf1b1d28acdb6a5. The later Manus SQLite browser journey concerns ede0446, not this PR head. Main has not received that candidate; no deployment or full-product acceptance is claimed.

The comprehensive independent Astra review remains pending. Any new findings must identify their exact code SHA and distinguish requirements, implementation and executed evidence.
