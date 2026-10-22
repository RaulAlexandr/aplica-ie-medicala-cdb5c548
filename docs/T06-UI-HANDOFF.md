# T06 — Odontogramă UI, etapa 1

## Implementat

- Diagramă 2D pentru pozițiile FDI ale dentiției permanente adulte: 11–18, 21–28, 31–38, 41–48.
- Arcadele sunt aranjate din perspectiva pacientului. În stânga e dreapta pacientului, iar în dreapta e stânga pacientului. Numărul FDI și starea apar pe fiecare dinte.
- Toți dinții rămân selectabili, inclusiv dinții marcați lipsă. Se poate selecta cu click, Tab/Enter sau cu săgețile între pozițiile vecine și arcade.
- Panoul individual separă starea dintelui, constatările și evenimentele din istoricul demonstrativ. Formularul permite mai multe constatări pe același dinte și pe aceleași suprafețe.
- O corecție demo schimbă valoarea curentă și adaugă în istoric valoarea anterioară și cea înlocuitoare.
- Dentiția temporară nu este inclusă. FDI identifică și dentiția temporară prin cadranele 5–8; implementarea acestei etape afișează doar cele 32 de poziții permanente adulte.

## Pornirea demonstrației

Din directorul `frontend`:

```sh
npm run dev -- --host 127.0.0.1
```

Deschide `http://127.0.0.1:5173/t06-demo.html`. Pagina afișează mesajul „Demonstrație — date locale, fără salvare pe server.”

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

## Verificări

Testele modulului folosesc Vitest și `react-test-renderer`, deja incluse în frontend.

- `npm test -- --reporter=verbose` — trecut la 23 octombrie 2026: 3 fișiere, 18 teste.
- `npm run build` — trecut: `tsc -b` și Vite production build.
- `git diff --check` — trecut.
- Browser — neverificat la 23 octombrie 2026. Inventarul CUA a raportat `apps: []`, `browsers: []`; Vite a eșuat la pornire cu `Error: listen EPERM: operation not permitted 127.0.0.1:5173`. Nu a fost capturată o imagine.
- Dependențele erau deja instalate în acest worktree la verificarea curentă. La instalarea anterioară, `npm ci` standard a fost blocată de sandbox în postinstall esbuild cu `spawnSync .../node_modules/esbuild/bin/esbuild EPERM`; `npm ci --ignore-scripts` pe Node 22 a permis atunci testele și build-ul.

## Pași manuali pentru browser

Din directorul `frontend`, cu Node 22 local:

```sh
PATH="/home/raul/Projects/dentacare-tools/node/bin:$PATH" npm run dev -- --host 127.0.0.1
```

Deschide `http://127.0.0.1:5173/t06-demo.html` și verifică:

1. 1.6 este selectat și are constatarea existentă.
2. Selectează 1.7, adaugă o constatare de carie și alege suprafețe; revino la 1.6, apoi la 1.7 și confirmă că datele rămân la dintele lor.
3. Selectează 1.8 marcat lipsă și consultă istoricul; selectează dinții cu Tab/Enter și săgețile și urmărește indicatorul vizibil de focus.
4. Repetă la lățime îngustă (320–375 px) și verifică faptul că numerele dinților și comenzile rămân utilizabile.

## Limite rămase

- Este doar T06 UI etapa 1, izolată de T04/T05 și nu declară T06 complet.
- Nu există integrarea în dosarul pacientului, persistență, istoric/audit real, acceptanță browser sau vizualizări alternative. Dentiția temporară nu este inclusă în această etapă.
- Stările clinice și vocabularul constatărilor sunt demonstrative; validarea clinică și contractul backend necesită confirmare în taskurile de integrare.
- **T07 rămâne task separat** pentru legătura fluxurilor de manoperă cu dinții; nu este necesar pentru completarea T06.
