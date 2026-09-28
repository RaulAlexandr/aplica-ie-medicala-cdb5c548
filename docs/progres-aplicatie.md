# Raport de progres DentaCare

**Data raportului:** 29 septembrie 2026  
**Ramura evaluată:** `main`  
**Commitul rezultat după merge:** `33ae083089b3413e3d73a215498b4f6f7e8fae42`  
**PR integrat:** [#1](https://github.com/RaulAlexandr/aplica-ie-medicala-cdb5c548/pull/1), cu head-ul autorizat `29da26a662c253b283a36101eec8080347e6886b`

Acest raport descrie numai codul existent în repository și verificările care au dovezi. Modulele planificate, structurile de date inexistente și implementarea separată Mistral nu sunt prezentate ca funcționalități finalizate.

## 1. Scop, arhitectură și tehnologii

DentaCare este, în starea actuală, un **MVP intern pentru operațiuni de clinică dentară**: permite unei clinici să își creeze contul, personalului autorizat să se autentifice, să gestioneze pacienți și să programeze consultații în camere, cu protecție de tenant și reguli de acces.

Arhitectura actuală este:

- **Backend:** Python 3.12, FastAPI, SQLAlchemy 2 async, Pydantic v2, PostgreSQL și Alembic.
- **Autentificare:** JWT pentru access token, refresh token rotativ păstrat doar sub formă hash, Argon2 pentru parole și revocare server-side la logout.
- **Identificatori și timp:** UUID-uri și timestamp-uri timezone-aware; programările folosesc intervale half-open.
- **Frontend:** React 18, TypeScript, Vite, React Router, TanStack Query, Axios și CSS simplu. Pachetele pentru React Hook Form, Zod și Zustand sunt disponibile, dar fluxul actual nu are nevoie de ele.
- **Persistență:** schema este schimbată prin migrații; startup-ul nu execută `create_all` și nu reconstruiește tabelele.
- **Izolare:** clinic_id este derivat din utilizatorul autentificat și verificat server-side pentru toate resursele relevante.

Structura curentă este intenționat mică: modulele implementate sunt `auth`, `patients` și `appointments`, iar modelele comune sunt în `backend/app/database.py`. Nu există încă module funcționale pentru toate domeniile din specificația inițială.

## 2. Ce este implementat, pe module

### Autentificare și sesiuni

Implementat:

- înregistrare clinică și creare automată a primului utilizator ca `clinic_manager`;
- login cu email și parolă;
- parole hash-uite cu Argon2;
- access token JWT cu durată configurabilă;
- refresh token aleator, hash-uit în baza de date, cu expirare și rotație la refresh;
- revocarea tokenului vechi după rotație și revocare la logout;
- endpoint `/auth/me` pentru sesiunea curentă;
- respingerea utilizatorilor inexistenți sau inactivi;
- protejarea rutelor fără token și răspunsuri 401/403 coerente;
- frontend Axios care atașează tokenul, încearcă o singură reautentificare prin refresh și coordonează cererile concurente;
- invalidarea cererilor active și protecția împotriva răspunsurilor întârziate după logout sau schimbarea sesiunii.

Limitarea de verificare este descrisă separat în [gap-ul de verificare în browser](browser-verification-gap.md): scenariile de expirare a sesiunii și de răspuns întârziat au acoperire automată, dar nu au fost confirmate complet end-to-end într-un browser real.

### Clinici și permisiuni de acces

Implementat:

- model de clinică și asociere obligatorie a utilizatorilor, pacienților, camerelor și programărilor cu o clinică;
- roluri recunoscute: `clinic_manager`, `doctor`, `assistant`, `reception`, `administrator`;
- dependențe FastAPI de autorizare pe rol;
- filtrare server-side după clinică pentru listări și identificarea resurselor;
- referințele din programări sunt verificate ca aparținând aceleiași clinici;
- nu se acceptă `clinic_id` din frontend pentru stabilirea tenantului;
- izolare verificată prin teste între două clinici.

Nu există încă un ecran de administrare a clinicii, invitații de personal sau schimbare de rol din interfață.

### Pacienți și informații medicale

Implementat:

- listare paginată și sortată;
- căutare după nume, telefon sau email;
- contor server-side de pacienți, separat de dimensiunea paginii;
- creare și vizualizare pacient;
- editare parțială cu schema dedicată de PATCH;
- câmpuri de identitate și contact: nume, data nașterii, sex, telefon, email, adresă, contact de urgență și ocupație;
- informații medicale: alergii, medicamente, boli cronice, sarcină, fumat, operații, condiții relevante, alerte medicale și note;
- email opțional validat; un câmp nullable poate fi golit explicit;
- câmpurile omise la PATCH rămân neschimbate, iar câmpurile obligatorii nu pot primi explicit `null`.

Există afișare frontend pentru lista, căutarea, crearea, detaliul și editarea pacientului. Nu există încă pagina Patient 360 completă cu timeline clinic, consultații, diagnostice, odontogramă, documente sau plăți.

### Istoric și audit

Implementat:

- `patient_revisions` append-only pentru schimbările importante ale câmpurilor pacientului;
- fiecare revizie păstrează actorul, câmpul, valoarea anterioară, valoarea nouă și momentul schimbării;
- evenimente de audit pentru crearea și actualizarea pacienților și pentru crearea și schimbarea stării unei programări;
- istoricul pacientului este protejat pentru roluri clinice/manageriale.

Istoricul de revizii este disponibil prin API, nu are încă ecran dedicat în frontend. Nu există încă audit pentru domeniile clinice care nu sunt implementate.

### Camere

Implementat:

- model de cameră asociat clinicii;
- nume unic per clinică și stare activă;
- creare de cameră, autorizată pentru manager și administrator;
- listare pentru personalul clinicii;
- camerele sunt resurse dinamice ale programărilor, nu sunt legate permanent de un medic.

Administrarea camerelor este în prezent accesibilă prin API; interfața nu are ecran de creare/editare camere.

### Director de personal

Implementat parțial:

- director API pentru medici eligibili la programare;
- verificare că medicul este activ și aparține clinicii;
- selector de medic în formularul de programare;
- rolurile managerului și administratorului pot fi folosite ca personal medical programabil conform politicii existente.

Nu există încă model și interfață complete pentru profiluri de staff, specializare, contact, statut de angajare, program de lucru, documente sau metrici. Nu există payroll.

### Programări

Implementat:

- creare și listare programări;
- pacient, medic, asistent opțional, cameră, start, durată, tip, note și stare;
- stările `scheduled`, `confirmed`, `arrived`, `in_progress`, `completed`, `cancelled`, `no_show`;
- tranziții de stare explicite și respingerea tranzițiilor invalide;
- verificarea conflictelor pentru medic, cameră și asistent atribuit;
- intervale half-open: o programare care se termină exact când începe următoarea este permisă;
- reactivarea unei programări anulate/no-show este reverificată pentru conflicte;
- protecție PostgreSQL prin trigger pentru `ends_at` și exclusion constraints GiST pentru suprapuneri concurente;
- toate referințele sunt verificate în tenantul utilizatorului;
- recepția, medicii, managerii și administratorii pot crea programări; asistenții le pot vedea, dar nu le pot crea sau schimba starea.

Frontendul are listare și formular de creare. Nu există încă vedere zilnică/săptămânală vizuală completă, drag-and-drop, proceduri multiple sau urmărirea timpului efectiv al procedurilor.

### Frontend

Implementat:

- ecran de autentificare cu moduri login și înregistrare clinică;
- layout cu utilizator, rol, navigație, logout și protecția rutelor prin sesiune;
- overview cu numărul server-side de pacienți și numărul de programări încărcate;
- listă și căutare pacienți;
- formular de pacient nou;
- detaliu și editare pacient;
- formular de programare cu selectoare pentru pacient, medic și cameră;
- afișarea stărilor loading, empty, validation și server error;
- refresh de sesiune, sign-out local și server-side și prevenirea restaurării datelor din sesiunea anterioară;
- build Vite verificat în CI.

## 3. Ce poate face fiecare rol astăzi

| Rol | Prin interfață | Prin API / limitări actuale |
|---|---|---|
| `clinic_manager` | Înregistrare/login, overview, listă/căutare/creare/editare pacienți, creare/listare programări, schimbare stare, logout | Poate crea camere și vedea istoricul pacientului; nu are încă administrare completă staff sau clinică |
| `administrator` | Aceleași fluxuri operaționale expuse în frontend | Poate crea camere și vedea istoricul pacientului; nu există încă modul administrativ dedicat |
| `doctor` | Listă/căutare/creare/editare pacienți, creare/listare programări, schimbare stare, logout | Vede camerele și directorul de medici; istoricul pacientului este API-only; nu are tratamente/odontogramă/periodontologie |
| `assistant` | Overview, listă/căutare/creare pacienți, listare programări, logout | Nu poate edita pacientul, crea programări sau schimba starea; poate lista camere și medici |
| `reception` | Overview, listă/căutare/creare pacienți, creare/listare programări, schimbare stare, logout | Nu poate edita pacientul sau accesa istoricul pacientului; poate lista camere și medici |

Acestea sunt roluri ale aplicației interne. Nu există rol de pacient și nu există portal separat pentru pacienți.

## 4. Parcursuri de utilizator suportate

### Înregistrare clinică și login

1. Proprietarul deschide frontendul și alege înregistrarea.
2. Introduce numele clinicii, numele său, emailul și o parolă de cel puțin 12 caractere.
3. Backendul creează clinica și utilizatorul manager, emite access și refresh token.
4. Frontendul încarcă `/auth/me` și deschide overview-ul.
5. Un utilizator existent se autentifică prin email/parolă; parolele greșite sunt respinse.

Crearea altor utilizatori, invitațiile și setarea rolurilor nu sunt încă fluxuri UI; necesită intervenție tehnică/bază de date sau API extins.

### Pacient

1. Un rol autorizat deschide Patients.
2. Caută după nume, telefon sau email sau vede lista paginată.
3. Alege New patient și completează datele de identitate, contact și câmpurile medicale disponibile.
4. Deschide detaliul pacientului.
5. Managerul, administratorul sau medicul poate edita câmpurile permise în frontend.
6. Backendul păstrează câmpurile omise și creează revizii pentru modificările urmărite.

Nu există încă atașamente, timeline clinic, consultație, diagnostic sau plată în acest parcurs.

### Programare

1. Utilizatorul cu drept de creare deschide Appointments și New appointment.
2. Caută și selectează pacientul.
3. Selectează medicul autorizat și camera activă.
4. Introduce data/ora cu fus orar, durata, tipul și notele.
5. Backendul verifică tenantul, resursele, starea și suprapunerile.
6. La conflict primește 409; altfel programarea este salvată și apare în listă.
7. Rolurile autorizate schimbă starea numai prin tranziții valide.

Nu există încă asociere de proceduri sau închidere distinctă a procedurii față de plecarea pacientului.

### Logout și sesiune expirată

1. Utilizatorul apasă Sign out.
2. Frontendul șterge local tokenurile și anulează cererile active, apoi cere revocarea server-side când este posibil.
3. Răspunsurile ulterioare care aparțin unei sesiuni vechi nu mai pot repopula interfața.
4. Dacă access tokenul expiră, clientul încearcă refresh o singură dată; dacă refreshul eșuează, sesiunea este curățată și utilizatorul revine la autentificare.

Acest parcurs este acoperit automat, dar verificarea completă în browser rămâne restantă pentru scenariile menționate în documentația gap-ului.

## 5. Probleme descoperite și corectate

Pe parcursul iterărilor, problemele principale și impactul corecțiilor au fost:

- **Izolare insuficient de explicită:** verificările au fost centralizate pe utilizatorul autentificat și clinică, reducând riscul ca un utilizator să acceseze date din alt tenant.
- **Permisiuni prea largi pentru staff:** politica a fost clarificată; asistenții pot vedea programări, dar nu le pot crea sau modifica starea, iar editarea/istoricul medical sunt limitate.
- **PATCH care putea șterge accidental date:** schema parțială separată distinge câmpurile omise de cele trimise explicit ca `null`; informațiile clinice neincluse rămân intacte.
- **Lipsa trasabilității la schimbarea pacientului:** au fost adăugate revizii de pacient și audit events fără ștergerea istoricului.
- **Contorul de pacienți dependent de pagina curentă:** overview-ul folosește endpoint server-side de count.
- **Conflicte de programare la nivel de verificare simplă:** au fost adăugate migrații PostgreSQL, trigger pentru capătul intervalului și exclusion constraints pentru doctor, cameră și asistent, astfel încât două cereri concurente să nu poată rezerva aceeași resursă.
- **Tranziții de stare implicite sau invalide:** stările și tranzițiile sunt validate explicit, inclusiv la reactivare.
- **Timestamp-uri naive sau conversii în indexuri:** migrația și testele au fost corectate pentru timestamp-uri timezone-aware, iar calculele de timp sunt păstrate în coloane/trigger, nu în expresii instabile de index.
- **Refresh concurent și răspunsuri întârziate în frontend:** refreshul este partajat între cereri, sesiunile au generații, iar logoutul anulează requesturile active și ignoră răspunsurile sesiunilor vechi.
- **Startup care putea modifica schema:** aplicarea schemei este mutată explicit în Alembic; startupul nu mai creează sau recreează tabele.
- **Lipsa verificării reproductibile:** CI include lint, compilare, teste rapide, build frontend, upgrade de bază PostgreSQL goală și teste de integritate/concurență PostgreSQL.

## 6. Verificare efectuată

### Confirmat în CI după merge

Workflow-ul [CI run 36497103953](https://github.com/RaulAlexandr/aplica-ie-medicala-cdb5c548/actions/runs/36497103953), pe commitul `33ae083089b3413e3d73a215498b4f6f7e8fae42`, s-a terminat cu succes. Toate cele trei joburi au fost verzi:

- **backend:** `pytest -q -m 'not postgres'`, Ruff, `compileall` și `alembic heads`;
- **frontend:** `npm ci`, `npm test` și `npm run build`;
- **postgres:** PostgreSQL 16, `alembic upgrade head` pe bază goală și `tests/test_postgres_integrity.py -m postgres`, inclusiv testele de concurență.

### Confirmat prin istoric și cod

- PR #1 avea exact head-ul autorizat înainte de merge: `29da26a...`.
- Toate check-urile cerute înainte de merge erau finalizate cu succes.
- PR-ul a fost merge-uit în `main` cu commitul `33ae083...`.
- Gap-ul pentru verificarea în browser a fost înregistrat în `docs/browser-verification-gap.md` înainte de merge.
- Migrația curentă este `0002_integrity_and_history`.
- Testele rapide acoperă autentificare, izolarea tenantului, refresh rotation/replay, conflicte, tranziții, revizii, logout, validare email și RBAC.
- Testele PostgreSQL acoperă migrarea și integritatea/concurența pentru rezervări.

### Neverificat complet / limitări de evidență

- Nu există dovadă completă de verificare end-to-end într-un browser real pentru expirarea sesiunii și răspunsurile întârziate după logout/schimbarea sesiunii.
- Nu a fost efectuată o validare clinică de către utilizatori medicali.
- Nu există test de încărcare, backup/restore, monitorizare, scanare de securitate sau hardening de producție documentat.
- În sandboxul inițial Docker nu a fost disponibil pentru verificarea locală; verificarea reală PostgreSQL a fost realizată în jobul CI cu serviciu PostgreSQL 16.

## 7. Ce lipsește față de cerințele platformei dentare

Cerințele complete sunt disponibile în `dental_clinic_coding_stress_test_prompt.md`. Față de acestea, lipsesc funcțional sau sunt doar parțiale:

- Patient 360 complet, timeline clinic, consultații, diagnostice, documente și plăți;
- urmărirea timpului pentru proceduri individuale și separarea de finalizarea programării;
- odontogramă adultă FDI 2D, suprafețe, observații și istoric;
- periodontologie cu șase situsuri, măsurători istorice, calcule și comparații;
- planuri de tratament, articole, statusuri și trasabilitatea procedurilor finalizate;
- administrare completă de personal, disponibilitate, program de lucru, documente și metrici;
- mesagerie internă, canale de echipă/cameră și unread state;
- inventar, mișcări imuabile, loturi/expirări și alerte low-stock;
- raportare operațională și financiară-ready pe perioade;
- portal separat pentru pacient și invitații clinic-specific;
- interfețe pentru integrarea viitoare AI, fără apeluri LLM reale;
- management clinic, invitații și configurare de utilizatori/roluri prin interfață;
- calendar zilnic/săptămânal complet și operațiuni avansate de programare.

Acestea nu trebuie considerate implementate doar pentru că apar în cerințe sau pentru că pachetele frontend permit extinderea lor.

## 8. Starea livrării

- **Merged în `main`:** da, PR #1, merge commit `33ae083089b3413e3d73a215498b4f6f7e8fae42`.
- **Head-ul autorizat al PR-ului:** `29da26a662c253b283a36101eec8080347e6886b`, neschimbat înainte de merge.
- **CI post-merge:** verde, run `36497103953`.
- **Deploy:** nu a fost făcut și nu a fost solicitat. Nu există mediu public de producție rezultat din această sarcină.
- **Acces local:** PostgreSQL 16, `alembic upgrade head`, backend FastAPI pe `localhost:8000` și frontend Vite pe `localhost:5173`, conform README.
- **Intervenție tehnică necesară:** configurarea `.env`, secret JWT, baza PostgreSQL, migrațiile și pornirea celor două procese; crearea staffului suplimentar și a camerelor se face prin API în starea actuală.
- **Workspace:** checkout-ul local este pe `main`, sincronizat cu `origin/main`, fără modificări necomise, nepushed sau neintegrate în acest workspace la momentul raportului.
- **Mistral:** nu a fost integrat și este exclus din acest raport, conform instrucțiunii.

## 9. Evaluarea pregătirii

### Demonstrație

**Potrivit cu date sintetice:** da. Se pot demonstra înregistrarea clinicii, loginul, overview-ul, pacienții, căutarea, editarea și programările cu protecția conflictelor. Este necesară pornirea tehnică a PostgreSQL, backendului și frontendului.

### Pilot cu date sintetice

**Posibil, dar limitat:** da pentru fluxurile de recepție și programare de bază și pentru testarea izolării între clinici. Înainte de pilot trebuie configurate conturile/rolurile suplimentare prin intervenție tehnică și trebuie făcută o verificare browser a sesiunilor. Lipsesc modulele clinice, inventory, mesagerie și raportare, deci pilotul nu acoperă operațiunile complete ale unei clinici.

### Date reale de pacienți

**Nu este recomandat:** nu. Lipsesc Patient 360 complet, audit clinic extins, documente, odontogramă, periodontologie, tratamente, politici operaționale, backup/restore, monitorizare, hardening și validare clinică. Codul actual este un milestone funcțional pentru MVP, nu o declarație de pregătire pentru date medicale reale sau producție.

## 10. Milestone recomandat

Recomand următorul milestone: **nucleul clinic pentru un pacient**, cu scop limitat la planuri de tratament, proceduri efectuate și primul Patient 360 auditabil.

### Domeniu inclus

1. modele și migrații pentru consultație, diagnostic, plan de tratament, itemi de plan și proceduri efectuate;
2. start/finish pentru procedură cu `procedure_started_at`, `procedure_completed_at` și durată calculată;
3. statusuri și tranziții server-side pentru plan și itemi;
4. timeline API pentru pacient care agregă evenimentele existente și noile evenimente clinice;
5. ecrane frontend pentru plan, itemi, finalizare procedură și timeline;
6. audit și tenant isolation pentru fiecare operație;
7. teste pentru tranziții, durate, trasabilitatea itemului și acces între clinici.

### Dependențe

- schema și politicile existente din `main`;
- decizie clinică asupra catalogului de proceduri, statusurilor și regulilor de editare;
- PostgreSQL disponibil pentru migrații și teste de integritate;
- roluri clinice confirmate pentru medic, asistent și manager;
- date sintetice reprezentative pentru acceptanță.

### Criterii de acceptare

- un medic poate crea un plan cu cel puțin doi itemi, iar fiecare item are dinți/suprafețe unde este cazul, prioritate, durată estimată și status;
- planul și itemii respectă tranziții valide și resping tranzițiile invalide;
- procedura efectuată păstrează legătura cu itemul de plan și calculează durata numai între start și finish;
- finalizarea programării nu modifică durata clinică a procedurii;
- timeline-ul pacientului este cronologic, tenant-scoped și nu șterge evenimente istorice;
- un utilizator din altă clinică primește 404/403 și nu vede datele;
- testele automate, migrația PostgreSQL și CI sunt verzi;
- fluxul este verificat end-to-end în browser pentru creare, editare, finalizare și logout;
- nu se începe periodontologia, inventarul, portalul sau AI în același milestone.

Acest milestone este o recomandare; nu face parte din implementarea raportată aici.

## Tabel compact de stare

| Funcționalitate | Stare |
|---|---|
| Înregistrare clinică, login, JWT, refresh rotativ, logout server-side | **implementat, dar nu complet verificat** — scenariile browser de expirare/răspuns întârziat rămân de confirmat |
| Izolare tenant, RBAC și validarea ownership-ului | **implementat și verificat** |
| Pacienți, căutare, paginare, câmpuri medicale și editare parțială | **implementat și verificat** |
| Revizii pacient și audit de bază | **implementat și verificat** prin teste/API |
| Camere și director API de medici | **implementat, dar nu complet verificat** în interfață; administrarea camerelor este API-only |
| Programări, tranziții și conflicte doctor/cameră/asistent | **implementat și verificat**, inclusiv PostgreSQL/concurență în CI |
| Frontend staff pentru auth, overview, pacienți și programări | **implementat, dar nu complet verificat** end-to-end în browser |
| Timeline Patient 360, consultații, diagnostice, documente și plăți | **parțial implementat** — există revizii/audit de bază, nu Patient 360 complet |
| Proceduri și urmărire timp efectiv | **neimplementat** |
| Odontogramă FDI și istoric odontogramă | **neimplementat** |
| Periodontologie cu șase situsuri și calcule | **neimplementat** |
| Planuri de tratament | **neimplementat** |
| Administrare completă staff și disponibilitate | **parțial implementat** — doar director API de medici pentru programare |
| Mesagerie internă | **neimplementat** |
| Inventar și mișcări de stoc | **neimplementat** |
| Rapoarte operaționale | **neimplementat** |
| Portal separat pentru pacient | **neimplementat** |
| Interfețe pentru viitorul AI/Mistral | **neimplementat** și exclus intenționat din acest task |
