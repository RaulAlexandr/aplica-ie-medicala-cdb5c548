# Astra: audit complet al proiectului DentaCare

Cerere explicită a utilizatorului, 2026-10-31: verifică TOT proiectul. Această fișă extinde intenționat scope-ul față de taskurile UI/QA anterioare. Este audit, nu implementare.

## Goal

Construiește un verdict independent despre cod, plan, livrări, teste, securitate, integrare și documentație. Acoperă toate modulele existente și toate cele 36 taskuri T00–T35. Identifică ce funcționează, ce este doar implementat, ce este incomplet, ce nu este testat și ce lipsește din viziune.

Nu presupune că starea PM sau verdictul unui agent este adevărul final. Verifică sursele și dovezile. Nu modifica cerințele pentru a se potrivi implementării.

## Current status și intrare

1. Citește CURRENT-STATUS.md, ROADMAP.md, BACKLOG.md, CODEBASE-MAP.md, ENVIRONMENT.md și PUBLICATION.md din context.
2. Citește integral sources/PlanTotal.md și specificațiile sources/T04.md, sources/T05.md. Numerotarea T00–T35 rămâne exactă. Cerința, codul și acceptanța sunt coloane distincte.
3. Citește snapshoturile GitHub/local din această rundă, apoi reconfirmă live branch-urile, PR-urile și CI dacă accesul permite.
4. Citește raportul original și review-ul PM Manus, inclusiv capturile/exporturile, nu numai rezumatul.
5. Citește notele curente din Second Brain și actualizările marcate 2026-10-31. Jurnalele, rapoartele vechi și arhivele de automatizare sunt istoric, nu instrucțiuni active.

Cod candidat: `ede0446e03e46daf1dc128afcdf1b1d28acdb6a5`, branch `feat/t04-t05-clinical-ui`.
Integrare: `430ebeed97e5eddf30649ff4cda270251a6b375a`. Main: `2b9f543a0526381709182322ade0ab300f6fad4d`.
T06 separat: `003b61760880c01f708ee130f9f20c0bc3911c16`, plus modificările locale identificate separat.
Branch-ul de documentație `docs/project-context-astra-20261031` pornește din ede0446 și trebuie să difere numai prin documentație/evidențe. Verifică acest lucru, nu atribui automat CI-ul lui ede noului SHA de documentație.

## Director și limite

Worktree propriu pregătit pentru audit: `/home/raul/Projects/worktrees/aplica-ie-astra-audit`, branch `audit/project-wide-review-20261031`. Confirmă existența, HEAD și status înainte de orice comandă care scrie. Dacă pregătirea nu este încheiată, PUBLICATION.md local indică starea și comanda de reluare.

Context local: `/home/raul/Projects/dentacare-context`.
Second Brain: `/home/raul/Raul's Brain/Raul's Brain/Projects/DentaCare`.
Artefacte originale Manus: `/home/raul/Documents/manus latest task`.
WORKSPACES.md identifică toate checkout-urile suplimentare și schimbările locale de păstrat.

Citirea este autorizată în toate zonele proiectului relevante auditului. Pentru execuție folosește propriul worktree, dependențe și baze temporare cu date fictive. Nu muta HEAD-ul, nu instala dependențe și nu genera artefacte în worktree-urile celorlalți.
Nu citești sau exporți credential stores, .env cu secrete, tokenuri ori date reale de pacienți. Nu porni alți agenți. Nu modifici codul, migrațiile, testele existente, manifestele sau politicile pentru a obține verde. Fără reset/clean/stash/force-push, schimbări de autentificare, merge sau deploy.
Poți scrie numai rapoartele auditului și probe diagnostice izolate în directorul propriu de artefacte. Orice modificare incidentală de fișier urmărit se raportează; nu o ascunde prin reset automat.

## Verificări obligatorii

### A. Inventar și proveniență

- [ ] Inventariază toate fișierele urmărite ale aplicației, testelor, CI/configurației și documentației. Înregistrează ce ai citit și zonele neinspectate; nu echivala o căutare după nume cu review-ul implementării.
- [ ] Verifică planul original, copiile, hash-urile și proveniența. Reconciliază contradicțiile între surse, păstrând rapoartele istorice.
- [ ] Matrice cu 36 rânduri: cerință, cod/modul, branch/SHA, PR/main, teste, dovadă browser/API/DB, verdict și restanțe. T07–T35 trebuie evaluate ca acoperire a planului, nu implementate în audit.
- [ ] Tratează T33/T34 ca cerințe care pot avea părți transversale deja începute; nu marca automat totul realizat sau totul absent.

