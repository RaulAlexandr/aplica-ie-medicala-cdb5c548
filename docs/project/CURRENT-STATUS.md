# DentaCare: starea verificată la 2026-10-31

Acesta este rezumatul curent. [Planul original](sources/PlanTotal.md) definește cerințele T00–T35, nu starea codului. [Matricea taskurilor](ROADMAP.md) păstrează această numerotare. Rapoartele mai vechi sunt dovezi datate și nu atribuie taskuri.

## GitHub verificat direct

Repository: https://github.com/RaulAlexandr/aplica-ie-medicala-cdb5c548 . Branch implicit: `main`.
Snapshot: [github-2026-10-31.json](snapshots/github-2026-10-31.json). Toate paginile necesare inventarului actual au fost citite: 9 branch-uri și 6 PR-uri, înaintea publicării documentației acestei runde.

| Livrare | SHA | Stare |
|---|---|---|
| main, T00/T01 | `2b9f543a0526381709182322ade0ab300f6fad4d` | PR #1 și #2 merged în main |
| T02 calendar | `adcdc820aef4f61f24879c7675c0658d35db4788` | PR #3 open, nedraft, bază main |
| T03 catalog | `415915faced44dc8ebcbbf782f46646a6316399f` | PR #4 open, draft, bază main |
| Backend T04/T05 | `5a09590ff0db5200140db454503280b51bfd3b4d` | PR #5 open, draft, bază T03 |
| Concurență T04 | `94108fc6a97f18a2d2779aef5f6d0e615abf8f06` | PR #6 merged în branch-ul backend; merge 5a09590, nu în main |
| Integrare T02–T05 | `430ebeed97e5eddf30649ff4cda270251a6b375a` | publicată, fără PR identificat în inventarul curent |
| UI clinic T04/T05 | `ede0446e03e46daf1dc128afcdf1b1d28acdb6a5` | publicat, fără PR identificat în inventarul curent |
| T06 odontogramă UI | `003b61760880c01f708ee130f9f20c0bc3911c16` | branch separat; demo fără API/persistență clinică |

Nu s-a integrat candidatul T02–T05/UI în main și nu există un deploy confirmat. `merge_commit_sha` dintr-un PR deschis poate indica merge-ul temporar GitHub; nu dovedește integrarea.

## CI și nivelul dovezilor

Stările workflow-urilor și ale tuturor celor trei joburi au fost reconfirmate la 2026-10-31. Numerele de mai jos provin din logurile citite în verificările anterioare, pe exact aceleași SHA-uri.

