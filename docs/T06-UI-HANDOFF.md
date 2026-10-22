# T06 — Odontogramă UI, etapa 1

## Implementat

- Diagramă 2D pentru pozițiile FDI ale dentiției permanente adulte: 11–18, 21–28, 31–38, 41–48.
- Arcadele sunt aranjate din perspectiva pacientului. În stânga e dreapta pacientului, iar în dreapta e stânga pacientului. Numărul FDI și starea apar pe fiecare dinte.
- Toți dinții rămân selectabili, inclusiv dinții marcați lipsă. Se poate selecta cu click, Tab/Enter sau cu săgețile între pozițiile vecine și arcade.
- Panoul individual separă starea dintelui, constatările și evenimentele din istoricul demonstrativ. Formularul permite mai multe constatări pe același dinte și pe aceleași suprafețe.
- O corecție demo schimbă valoarea curentă și adaugă în istoric valoarea anterioară și cea înlocuitoare.
- Dentiția temporară nu este inclusă. FDI identifică și dentiția temporară prin cadranele 5–8; implementarea acestei etape afișează doar cele 32 de poziții permanente adulte.

## Pornirea demonstrației

Din directorul `frontend`, build-ul demo este separat de build-ul principal și scrie în `frontend/dist-t06`:

```sh
PATH="/home/raul/Projects/dentacare-tools/node/bin:$PATH" ./node_modules/.bin/vite build --config vite.t06.config.ts
```

Pentru servirea artefactului build-uit pe loopback și portul dedicat 5176:

```sh
PATH="/home/raul/Projects/dentacare-tools/node/bin:$PATH" ./node_modules/.bin/vite preview --config vite.t06.config.ts --host 127.0.0.1 --port 5176 --strictPort
```

Deschide `http://127.0.0.1:5176/t06-demo.html`. Pagina afișează mesajul „Demonstrație — date locale, fără salvare pe server.” Build-ul standard continuă să scrie în `frontend/dist`; build-ul T06 verificat nu a schimbat conținutul acelui director.

Pentru dezvoltare fără build, Vite poate servi pagina la URL-ul dedicat prin configurarea T06:

```sh
PATH="/home/raul/Projects/dentacare-tools/node/bin:$PATH" ./node_modules/.bin/vite --config vite.t06.config.ts --host 127.0.0.1 --port 5176 --strictPort
```

Scenariul demonstrativ pornește cu 1.6 selectat și o constatare existentă. Selectează 1.7, adaugă o constatare de carie, apoi revino la 1.6. Constatarea inițială rămâne acolo; reselectarea lui 1.7 arată constatarea adăugată.

## Componentă și contract propus

`Odontogram` este componentă de prezentare controlată. Primește `teeth`, `findings`, `history`, `selectedToothId` și callback-urile `onToothSelect`, `onFindingCreate`, `onFindingCorrect`, `onToothStatusChange`. Tipurile sunt exportate din `src/odontogram/index.ts`.

Acesta este un **contract propus** pentru integrare; nu reprezintă un API backend existent:

- `OdontogramTooth`: `id: ToothId`, `status: ToothStatus` (`present`, `missing`, `unevaluated`).
- `ClinicalFinding`: `id` stabil, `toothId`, `kind`, `description`, `surfaces`, `observedAt`, `author`.
- `ToothHistoryEvent`: `id` stabil, `toothId`, `occurredAt`, `author`, `kind`, `summary`, cu `previousValue`/`currentValue` pentru corecții și schimbări de stare.
- `OdontogramProps`: cele trei colecții de mai sus, `selectedToothId` și callback-uri tipizate. Callback-urile de scriere primesc ID-ul dintelui/constatării și datele schimbate; pot întoarce `Promise<void>`.

`ToothId` folosește codul FDI cu două cifre, pentru cadranele permanente adulte 1–4 și pozițiile 1–8. `ToothSurface` folosește identificatorii `mesial`, `distal`, `buccal`, `lingual`, `palatal`, `occlusal`, `incisal`, `vestibular`, `labial`, `approximal`, `interproximal`. Diagrama emite `vestibular` pentru suprafața spre obraz/buze și etichetează suprafața centrală `incisal` la anteriori, `occlusal` la posteriori.

Starea descrie dintele; constatarea descrie o observație clinică; evenimentul istoric descrie o schimbare. Acestea nu sunt tratamente planificate sau manopere efectuate.

Modulul cere părintelui să furnizeze lista de dinți și stările lor curente, constatările cu ID-uri și autor/data, evenimentele de istoric cu ID-uri și selecția curentă. Nu presupune endpointuri sau modele T05. Pentru integrare, părintele va mapa dosarul și istoricul T05 la aceste tipuri după stabilirea contractului real.

În demonstrație callback-ul actualizează imediat stare React locală; aceasta nu este confirmare de persistență. În integrarea viitoare, callback-ul asincron trebuie să se rezolve după confirmarea operației T05 și după ce părintele actualizează props-urile din răspunsul confirmat. La respingerea unui callback asincron, componenta păstrează ciorna și afișează eroarea. Corecția cere o valoare editată explicit și trimite în callback identificatorul constatării existente.

Datele și actualizările demonstrației sunt separate în `demoData.ts` și `useOdontogramDemo.ts`. Componenta reutilizabilă nu importă niciun client HTTP și nu importă datele demo.

## FDI și suprafețe

ID-urile suprafețelor respectă vocabularul validat în T04 și în routerul de constatări T05 aflat în lucru: `mesial`, `distal`, `buccal`, `lingual`, `palatal`, `occlusal`, `incisal`, `vestibular`, `labial`, `approximal`, `interproximal`.