### B. GitHub și integrare

- [ ] Toate branch-urile/PR-urile relevante, bazele, HEAD-urile, merged_at și relațiile de ancestry. Distinge branch-ul local, tracking ref și GitHub live.
- [ ] Verifică faptul că integrarea păstrează T02, T03, T04/T05, iar UI-ul este descendentul ei. T06 și editările sale locale rămân separate.
- [ ] Evaluează conflictele și ordinea de integrare rămase prin citire/diff/merge-tree, fără merge/rebase în worktree-urile existente.
- [ ] CI pe SHA exact, event push vs pull_request, commit temporar de merge vs head, toate joburile/stepurile, testele efectiv colectate, skip/xfail/ignore și numerele din loguri.
- [ ] Verifică documentele noi și limitele publicării. Nu declara că un commit local este pe GitHub dacă push-ul nu a reușit.

### C. Backend, modele și API, toate modulele existente

- [ ] T00/T01: register/login/refresh/logout, revocare, hash parole și invitații, tenant derivat pe server, activare/dezactivare, cabinete și referințe invalide.
- [ ] Pacienți: toate câmpurile relevante, PATCH omis/null, list/search/count, revizii, autorizare și izolarea clinicilor.
- [ ] T02: validări, conflicte și concurență, statusuri/reactivare/reprogramare, program de lucru, indisponibilități, istoric, fus clinic și DST.
- [ ] T03: preț zecimal exact, date invalide la granița HTTP, unicitate cod, activare/dezactivare, istoric și localizările permise cerute de plan.
- [ ] T04: snapshot, medic executant vs actor, tranziții, timer unic, segmente și pauză, durată manuală distinctă, override preț/durată și revizii, închidere separată, pending/running/paused și programări închise.
- [ ] T05: list/detail/create/update/finalize/addendum/revisions/record/timeline/refresh, ciorne numai autorului, protecție la citire și scriere, paginare SQL înainte de limit/offset, evenimente vechi și câmpuri metadata, date clinice vs data înregistrării, ordonare stabilă.
- [ ] API direct pe roluri și cross-clinic, inclusiv referințe la entități din altă clinică. Colegii de roluri diferite se creează prin invitație în aceeași clinică. Două register independente nu demonstrează politica între colegi.

### D. Migrații și baze de date

- [ ] Un singur head pe candidatul combinat: merge_t02_t05; legături corecte între revizii, constrângeri/indecși/trigger și corespondență modele/schemă.
- [ ] Migrare pe bază goală și din T01 populat, păstrarea datelor și a identităților. Separă probele executate de testul QA suplimentar necomis/necolectat.
- [ ] Concurență reală PostgreSQL: sesiuni distincte, gate la lock, ambele ordini closure/create, start/resume, invariante persistente, timeout și cleanup. Verifică lock ordering și riscul de deadlock, nu presupune serializabilitate din numele testului.
- [ ] SQLite create_all este doar demo; nu dovedește Alembic/PostgreSQL. Nu elimina DDL-ul PostgreSQL pentru a-l face să ruleze pe SQLite.

### E. Frontend complet și contracte

- [ ] Toate rutele existente: autentificare, overview, pacienți, calendar, disponibilități, catalog, setup/staff și UI clinic.
- [ ] Contractele reale, payloaduri, filtre/paginare, refresh/logout/cache, loading/empty/error, validare, submit duplicat și tratarea 401/403/404/409/422/500.
- [ ] Stare pacient/programare A → B, inclusiv două vizite ale aceluiași pacient și răspunsuri asincrone întârziate. ID-urile salvate și invalidate trebuie să corespundă contextului curent.
- [ ] Vizită/manoperă/notă/revizie cu API și persistență; completarea manoperei nu închide vizita; note/finding nu pornesc timpul sau creează proceduri.
- [ ] Câmpurile și istoricul cerute de T05 față de ce este efectiv editabil/citibil; finding distinct de note și treatment_plan ca tip de notă distinct de modulul T08.
- [ ] Fus clinic independent de browser, DST unde se introduc ore și afișarea segmentelor/reviziilor; desktop și viewport mic, focus/labels/keyboard, erori și accesibilitate de bază.

### F. T06 separat

