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

Acesta este un contract de integrare propus pentru T05; nu reprezintă un API backend existent. Înregistrările au identificatori stabili (`id`), iar callback-ul de creare primește datele clinice fără identitatea și metadatele generate la salvare. Părintele care va integra T05 va deține datele și va mapa răspunsurile backend la aceste tipuri.

Datele și actualizările demonstrației sunt separate în `demoData.ts` și `useOdontogramDemo.ts`. Componenta reutilizabilă nu importă niciun client HTTP și nu importă datele demo.

## FDI și suprafețe

ID-urile suprafețelor respectă vocabularul validat în T04 și în routerul de constatări T05 aflat în lucru: `mesial`, `distal`, `buccal`, `lingual`, `palatal`, `occlusal`, `incisal`, `vestibular`, `labial`, `approximal`, `interproximal`.

Selectorul folosește `mesial` și `distal` pentru contactele interdentare, `vestibular` pentru fața spre obraz/buze, `palatal` la dinții superiori și `lingual` la cei inferiori. Suprafața centrală este `incisal` pentru dinții anteriori (pozițiile 1–3) și `occlusal` pentru cei posteriori (pozițiile 4–8). Diagrama arată orientarea mesială spre linia mediană. Etichetele sunt afișate lângă desen pentru a explica suprafața, nu se bazează doar pe culoare.

## Date demonstrative și istoric

Pacientul este fictiv și nu este identificat în această demonstrație. Exemplele conțin autor fictiv și dată. Înregistrarea, corecția și schimbarea stării actualizează numai memoria paginii; reîncărcarea pierde aceste schimbări. Istoricul nu este audit persistent și datele nu sunt salvate pe server.

Constatarea este o observație clinică, starea descrie dintele, tratamentul planificat este o intenție ulterioară, iar manopera efectuată este o înregistrare operațională distinctă. Această etapă editează numai constatări și starea demo. Nu implementează tratamente, manopere, tarifare sau cronometrare.

## Integrare ulterioară

- **T05:** alimentarea props-urilor cu dosarul pacientului și reviziile persistente; maparea ID-urilor și a câmpurilor backend va necesita acord asupra contractului real T05.
- **T07:** legarea unei constatări de dinte/suprafețe de fluxurile de creare a manoperei, păstrând identitatea și regulile manoperei T04. Selectarea dintelui în acest modul nu declanșează T04.
- **Perspectivă:** etapa curentă oferă perspectiva pacientului; o perspectivă alternativă nu este implementată.

## Verificări

Testele modulului folosesc Vitest și `react-test-renderer`, deja incluse în frontend.

- `npm test -- --reporter=verbose` — trecut: 3 fișiere, 15 teste.
- `npm run build` — trecut: `tsc -b` și Vite production build.
- `git diff --check` — trecut.
- Browser — neverificat. Pornirea Vite pentru browser a fost blocată de sandbox: `Error: listen EPERM: operation not permitted 127.0.0.1:5173`. Inventarul CUA nu a raportat un browser disponibil; nu a fost capturată o imagine.
- Instalarea standard `npm ci` a fost blocată în scriptul postinstall esbuild: `spawnSync .../node_modules/esbuild/bin/esbuild EPERM`. `npm ci --ignore-scripts` pe Node 22 a instalat dependențele din lockfile și a permis testele și build-ul.

## Limite rămase

- Este doar T06 UI etapa 1, izolată de T04/T05 și nu declară T06 complet.
- Nu există persistență, integrare de dosar, vizualizări alternative sau dentiție temporară.
- Stările clinice și vocabularul constatărilor sunt demonstrative; validarea clinică și contractul backend necesită confirmare în taskurile de integrare.
