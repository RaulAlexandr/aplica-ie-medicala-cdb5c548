# DentaCare: restanțe și limite la 2026-10-31

Inventar pentru audit, nu verdict nou de defect. Severitatea și starea se stabilesc prin reproducere la SHA-ul relevant. [Stare curentă](CURRENT-STATUS.md), [plan](sources/PlanTotal.md).

| ID | Zonă | Stare și următoarea verificare |
|---|---|---|
| QA-01 | S07 context pacient/vizită | PARTIAL; probează și două programări ale aceluiași pacient, răspunsuri întârziate, ID-ul salvat |
| QA-02 | S08/S09 ciorne/roluri | NOT RUN în browser Manus; folosește invitații în aceeași clinică, sesiuni separate, list/detail/update/finalize/revisions/record/timeline și acces direct |
| QA-03 | Timer | Durata și statusurile sunt vizibile; lipsesc segmentele în artefacte pentru verificarea independentă a pauzei/duplicatelor |
| QA-04 | Închidere | pending/paused observate; completează dovada running și consistența can_close_visit față de API |
| QA-05 | Finding | Proba a folosit note; creează finding distinct fără manoperă sau timer suplimentar |
| QA-06 | Export dovezi | Originalul persisted-sanitized.json are secțiuni etichetate, nu JSON unic valid; păstrează sursa și etichetează orice normalizare drept derivată |
| PROD-01 | Timeline | Populare automată incompletă, proiecția rămâne goală fără refresh; task separat de produs, nu pierdere de date demonstrată |
| PROD-02 | Durată manuală | Verifică modelul și istoricul față de cerința de durată retrospectivă distinctă de măsurare; simpla existență override-duration nu înseamnă acceptanță |
| PROD-03 | Addendum/corecții | Verifică legătura cu nota originală, autorul, motivul și istoricul; nu prezenta modelul actual drept lanț complet fără probe |
| PROD-04 | T03 localizări permise | Cerință din plan; verifică modelul/UI și documentează lipsa dacă nu există, fără a schimba planul după cod |
| QA-07 | Migrare suplimentară | Testul necomis din worktree-ul QA nu a fost executat/colectat în CI; separă-l de probele de migrare care au trecut |
| T06-01 | Odontogramă | UI/demo separat la 003b617, fără persistență/API/audit clinic real; 22 teste React/build raportate/verificate anterior, fără acceptanță browser |
| T06-02 | Probe locale | Modificări necomise în script/DemoApp din worktree-ul t06-browser; păstrează-le și raportează exact ce versiune citești |
| OPS-01 | Main/PR-uri | Candidatul integrat și UI-ul nu sunt în main; PR #3/#4/#5 rămân deschise, #6 merged numai în backend |
| OPS-02 | Publicare documentație | Branch docs separat; verifică PUBLICATION.md local înainte de a afirma că o copie este pe GitHub |
| ENV-01 | Sandbox | DNS/loopback/async SQLite au fost blocate în anumite sesiuni Codex; Manus cloud a pornit serviciile. Nu generaliza blocajul la laptop sau la toate mediile |
| ENV-02 | GitHub | Citirea funcționează; scrierea prin integrare a primit 403 în încercările anterioare, Git CLI a avut DNS blocat. Nu schimba auth/bugete pentru a ascunde un blocaj |
| SEC-01 | Dependențe | Instalările anterioare au raportat advisories; nu există în această consolidare un audit de vulnerabilități curent. Astra verifică versiunile/advisory-urile actuale dacă accesul permite, fără audit fix automat |
| DOC-01 | Istoric | Unele note vechi aveau stări intermediare/numere CI greșit asociate. Se citesc ca istoric datat; CURRENT-STATUS și snapshoturile pe SHA au prioritate pentru starea actuală |

T07–T32 și T35 nu au o livrare acceptată identificată în acest inventar. T33 are probe de test/CI, dar nu acceptanță clinică/staging complet. T34 are măsuri transversale de securitate, dar nu dovadă de pregătire operațională completă. Matricea completă este în ROADMAP.