| Candidat | CI | Rezultat |
|---|---|---|
| UI ede0446 | [40602872429](https://github.com/RaulAlexandr/aplica-ie-medicala-cdb5c548/actions/runs/40602872429) | completed/success; backend 78 passed / 9 deselected; PostgreSQL 25 passed; frontend 27 passed în 6 fișiere și build |
| Integrare 430ebee | [39982401866](https://github.com/RaulAlexandr/aplica-ie-medicala-cdb5c548/actions/runs/39982401866) | completed/success; backend 78 / PostgreSQL 25 / frontend 17 și build |
| Backend 5a09590 | [39873123285](https://github.com/RaulAlexandr/aplica-ie-medicala-cdb5c548/actions/runs/39873123285) | completed/success; backend 72 / PostgreSQL 24 / frontend success |
| T02 adcdc820 | [39798043917](https://github.com/RaulAlexandr/aplica-ie-medicala-cdb5c548/actions/runs/39798043917) | toate joburile completed/success |
| T03 415915f | [37018096812](https://github.com/RaulAlexandr/aplica-ie-medicala-cdb5c548/actions/runs/37018096812) | toate joburile completed/success |
| main 2b9f543 | [36706669707](https://github.com/RaulAlexandr/aplica-ie-medicala-cdb5c548/actions/runs/36706669707) | toate joburile completed/success |

Integrarea are un singur head Alembic, `merge_t02_t05`, care unește ramurile T02 și T05. Migrarea pe bază goală, probele existente de upgrade din T01 populat și testele de concurență au rulat în CI. Testul suplimentar necomis din worktree-ul QA nu a fost inclus și nu este declarat trecut.

CI pe PostgreSQL nu dovedește că traseul browser Manus a folosit PostgreSQL. Acesta a folosit SQLite. CI vechi sau pe merge-ul temporar al unui PR nu se atribuie automat altui head.

## Ce este implementat în candidatul UI

- Fundație T00/T01: autentificare/sesiuni, clinici, roluri, pacienți/revizii, configurare, cabinete și invitații.
- Calendar T02 și catalog T03, reunite cu backendul clinic în 430ebee.
- T04: programarea este contextul vizitei; manopere din catalog, snapshot tarif, segmente run/pause, acțiuni explicite și închiderea separată a programării.
- T05: note/ciorne/finalizare/revizii, dosar și proiecție timeline. Politica cerută este ciornă accesibilă numai autorului, inclusiv față de manager/admin.
- UI nou: calendar → vizită → manoperă și dosar → notă, cu clientul autentificat existent. Aceasta este o etapă funcțională, nu întregul T04/T05 acceptat.

Pentru schema și directoarele efective vezi [harta codului](CODEBASE-MAP.md). Formulările de mai sus inventariază livrarea; auditul Astra trebuie să îi verifice corectitudinea, inclusiv permisiunile.

## Proba Manus din 2026-10-31

[Review PM](reports/manus-browser-t04-t05/PM-REVIEW.md): SHA ede0446, API real, SQLite demo, browser UTC, clinică Europe/Bucharest. Capturile și exportul susțin manoperă completed cu 37 secunde, închidere separată a vizitei, notă finalizată și revizie persistentă. Nu s-a reprodus P0/P1 în parcursul raportat.

Acoperire incompletă, fără PASS global:

- S07 este PARTIAL: schimbarea pacientului a fost parcursă; schimbarea explicită a vizitei A → B nu a fost repetată. Captura Ana nu include secțiunea de note.
- S08/S09 sunt NOT RUN: lipsesc colegii invitați în aceeași clinică și verificarea rolurilor prin UI/API.
- Lipsesc segmentele timerului pentru verificarea independentă a sumei run, excluderii pauzei și lipsei duplicatelor. Blocarea închiderii cu pending/paused este vizibilă; running nu are dovadă distinctă.
- A fost creată o înregistrare `note`, nu `finding`.
- Timeline-ul a rămas gol. Este restanța cunoscută de populare automată, fără dovadă că nota/manoperele au fost pierdute.
- Exportul original cu extensia JSON este text cu secțiuni etichetate; secțiunile se parsează separat, nu întregul document. Originalele se păstrează nemodificate.

## Lucru local de păstrat

Inventarul local este separat de GitHub și păstrează ref-uri de tracking posibil vechi. [Harta locală](WORKSPACES.md).

- Backend T04/T05 local este la 0c70bfc, deși branch-ul remote este la 5a09590. Nu face reset/pull ca simplă curățare.
- QA: `backend/tests/test_integration_t02_t05.py` este necomis la baza locală 5a09590; execuția sa pe candidatul final nu este dovedită.
- T06-browser: scriptul Playwright și DemoApp sunt modificate local, plus dist-t06 generat. Baza este 003b617; nu atribui modificările locale branch-ului publicat.
- PM: `docs/coordination/` rămâne necomis. Pointerele AGENTS locale sunt în majoritatea checkout-urilor neînregistrate.
- Automatizarea agenților a fost retrasă. Nu există lansatoare de repornit sau taskuri atribuite implicit după numele Luna/Vibe.

## Următoarea delegare

Raul cere acum [auditul complet Astra](ASTRA-AUDIT.md), asupra tuturor modulelor existente și a întregului plan T00–T35. Este review și verificare, fără fixuri de produs sau merge/deploy. [Backlogul](BACKLOG.md) separă lipsurile de produs, dovezile lipsă și blocajele de mediu.