Selectorul folosește `mesial` și `distal` pentru contactele interdentare, `vestibular` pentru fața spre obraz/buze, `palatal` la dinții superiori și `lingual` la cei inferiori. Suprafața centrală este `incisal` pentru dinții anteriori (pozițiile 1–3) și `occlusal` pentru cei posteriori (pozițiile 4–8). Diagrama arată orientarea mesială spre linia mediană. Etichetele sunt afișate lângă desen pentru a explica suprafața, nu se bazează doar pe culoare.

## Date demonstrative și istoric

Pacientul este fictiv și nu este identificat în această demonstrație. Exemplele conțin autor fictiv și dată. Înregistrarea, corecția și schimbarea stării actualizează numai memoria paginii; reîncărcarea pierde aceste schimbări. Istoricul nu este audit persistent și datele nu sunt salvate pe server.

Constatarea este o observație clinică, starea descrie dintele, tratamentul planificat este o intenție ulterioară, iar manopera efectuată este o înregistrare operațională distinctă. Această etapă editează numai constatări și starea demo. Nu implementează tratamente, manopere, tarifare sau cronometrare.

## Integrare ulterioară

- **T05:** alimentarea props-urilor cu dosarul pacientului și reviziile persistente; maparea ID-urilor și a câmpurilor backend va necesita acord asupra contractului real T05.
- **Perspectivă:** etapa curentă oferă perspectiva pacientului; o perspectivă alternativă nu este implementată.

**T07 este separat de finalizarea T06.** T07 va lega intrările din manoperă și dinte; nu este dependență pentru completarea T06.

## Build și verificarea browser

Configurația izolată este `frontend/vite.t06.config.ts`. Intrarea este `t06-demo.html`, `base` este relativ, iar outputul dedicat este `frontend/dist-t06`. Build-ul T06 nu șterge și nu suprascrie `frontend/dist`. Artefactul verificat conține pagina HTML și cele două resurse locale referite de ea.

Scriptul reproductibil este `frontend/scripts/t06-browser-check.mjs`. Folosește Playwright 1.62.1 din `/home/raul/Projects/dentacare-tools/browser-runner/node_modules` și Chromium headless existent la `/home/raul/Projects/dentacare-tools/browsers/chromium_headless_shell-1234/chrome-headless-shell-linux64/chrome-headless-shell`. Nu instalează dependențe. URL-ul se transmite ca argument sau prin `T06_DEMO_URL`; sunt acceptate numai URL-uri loopback. Cererile către alte origini sunt blocate. Timeouturile sunt finite. Capturile și `report.json` se scriu sub `/tmp/dentacare-t06-browser-results/` (sau în `T06_RESULTS_DIR`), în afara repository-ului.

Comandă reproductibilă, din directorul `frontend`, după pornirea preview-ului de mai sus:

```sh
PATH="/home/raul/Projects/dentacare-tools/node/bin:$PATH" T06_DEMO_URL=http://127.0.0.1:5176/t06-demo.html node scripts/t06-browser-check.mjs
```

Scriptul acoperă scenariile: A) 1.6 → 1.7 → 1.6, adăugare cu suprafețe și lipsa tarifării/manoperei/timerului; B) ciorne distincte pentru fiecare dinte; C) corecție explicită și păstrarea valorilor anterioară/curentă; D) dinte lipsă 1.8 selectabil cu istoric; E) Tab/Enter/Space/săgeți, focus vizibil și ieșire prin Tab; F) desktop 1440px și mobil 375px/320px cu verificări de overflow și capturi.

### Verificări executate la 23 octombrie 2026

- `npm test -- --reporter=verbose` — trecut: 3 fișiere, 22 teste, inclusiv promisiuni controlate pentru succes/eșec întârziat la creare și corecție.
- `npm run build` — trecut: `tsc -b` și build-ul standard Vite.
- `./node_modules/.bin/vite build --config vite.t06.config.ts` — trecut; pagina și resursele build-uite există în `dist-t06`. Manifestul SHA-256 al fișierelor din `dist` a rămas identic înainte și după build-ul T06.
- `node --check scripts/t06-browser-check.mjs` — trecut.
- Browser — neexecutat. Pornirea preview-ului pe `127.0.0.1:5176` a eșuat în sandbox cu `Error: listen EPERM: operation not permitted 127.0.0.1:5176` (stack Node: `Server.setupListenHandle`, `listenInCluster`). Nu au fost create capturi și nu există rezultate browser. Testele React și build-urile nu înlocuiesc această acceptanță.
- `git diff --check` — trecut.

### Pașii browser pentru Raul

Din `frontend`, rulează comanda de preview din secțiunea de mai sus. În alt terminal, rulează comanda `T06_DEMO_URL=... node scripts/t06-browser-check.mjs`. Dacă scriptul trece, raportul și capturile apar în directorul afișat la final, sub `/tmp/dentacare-t06-browser-results/`. Scriptul folosește numai infrastructura browser deja instalată; nu instala browsere sau pachete. Dacă preview-ul eșuează și în terminalul gazdă, deschide pagina cu Vite dev pe portul 5176 și transmite același URL scriptului.

Scenariile A–F și capturile desktop, după adăugarea constatării și mobil sunt pregătite în script, dar rămân neexecutate până când browserul poate încărca URL-ul loopback.

## Limite rămase

- Este doar T06 UI etapa 1, izolată de T04/T05 și nu declară T06 complet.
- Nu există integrarea în dosarul pacientului, persistență, istoric/audit real, acceptanță browser executată sau vizualizări alternative. Dentiția temporară nu este inclusă în această etapă.
- Stările clinice și vocabularul constatărilor sunt demonstrative; validarea clinică și contractul backend necesită confirmare în taskurile de integrare.
- **T07 rămâne task separat** pentru legătura fluxurilor de manoperă cu dinții; nu este necesar pentru completarea T06.
