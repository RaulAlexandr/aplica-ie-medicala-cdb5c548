# Integrare T02–T05: verificarea esențială închisă

Actualizat: 2026-10-23T18:58+03:00.
Worktree: `/home/raul/Projects/worktrees/aplica-ie-integration`.
Branch: `integration/t02-t05`. HEAD publicat: `430ebeed97e5eddf30649ff4cda270251a6b375a`.
Niciun merge în main, deploy sau modificare în worktree-urile celorlalte taskuri.

## Ce s-a făcut

- Integrarea Lunei 7f1e2c4 păstrează T02 și T03/T04/T05, cu revizia Alembic merge_t02_t05.
- 0164663: aserțiunea PostgreSQL de catalog actualizată la head-ul combinat; CI declanșat și la push
  pe branch-ul dedicat integration/t02-t05, fără extinderea la toate branch-urile.
- 430ebee: programările din test_race_condition_start_resume_same_doctor mutate la 10:00/11:00 UTC,
  în aceeași zi locală București. Intervalul vechi 20:00 UTC + 60 minute traversa miezul nopții și
  era respins corect cu 422 de calendarul T02. Aserțiunile și gate-urile concurențiale sunt păstrate.
- Corecțiile nu schimbă logica produsului; sunt workflow și date/aserțiuni de test.

## Dovezi CI, pentru SHA final

[Run 39982401866](https://github.com/RaulAlexandr/aplica-ie-medicala-cdb5c548/actions/runs/39982401866): workflow completed/success, head_sha exact 430ebeed97e5eddf30649ff4cda270251a6b375a.
Toate cele trei joburi sunt completed/success, cu logurile citite:

| Job | Rezultat observat |
|---|---|
| backend | 78 passed, 9 deselected; Ruff, compileall și Alembic head trecute |
| postgres | upgrade pe bază goală la merge_t02_t05; 25 passed |
| frontend | 17 passed; build trecut |

Dovezi normalizate: `snapshots/integration-ci-430ebee.json`.
Suita PostgreSQL include upgrade din T01 populat și probele de concurență existente.
Testul suplimentar pregătit separat în worktree-ul QA nu este inclus și nu este declarat executat aici.

## Verificări locale și limite

Frontend rerulat local: 17 teste și build trecute. Ruff, compileall, head Alembic și diff check trecute.
Backend SQLite în sandbox a expirat la 35 secunde; nu este declarat trecut local. Dovada lui este
execuția CI finală. Pentru corecția intervalelor am rulat funcția reală de validare a zilei locale cu
lookup DB mock-uit: vechiul interval respins, ambele intervale noi acceptate; ulterior CI PostgreSQL a trecut.

Primul run 39981865475 pe 0164663: backend/frontend success, PostgreSQL 24 passed / 1 failed,
în setup-ul intervalului corectat. Nu se folosește acel run drept verdict final verde.

## Publicare și următorul pas

Push-urile au fost executate de utilizator pe gazdă: Git din sandbox nu rezolvă github.com.
Crearea PR prin conector a eșuat cu 403; gh are token invalid și CUA nu are browser. CI rulează
acum pe push, deci lipsa PR-ului nu mai blochează verificarea candidatului.

Verificarea esențială a integrării este închisă pe branch. PR către main și merge/deploy rămân operații
separate; nu au fost efectuate. Următoarea prioritate propusă este fluxul UI utilizabil T04/T05,
într-un task nou delimitat și pornit manual. T06 rămâne separat; nu se declară acceptanță browser
sau persistență T06 din rezultatul acestei runde. Avertismentul SQLAlchemy metadata rămâne backlog,
fără extinderea acestei runde de integrare.
