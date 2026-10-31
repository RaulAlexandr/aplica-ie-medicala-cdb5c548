# T04/T05 UI: verificare PM după publicare

Verificat la 2026-10-28. Această notă completează raportul agentului, fără să îl rescrie.

- Branch publicat de Raul: `feat/t04-t05-clinical-ui`.
- SHA verificat în workflow-ul GitHub: `ede0446e03e46daf1dc128afcdf1b1d28acdb6a5`.
- [CI 40602872429](https://github.com/RaulAlexandr/aplica-ie-medicala-cdb5c548/actions/runs/40602872429), event push, completed/success. Toate cele trei joburi sunt completed/success; logurile au fost citite.
- Backend: 78 passed, 9 deselected; Ruff trecut, Alembic `merge_t02_t05 (head)`.
- PostgreSQL: upgrade pe bază goală trecut și 25 passed.
- Frontend: 27 passed în 6 fișiere și build trecut.
- [Snapshot normalizat](../../snapshots/clinical-ui-ci-ede0446.json).

## Ce rămâne

Browserul cu API real și persistența după reload nu sunt încă verificate. Acestea sunt următorul pas al etapei UI, pe gazdă.
Comenzile corectate sunt în `/home/raul/Projects/worktrees/aplica-ie-t04-t05-ui/docs/T04-T05-UI-HANDOFF.md`.
Demo-ul SQLite folosește `Base.metadata.create_all(checkfirst=True)`, fără Alembic și fără ștergerea datelor existente.
Inițializarea efectivă a fost blocată în sandbox; compilarea DDL nu este dovada execuției.

Crearea PR draft către `integration/t02-t05` prin conector a fost încercată și refuzată cu `403 Resource not accessible by integration`.
La verificare nu exista PR deschis pentru acest head. Se poate crea manual din browser, alegând explicit baza `integration/t02-t05`, nu main.
Nu s-a făcut merge sau deploy. CI-ul verde nu închide singur acceptanța T04/T05.

## Reluare

Reia numai scenariul API/browser din handoff și corectează defectele concrete pe care le reproduce.
Folosește worktree-ul UI existent, pe branch-ul său; nu recrea implementarea, nu reface integrarea și nu redeschide acceptanța backend.
După probă, adaugă mediul, SHA-ul, rezultatele și dovada redeschiderii datelor în raportul taskului.
