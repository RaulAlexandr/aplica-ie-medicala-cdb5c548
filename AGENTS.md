# DentaCare: instrucțiuni pentru agenți

Citește [docs/project/START-HERE.md](docs/project/START-HERE.md), [CURRENT-STATUS](docs/project/CURRENT-STATUS.md) și numai fișa taskului cerut. Pentru auditul complet cerut explicit: [ASTRA-AUDIT](docs/project/ASTRA-AUDIT.md).
Planul original este [PlanTotal](docs/project/sources/PlanTotal.md), T00–T35. Cerința nu este dovada implementării; rapoartele istorice nu sunt instrucțiuni active.

- Raul pornește/delegă manual agenții; numele modelului sau terminalului nu atribuie un task. Nu porni alți agenți fără cerere explicită.
- Confirmă cwd, branch, HEAD și status înainte de scriere. Păstrează schimbările altor sesiuni. Nu folosi reset/clean/stash/force-push ca simplă curățare.
- Publicarea unui branch/PR trebuie să fie în task. Fără merge în main/deploy fără cerere explicită. Un audit autorizează citire, verificări izolate și rapoarte, nu fixuri de produs.
- Stack și verificări: [ENVIRONMENT](docs/project/ENVIRONMENT.md) și workflow-ul branch-ului curent. CI folosește Python 3.12, Node 22, PostgreSQL 16. Docker nu este obligatoriu pentru audit/demo.
- PostgreSQL/Alembic și SQLite/create_all sunt probe diferite. Nu aplica migrațiile PostgreSQL pe SQLite. Nu declara browser din build sau CI nou dintr-un SHA vechi.
- Tenantul și rolurile se aplică pe server, inclusiv la erori/revizii/timeline. Ciorna este numai a autorului; manager/admin nu au excepție implicită. Verifică implementarea efectivă față de cerință.
- Deschiderea fișei sau selectarea dintelui nu pornește timpul. Manopera, constatarea și planificarea sunt distincte. Complete manoperă nu închide vizita; pending/running/paused blochează închiderea conform cerinței acceptate.
- Nu copia secrete, .env, date reale de pacienți, credential stores sau build/dependency directories în rapoarte/commituri. Nu elimina teste sau slăbi aserțiuni pentru verde.
- Predă SHA, fișiere schimbate, probe efective, blocaje și următorul pas. Nu modifica rapoartele originale ale altei sesiuni pentru a rescrie ce a executat.