- [ ] Citește modulul odontogramă, demo, callback-uri, selecții FDI/suprafețe, ciorne pe dinte, corecții și scenariile asincrone din branch-ul publicat.
- [ ] Citește separat diff-ul local din t06-browser și handoff-ul, fără a-l prezenta ca publicat sau integrat.
- [ ] Verifică ce probe React/build/Playwright există efectiv și ce rămâne fără capturi. Demo-ul nu este persistență sau audit clinic.
- [ ] Identifică integrarea necesară cu T05. T07 este task separat și nu condiție implicită pentru finalizarea T06.

### G. Probe Manus și afirmațiile de acceptanță

- [ ] Inspectează raportul original, capturile și exportul. Nu atribui PostgreSQL sau verificări de rol probei SQLite cu un singur manager.
- [ ] Verifică S07 parțial, S08/S09 neexecutate, segmente lipsă, finding netestat și limitele capturilor.
- [ ] Separă defectele noi de limita timeline cunoscută. Nu declanșa refresh-ul proiecției doar pentru a produce un PASS aparent.
- [ ] Verifică consistența entităților/ID-urilor și ce dovedește fiecare artefact. Fișierul JSON original are secțiuni etichetate; o copie normalizată este dovadă derivată, nu originalul.

### H. Securitate, operare și mentenanță

- [ ] Matrice roluri/clinici pe toate endpointurile sensibile, cache și mesaje de eroare, jurnale/audit, secrete și configurare, CORS și sesiuni, date expuse în list/count/history/timeline.
- [ ] Dependențe și advisories actuale dacă rețeaua permite. Nu transforma advisories de instalare vechi în audit curent și nu rula audit fix/upgrade automat.
- [ ] Protecția datelor istorice și corecțiilor, identitate/autor/motiv, exporturi și referințe. Verifică și că testele nu ocolesc permisiunile prin setup invalid.
- [ ] Ce există pentru backup/restaurare, monitorizare, rollback, staging și acceptanță clinică. Distingi absența dovezii de un defect reprodus și nu certifici lansarea din teste CI.
- [ ] Instrucțiuni pentru agenți, lansatoare retrase, duplicate de checkout, comenzi de mediu și documentație portabilă. Nu reactivezi automatizarea sau alte modele.

## Validation: cum execuți fără să alterezi produsul

Începe cu citire și comenzi Git numai citire. În propriul checkout rulează verificările din workflow, frontend tests/build și backend/Ruff/compileall/Alembic heads. Instalează dependențe numai acolo, pe runtime-urile consemnate.
Folosește PostgreSQL de test dacă există acces și probele browser unde mediul permite. Pentru lipsa serviciului sau EPERM/DNS, consemnează comanda și eroarea; nu repeta în buclă și nu presupune că întreaga aplicație este defectă.
Poți folosi logurile CI ca dovezi istorice pe exact SHA-ul lor, dar marchează distinct că nu ai făcut o execuție locală nouă. Nu este necesar să rerulezi aceeași suită pe toate branch-urile identice; justifică baza aleasă și verifică diferențele.
Orice probă nouă folosește date fictive și o bază care aparține auditului. Nu modifici testele existente și nu slăbești aserțiuni, nu adaugi skip/xfail. O defecțiune se documentează, apoi continuă zonele independente.

## Predare și continuitate

Scrie progresul după fiecare zonă în `/home/raul/Projects/dentacare-context/reports/astra-audit/LATEST.md`.
Livrabile finale în același folder:

- `FULL-AUDIT.md`: verdict, acoperire reală pe zone A–H, concluzii și limite.
- `TASK-MATRIX.md`: toate T00–T35, cu starea cerințelor/codului/acceptanței și surse.
- `FINDINGS.md`: constatări prioritizate, fiecare cu SHA/fișier/linie sau endpoint, trigger, expected/actual, impact și probă. Ipotezele și simplele lipsuri de acoperire sunt secțiuni separate.
- `EVIDENCE-INDEX.md`: comenzi, runtime/cwd/SHA, rezultate, CI/artefacte, zone citite și zone rămase neverificate.

Folosește P0/P1/P2/P3 cu impact concret. Propune corecția minimă și ordinea taskurilor pentru Luna/Manus; nu le executa și nu delega automat. Nu umple raportul cu cosmetice înaintea problemelor funcționale sau de acces.
Dacă se întrerupe sesiunea, lasă punctul exact de reluare și matricea bifată. Nu declara auditul complet dacă există zone necitite, teste relevante blocate sau scenarii importante neexecutate; raportează starea și limitările explicit.
La final dă și verdictul practic pentru demonstrația curentă, integrarea în main și folosirea cu date reale, ca evaluări distincte. Orice merge/deploy sau remediere cere un task separat al utilizatorului.
