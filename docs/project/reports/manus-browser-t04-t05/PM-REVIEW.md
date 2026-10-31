# Review PM al probei browser Manus

Verificat la 2026-10-31. Scope: raport și artefacte livrate, fără rerulare browser/API, fără modificări de cod sau GitHub.

## Surse și verdict

- [Raport original](../../evidence/manus-2026-10-31/MANUS-T04-T05-BROWSER-REPORT.md).
- [Manifest și dovezi copiate](../../evidence/manus-2026-10-31/README.md).
- SHA declarat și consemnat în git-state: `ede0446e03e46daf1dc128afcdf1b1d28acdb6a5`.
- Mediu declarat: cloud Manus, Python 3.12.3, Node 22.23.4, API real, SQLite demo, browser UTC, clinică Europe/Bucharest.

Verdict PM: flux principal demonstrat în browser pe SQLite, cu acoperire incompletă. Nu este PASS necondiționat pentru toate S01–S07 și nici acceptanță completă T04/T05 sau PostgreSQL.
Nu s-a reprodus un defect P0/P1 în parcursul raportat. Acest fapt nu certifică verificările de rol rămase neexecutate.

## Ce susțin dovezile

- Arhiva are 21 fișiere: 16 capturi, 4 fișiere text și un export cu extensia JSON. Hash-urile fișierelor din arhivă corespund copiei extrase de utilizator. Cele două rapoarte Markdown din folder sunt identice.
- Au fost inspectate vizual capturile pentru manoperă paused/completed, închidere blocată cu pending, vizită completed, ciornă redeschisă, revizie și pacientul Ana.
- Captura S04 arată manoperă completed cu 37 secunde, snapshot 125.50 RON și vizită încă in_progress. Captura paused arată 21 secunde și închidere dezactivată.
- Captura S05 arată blocarea închiderii cu pending, apoi vizită completed după anularea explicită a celei de-a doua manopere.
- Nota este finalized, iar revizia afișează textul vechi și textul nou. Exportul conține aceleași valori și legătura notei cu programarea corectă.
- Exportul cuprinde 2 pacienți, 2 programări, 3 manopere, o notă și o revizie. Verificarea locală a legăturilor pacient/programare/manoperă/notă și a textului reviziei a trecut.
- Git-state consemnează SHA-ul cerut și zero modificări. Acesta este jurnalul livrat de Manus, nu o inspecție live a checkout-ului său.

## Acoperire și rezerve

| Subiect | Ce este demonstrat / ce lipsește |
|---|---|
| S04 timer | Parcursul și durata nenulă sunt vizibile. Nu există export al segmentelor; excluderea pauzei și lipsa segmentelor active duplicate nu pot fi verificate independent din artefactele primite |
| S05 închidere | Blocarea în stările pending și paused este vizibilă în capturi. Pentru running nu există dovadă distinctă în livrare |
| S06 notă | Salvare/redeschidere/editare/finalizare/revizie susținute de capturi și export. Tipul folosit a fost `note`, nu `finding`, deși planul cerea și constatare |
| S07 schimbare context | PARTIAL. Manus a declarat doar schimbarea pacientului; vizită A → B neexecutată explicit. Captura Ana arată începutul dosarului, fără secțiunea de note în cadru |
| S08/S09 roluri | NOT RUN. Managerul a fost folosit și ca medic; nu au fost create conturi colegiale pentru refuzurile cerute |
| S10 FDI | Raportat ca validare în browser pentru 99, urmată de salvare validă cu 26. Nu este dovadă separată pentru răspuns HTTP 422 al API-ului |
| S11 timezone | Jurnalul arată browser UTC. Exportul programării este la 08:00 UTC, iar captura afișează 10:00 București, coerent cu 2026-11-01 |
| S12 timeline | Gol, conform limitei deja cunoscute; nu dovedește pierdere de date. Rămâne restanță de produs separată |

Fișierul `persisted-sanitized.json` nu este un document JSON valid: are linii etichetate, de forma `appointments [...]`. Secțiunile individuale se parsează și sunt coerente. Pentru următoarea livrare cere un obiect JSON valid sau redenumirea în `.txt`; nu modifica artefactul original pentru a-i schimba retroactiv forma.
Nu a fost efectuat un nou audit independent al secretelor din toate imaginile. Scanarea declarată de Manus nu este prezentată drept verificare PM separată.

## Următoarea probă propusă pentru Manus

Continuă pe același SHA și în mediul său existent dacă mai este disponibil. Fără refacerea parcursului complet sau corecții de cod.

1. S07: termină proba pacient A → B cu secțiunea de note în captură. Testează separat două vizite ale aceluiași pacient, cu appointment_id diferite: ciornă nesalvată în A, navigare la B, valori distincte în B, salvare și confirmarea ID-urilor în răspunsul/înregistrarea serverului. Nu salva automat ciorna A.
2. S08: din contul manager invită medic A și medic B în aceeași clinică. Ciornă nouă creată de A; B și managerul nu o pot citi/modifica/finaliza. Verifică aceeași clinic_id și sesiuni separate. Autorul o regăsește neschimbată după refuzuri.
3. S09: invită recepție sau asistent în aceeași clinică. Verifică UI și GET direct la dosarul/notele protejate, fără acces la conținut.
4. Completează dovada timerului într-o manoperă nouă: exportă segmentele și durata raportată, verifică suma segmentelor run închise, pauza exclusă și niciun run activ după Complete. Capturează blocarea Close visit și în starea running. Nu cere cronometrare manuală la secundă.
5. Creează distinct un `finding` cu FDI valid, salvează și redeschide după reload. Păstrează-l separat de nota deja demonstrată; verifică faptul că nu creează manoperă sau timer nou.
6. Livrează raport actualizat cu S07 PARTIAL până la acoperirea completă, matricea rolurilor și export JSON valid. Păstrează și raportul inițial ca istoric. Atașează Markdown și dovezi descărcabile.

Nu este necesară repararea timeline-ului pentru a termina această rundă QA. PostgreSQL E2E și modulele ulterioare rămân taskuri separate. Nicio autorizație de merge/deploy este introdusă prin acest review.
