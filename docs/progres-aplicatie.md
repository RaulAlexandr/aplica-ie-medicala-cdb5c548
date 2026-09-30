# Raport de progres DentaCare

**Data raportului:** 30 septembrie 2026
**Baseline deja integrat în `main`:** commit `33ae083089b3413e3d73a215498b4f6f7e8fae42`, după [PR #1](https://github.com/RaulAlexandr/aplica-ie-medicala-cdb5c548/pull/1)
**Implementarea T01 evaluată în PR deschis:** [PR #2](https://github.com/RaulAlexandr/aplica-ie-medicala-cdb5c548/pull/2), branch `feat/task-t01-clinic-setup-staff-onboarding`, commit `d7c0336b859e169d8d976f7118ad00197544da4f`
**Migrația la head-ul T01:** `0003_clinic_setup` — prezentă în PR #2, nu încă în `main`

Acest raport separă explicit funcționalitatea deja integrată în `main` de implementarea T01 aflată în PR #2. Codul Mistral nu este integrat și nu este prezentat ca funcționalitate finalizată.

## 1. Scop, arhitectură și tehnologii

DentaCare este un **MVP intern pentru operațiuni de clinică dentară**: permite unei clinici să creeze contul, personalului autorizat să se autentifice, să gestioneze pacienți și să programeze consultații cu protecție de tenant și reguli de acces.

Arhitectura este:

- **Backend:** Python 3.12, FastAPI, SQLAlchemy 2 async, Pydantic v2, PostgreSQL și Alembic.
- **Autentificare:** JWT pentru access token, refresh token rotativ păstrat doar sub formă hash, Argon2 pentru parole și revocare server-side la logout.
- **Identificatori și timp:** UUID-uri și timestamp-uri timezone-aware; programările folosesc intervale half-open. Fusul orar al clinicii este persistat în T01, dar inputul și afișarea programărilor folosesc încă fusul orar local al browserului.
- **Frontend:** React 18, TypeScript, Vite, React Router, TanStack Query, Axios și CSS simplu.
- **Persistență:** schema este schimbată prin migrații; startup-ul nu execută `create_all` și nu reconstruiește tabelele.
- **Izolare:** `clinic_id` este derivat din utilizatorul autentificat și verificat server-side pentru resursele relevante.

## 2. Baseline deja integrat în `main`

PR #1 a integrat în `main` nucleul de autentificare, pacienți, programări și istoric/audit de bază. La nivelul baseline-ului din `main`, head-ul de migrații este `0002_integrity_and_history`.

### Autentificare și sesiuni

În `main` sunt implementate:

- înregistrare clinică și creare automată a primului utilizator ca `clinic_manager`;
- login cu email și parolă;
- parole hash-uite cu Argon2;
- access token JWT și refresh token aleator, hash-uit în baza de date;
- expirare, rotație și revocare server-side a refresh tokenurilor;
- endpoint `/auth/me` și respingerea utilizatorilor inexistenți sau inactivi;
- protejarea rutelor fără token și răspunsuri 401/403 coerente;
- client Axios cu o singură reautentificare prin refresh, coordonarea cererilor concurente și invalidarea răspunsurilor unei sesiuni vechi.

### Clinici, tenant și permisiuni

În `main` sunt implementate:

- modelul clinicii și asocierea obligatorie a utilizatorilor, pacienților, camerelor și programărilor cu o clinică;
- rolurile `clinic_manager`, `doctor`, `assistant`, `reception` și `administrator`;
- autorizare FastAPI pe rol;
- filtrare server-side după clinică;
- verificarea ownership-ului pentru referințele din programări;
- izolarea între două clinici, acoperită prin teste.

### Pacienți, istoric și audit

În `main` sunt implementate listarea paginată, căutarea, contorul server-side, crearea, vizualizarea și editarea parțială a pacienților, cu câmpuri medicale și validări de păstrare a valorilor omise.

Există `patient_revisions` append-only și audit de bază pentru operații importante. Istoricul de revizii este disponibil prin API, fără ecran frontend dedicat.

### Programări și integritate

În `main` sunt implementate:

- creare și listare programări;
- pacient, medic, asistent opțional, cameră, start, durată, tip, note și stare;
- stările `scheduled`, `confirmed`, `arrived`, `in_progress`, `completed`, `cancelled` și `no_show`;
- tranziții de stare explicite;
- verificarea conflictelor pentru medic, cameră și asistent;
- intervale half-open;
- trigger PostgreSQL pentru `ends_at` și exclusion constraints GiST pentru suprapuneri concurente;
- validarea tenantului pentru toate referințele.

Frontendul din baseline are listare și formular de creare, dar nu are calendar zilnic/săptămânal complet, drag-and-drop sau operațiuni avansate de programare.

## 3. T01 în PR #2 — implementat, verificat și încă neintegrat în `main`

T01 extinde baseline-ul cu setup-ul clinicii, camere, onboarding staff, protecții de lifecycle și corecții de integritate. Toate aceste modificări sunt în PR #2 la commitul `d7c0336...`; nu trebuie descrise ca fiind deja prezente în `main` până la merge.

### Setup clinic și camere

T01 adaugă:

- setări de clinică, inclusiv persistarea fusului orar;
- creare, listare, redenumire, activare și dezactivare camere;
- unicitate case-insensitive a numelui camerei;
- blocarea dezactivării unei camere cu programări viitoare active;
- răspunsuri care enumeră programările afectate;
- protecție tranzacțională și teste PostgreSQL pentru creare/redenumire concurentă.

Administrarea camerelor este disponibilă în interfața managerului/administratorului, nu este API-only.

### Director staff și onboarding

T01 adaugă:

- listă și detaliu de personal în UI și API;
- invitații single-use cu token păstrat doar hash-uit;
- expirare, revocare, reemitere și acceptare cu parola aleasă de destinatar;
- afișarea linkului nou la creare și reemitere;
- copierea linkului și fallback pentru copiere manuală când clipboard-ul nu este disponibil;
- deactivarea staffului obișnuit, cu blocare atunci când există programări viitoare atribuite;
- director API pentru medici eligibili la programare și selector frontend de medic.

Onboardingul staffului este disponibil în UI și API în PR #2; nu este neimplementat. Emailul/SMS-ul de transmitere a invitației nu este implementat: linkul este afișat pentru copiere manuală.

### Lifecycle și concurență programări

T01 adaugă:

- reverificarea și blocarea doctorului, camerei și asistentului la reactivarea unei programări `cancelled` sau `no_show`;
- păstrarea stării anterioare când reactivarea este respinsă din cauza unei resurse inactive sau a unui conflict;
- teste PostgreSQL pentru reactivare după dezactivarea fiecărei resurse;
- teste pentru curse între reactivare și dezactivarea resursei;
- teste pentru curse între booking și dezactivarea doctorului/asistentului, în completarea testului pentru cameră.

## 4. Ce poate face fiecare rol în implementarea T01

| Rol | Prin interfață | Prin API / limitări actuale |
|---|---|---|
| `clinic_manager` | Înregistrare/login, overview, pacienți, programări, setup clinică, camere, invitații și director staff | Poate schimba starea programărilor prin API; UI-ul nu expune încă controale pentru status |
| `administrator` | Aceleași ecrane de setup și fluxuri operaționale expuse în frontend | Are aceeași politică de administrare a clinicii, camerelor și onboardingului |
| `doctor` | Pacienți, programări, logout și vizualizarea resurselor disponibile | Nu poate administra setup-ul sau invita staff; istoricul pacientului este API-only |
| `assistant` | Overview, pacienți și listare programări | Nu poate crea programări sau schimba starea; poate lista camere și medici |
| `reception` | Overview, pacienți și creare/listare programări | Nu poate edita pacientul, accesa istoricul sau administra setup-ul |

Nu există rol de pacient și nu există portal separat pentru pacienți.

## 5. Parcursuri suportate în UI

### Înregistrare, setup și onboarding

Managerul poate înregistra clinica, se poate autentifica, poate deschide Clinic setup, poate salva fusul orar, poate crea/redenumii/dezactiva camere și poate deschide Staff pentru invitații. Linkul de invitație este afișat o singură dată pentru copiere; aplicația nu pretinde că a trimis un email.

Invitatul deschide linkul, alege parola și activează contul. Linkurile acceptate sau revocate sunt respinse, iar operațiile eșuate afișează mesaje vizibile.

### Pacienți

Un rol autorizat poate căuta, crea, vizualiza și, unde politica permite, edita pacienți. API-ul suportă paginare; ecranul actual afișează cel mult 100 de rezultate și nu are navigare între pagini.

Nu există încă Patient 360 complet, timeline clinic, consultații, diagnostice, odontogramă, documente sau plăți.

### Programări

Utilizatorul cu drept de creare selectează pacientul, medicul și camera activă, apoi introduce data/ora și durata. `datetime-local` și listarea folosesc fusul orar local al browserului; API-ul păstrează instanța timezone-aware. Backendul verifică tenantul, resursele, starea și suprapunerile.

Nu există încă vedere calendaristică zilnică/săptămânală completă, drag-and-drop, operațiuni avansate de rescheduling sau asociere de proceduri.

> **T02 planificat:** implementarea calendarului operațional zilnic/săptămânal, cu input și randare în fusul orar persistat al clinicii, conversii consistente între browser și clinică și tratarea explicită a tranzițiilor DST. T01 nu pretinde că acest comportament este implementat.

### Logout și sesiune expirată

Frontendul curăță tokenurile local, revocă sesiunea server-side când este posibil și ignoră răspunsurile unei sesiuni vechi. Refresh-ul eșuat readuce utilizatorul la autentificare.

## 6. Verificare și dovezi

### Verificare CI pentru T01

Workflow-ul [CI run 36704936353](https://github.com/RaulAlexandr/aplica-ie-medicala-cdb5c548/actions/runs/36704936353), pe commitul final T01 `d7c0336b859e169d8d976f7118ad00197544da4f`, s-a încheiat cu succes. Toate joburile au fost verzi:

- **backend:** teste rapide, Ruff, compilare Python și `alembic heads`;
- **frontend:** teste Vitest și build Vite;
- **postgres:** PostgreSQL 16, upgrade pe bază goală, upgrade de la baze populate și testele de migrare/integritate/concurență, inclusiv camere case-insensitive, booking/dezactivare și reactivare/dezactivare.

Înainte de CI, testele frontend au verificat cu API mock-uit crearea invitației, reemiterea, afișarea fiecărui link nou și fallback-ul de copiere manuală. Testele rapide backend au trecut local; PostgreSQL a fost validat în jobul CI.

### Browser și limite de verificare

Parcursul T01 corectat a fost exercitat în browserul sandbox cu frontendul și backendul hosted: înregistrare clinică, setup/timezone, cameră, creare/redenumire, invitație, copiere, acceptare, reemitere, revocare, respingerea linkurilor reutilizate/revocate, login doctor, selector doctor/cameră, booking și blocarea dezactivării cu programări afectate au fost observate.

Rămân explicit neconfirmate end-to-end într-un browser real:

- expirarea sesiunii și redirectul la autentificare fără restaurarea datelor protejate;
- răspunsuri întârziate după logout sau schimbarea sesiunii;
- validarea clinică de către utilizatori medicali;
- testare de încărcare, backup/restore, monitorizare, scanare de securitate și hardening de producție.

Aceste limite sunt consemnate și în [browser-verification-gap.md](browser-verification-gap.md). Documentul păstrează istoricul blocajului CORS întâlnit în prima încercare; configurația hosted-origin a fost ulterior corectată în T01.

## 7. Ce lipsește față de platforma dentară completă

Rămân neimplementate sau parțiale:

- calendar operațional zilnic/săptămânal, inclusiv input/randare în fusul clinicii și DST — planificat pentru T02;
- Patient 360 complet, timeline clinic, consultații, diagnostice, documente și plăți;
- proceduri individuale, start/finish și urmărirea timpului efectiv;
- odontogramă adultă FDI 2D, suprafețe, observații și istoric;
- periodontologie cu șase situsuri, măsurători istorice, calcule și comparații;
- planuri de tratament și trasabilitatea procedurilor efectuate;
- program de lucru, disponibilitate, documente și metrici detaliate pentru staff;
- mesagerie internă și canale de echipă/cameră;
- inventar, loturi, expirări și alerte low-stock;
- raportare operațională și financiară;
- portal separat pentru pacient;
- email/SMS pentru invitații;
- integrarea AI/Mistral.

Administrarea clinicii, camerelor și onboardingul de bază nu mai apar în lista de funcționalități neimplementate: acestea sunt livrate în T01 PR #2, dar încă nu sunt în `main`.

## 8. Starea livrării

- **În `main`:** baseline-ul PR #1, commit `33ae083...`, cu migrația `0002_integrity_and_history`.
- **În PR #2:** T01 complet până la commitul `d7c0336...`, inclusiv migrația `0003_clinic_setup`, setup clinic, camere, onboarding staff și corecțiile de reactivare/concurență.
- **PR #2:** deschis; nu a fost încă merge-uit.
- **CI T01:** verde, workflow `36704936353`, cu backend/frontend/PostgreSQL reușite.
- **Deploy:** nu a fost făcut și nu a fost solicitat.
- **Browser:** parcursul T01 corectat a fost exercitat în sandbox; limitele de sesiune și validarea clinică rămân neconfirmate.
- **Mistral:** nu a fost integrat.
- **T02:** nu a început; următorul task planificat este calendarul operațional zilnic/săptămânal cu timezone clinică și DST.

## 9. Evaluarea pregătirii

### Demonstrație cu date sintetice

**Potrivită:** da, pentru baseline-ul din `main` și pentru fluxurile T01 din PR #2 când branch-ul este rulat cu migrația `0003_clinic_setup`. Se pot demonstra autentificarea, pacienții, programările, setup-ul clinicii, camerele și onboardingul staffului.

### Pilot cu date sintetice

**Posibil, dar limitat:** da pentru recepție, programare de bază, izolarea între clinici și administrarea T01. Pilotul nu acoperă operațiunile clinice complete, calendarul operațional, inventarul, mesageria sau raportarea. Trebuie rulate migrațiile și trebuie păstrate limitările de browser și timezone descrise mai sus.

### Date reale de pacienți și producție

**Nu este recomandat:** nu. Lipsesc Patient 360 complet, modulele clinice, backup/restore operațional, monitorizarea, hardening-ul de producție, validarea clinică și calendarul operațional cu timezone clinică.

## 10. Următorul task: T02

T02 este calendarul operațional zilnic/săptămânal. Domeniul planificat include:

1. vizualizare zilnică și săptămânală a programărilor;
2. input și randare în fusul orar persistat al clinicii, nu în fusul implicit al browserului;
3. conversii stabile între instantul UTC stocat, browser și timezone clinică;
4. reguli și teste pentru DST, inclusiv ore ambigue sau inexistente;
5. operațiuni calendaristice de bază pentru mutare/rescheduling, cu aceleași verificări de conflict și tenant isolation;
6. teste backend, frontend, PostgreSQL și browser pentru comportamentul calendaristic.

T02 nu a început și nu include încă periodontologie, inventar, portal de pacient sau AI/Mistral.

## Tabel compact de stare

| Funcționalitate | Stare actuală |
|---|---|
| Auth, JWT, refresh rotativ, logout server-side | **În `main`, implementat;** scenariile browser de expirare/răspuns întârziat rămân neconfirmate |
| Izolare tenant, RBAC și ownership | **În `main`, implementat și verificat** |
| Pacienți, căutare, paginare și editare parțială | **În `main`, implementat și verificat** |
| Revizii pacient și audit de bază | **În `main`, implementat și verificat** prin teste/API |
| Programări, tranziții și conflicte | **În `main`, implementat;** T01 adaugă reactivare cu resurse active și curse PostgreSQL în PR #2 |
| Setup clinică și timezone persistat | **În T01 PR #2, implementat și verificat;** conversia calendaristică nu este încă implementată |
| Camere: creare, redenumire, activare/dezactivare sigură | **În T01 PR #2, implementat și verificat;** disponibil în UI, nu API-only |
| Staff directory, invitații și acceptare | **În T01 PR #2, implementat și verificat;** disponibil în UI și API |
| Staff deactivation safeguards și appointment details | **În T01 PR #2, implementat și verificat** |
| Frontend auth, overview, pacienți și programări | **În `main`, implementat;** T01 adaugă ecranele setup/staff |
| Calendar zilnic/săptămânal și timezone clinică la input/randare | **Planificat pentru T02, neimplementat** |
| Patient 360, consultații, diagnostice, documente și plăți | **Neimplementat/parțial** |
| Proceduri și urmărire timp efectiv | **Neimplementat** |
| Odontogramă FDI și istoric | **Neimplementat** |
| Periodontologie | **Neimplementat** |
| Planuri de tratament | **Neimplementat** |
| Disponibilitate și administrare completă staff | **Parțial implementat;** onboardingul de bază este în T01, programul de lucru nu există |
| Mesagerie internă | **Neimplementat** |
| Inventar și mișcări de stoc | **Neimplementat** |
| Rapoarte operaționale | **Neimplementat** |
| Portal separat pentru pacient | **Neimplementat** |
| Email/SMS pentru invitații | **Neimplementat** |
| Interfețe AI/Mistral | **Neimplementat și exclus intenționat** |
