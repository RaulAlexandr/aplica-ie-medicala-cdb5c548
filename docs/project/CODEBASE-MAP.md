# DentaCare: hartă pentru audit

Referință de cod: `ede0446e03e46daf1dc128afcdf1b1d28acdb6a5`, descendent al integrării 430ebee. T06 este pe branch separat 003b617. Verifică schema efectivă înainte de a formula un finding.

## Backend

- `backend/app/main.py`: routere înregistrate sub `/api`, CORS, health. Startup-ul nu creează schema automat.
- `backend/app/config.py`, `database.py`: configurare, modele, motor/sesiuni, tipuri compatibile SQLite/PostgreSQL pentru demo/test.
- `backend/app/auth/`: înregistrare clinică, login, access/refresh, revocare, autorizare.
- `backend/app/patients/`: date pacient, PATCH parțial, count/search/history și revizii.
- `backend/app/clinic/`, `rooms_router.py`, `staff/`: configurare, cabinete, invitații și personal.
- `backend/app/appointments/`: calendar, conflicte, program de lucru, indisponibilități, fus clinic/DST, istoric și statusuri.
- `backend/app/procedures/`: catalog T03, prețuri și validarea referinței la catalog.
- `backend/app/performed_procedures/router.py`: manopere T04, snapshot, doctor executant, segmente, tranziții, overrides și sumarul vizitei.
- `backend/app/clinical_notes/router.py`: note/ciorne, finalizare/addendum/revizii, record/timeline și refresh al proiecției.

`GET /appointments/{id}` există în candidatul actual. `GET /performed-procedures/visit/{appointment_id}/summary` este sumarul T04. Catalogul este `/procedures`, nu `/procedure-catalog`. Notele pacientului sunt `/clinical-notes/patients/{patient_id}`.
Folosește schemele Pydantic pentru payloadurile exacte; tooth_surface este string, iar prețurile API sunt șiruri zecimale. Verifică și comportamentul invalid, nu numai tiparea frontend.

## Frontend

- `frontend/src/App.tsx`: autentificare/layout și rutele pacienți, calendar, catalog, setup/staff; integrarea noului dosar.
- `frontend/src/Calendar.tsx`, `AvailabilitySettings.tsx`, `timezone.ts`: calendar și conversii în fusul clinicii. Details se remontează după selected.id.
- `frontend/src/api/client.ts`: Axios autentificat și mecanismul refresh/logout. Refolosit de clientul clinic.
- `frontend/src/clinical/`: VisitPage, ProcedureCard, ClinicalPatientRecord, ClinicalNotesPanel, ClinicalNoteEditor și api tipat.
- `frontend/src/clinical/*.test.*`: teste React/API mock; nu înlocuiesc traseul browser → API → DB.

T06: `frontend/src/odontogram/`, `t06-demo.html`, `vite.t06.config.ts`, `scripts/t06-browser-check.mjs`, `docs/T06-UI-HANDOFF.md`, pe branch-ul 003b617. Nu se găsește în candidatul UI clinic printr-o presupunere de integrare.

## Migrații și CI

- `0001_initial` → `0002_integrity_and_history` → `0003_clinic_setup`.
- Din 0003: T02 `0004_operational_calendar`; separat catalogul `c2195d701d56` → T04 → T05.
- `merge_t02_t05` unește capetele fără modificări de schemă. Candidatul integrat trebuie să aibă acest singur head.
- PostgreSQL: trigger pentru ends_at, btree_gist/exclusion constraints, JSONB și DDL specific. Nu aplica aceste migrații pe SQLite.
- `.github/workflows/ci.yml`: backend, frontend, postgres. Filtrul push include integration/t02-t05 și feat/t04-t05-clinical-ui; PR-urile declanșează workflow-ul.
- Testele PostgreSQL selectate: `test_postgres_integrity.py`, `test_postgres_procedures.py`, `test_postgres_concurrency.py`.
- `test_postgres_concurrency.py`: gate la lock, sesiuni distincte, observarea așteptării PostgreSQL, timeout și cleanup. Nu confunda asyncio.gather/barrier generic cu această acceptanță.

## Surse locale suplimentare pentru Astra

WORKSPACES și snapshotul local identifică QA-ul și modificările T06 necomise. Citește-le separat, fără a modifica branch-urile lor. Artefactele Manus originale sunt păstrate de utilizator; orice copie publicată este identificată prin hash și proveniență, nu declarată rerulare.
