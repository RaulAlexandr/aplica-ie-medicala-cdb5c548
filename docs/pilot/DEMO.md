# Ghid demo DentaCare — pilot cu date fictive

Pentru Serena și echipă. Ghidul se urmează pe roluri (recepție, medic, manager), fără pregătire manuală: totul pornește dintr-o singură comandă și vine cu pacienți, vizite, plan, evaluări, documente și încasări fictive.

> **Date fictive, nu fișe reale.** Nu introduce date reale de pacienți. Aplicația nu emite factură sau bon fiscal.

## 1. Pornire și oprire

Din checkout-ul curat al aplicației (aceeași comandă pe laptopul lui Raul):

```bash
cd /home/raul/Projects/worktrees/aplica-ie-pilot-demo
scripts/pilot/demo.sh up        # creează config, venv, bază NOUĂ, pornește API + web, rulează seed-ul
```

La final apar adresele: **web `http://127.0.0.1:5179`**, API `http://127.0.0.1:8039/health`. Deschide adresa web într-un browser setat pe fusul **Europe/Bucharest** (fusul clinicii).

| Ce vrei | Comandă |
|---|---|
| Oprire | `scripts/pilot/demo.sh stop` |
| Repornire pe ACEEAȘI bază (seed-ul nu dublează nimic) | `scripts/pilot/demo.sh resume` |
| Demo complet nou | `DENTACARE_PILOT_HOME=$HOME/.local/share/dentacare-demo-2 PILOT_CONFIG=$HOME/.config/dentacare-demo-2/pilot.env scripts/pilot/demo.sh up` (alte porturi în config dacă rulează și primul) |

- **Demo-uri create înainte de scurtarea reviziei Alembic T13** (`t13_add_patient_financial_entries` → `t13_patient_financial_entries`): nu le relua cu `resume`; pornește un demo nou, cu bază nouă:

  ```bash
  cd /home/raul/Projects/worktrees/aplica-ie-pilot-demo
  DENTACARE_PILOT_HOME=$HOME/.local/share/dentacare-pilot-demo-v2 PILOT_CONFIG=$HOME/.config/dentacare-pilot-demo-v2/pilot.env scripts/pilot/demo.sh up
  ```

  (Notă tehnică: demo-ul rulează pe SQLite creat cu `create_all`, fără tabelul `alembic_version`, deci schema veche nu este blocată de redenumire; măsura este totuși cerută ca să nu amestecăm baze create înainte și după corecție. Nu șterge demo-ul vechi.)
- `demo.sh up` **refuză** să pornească dacă baza există deja și nu șterge, nu resetează și nu suprascrie nimic. Un checkout cu modificări locale este refuzat.
- Date: `~/.local/share/dentacare-pilot-demo/` (bază SQLite `fictional-clinic.sqlite3`, documente, loguri). Config: `~/.config/dentacare-pilot-demo/pilot.env`.
- Backup/restaurare (cu aplicația oprită): vezi [README.md](README.md) (`backup.sh`, `restore.sh`, apoi `verify_copy.py SURSĂ COPIE`).

### Conturi

Trei conturi în aceeași clinică fictivă, **Clinica Demo Fictivă**: managerul *Mara Demo*, medicul *Dr. Alex Demo* și recepționerul *Radu Recepție Demo*. Emailul și parola fiecăruia sunt în fișierul local `~/.local/share/dentacare-pilot-demo/credentials.json` (permisiuni 600). Deschide fișierul local; nu îl copia în chat, Git sau rapoarte. Autentificare: câmpurile **Email** / **Password**, butonul **Sign in**.

### Ce conține seed-ul

| Pacient | Ce găsești |
|---|---|
| **Ioana Exemplu** | vizită azi 09:00 *în desfășurare*; manoperă REST-DEMO pe 1.6 efectuată (timp măsurat); constatări pe 1.6, 3.6, 2.1; dinții 1.8 și 2.8 lipsă; notă clinică; plan acceptat în două etape cu prima manoperă legată; încasare 200 RON, o încasare de 50 RON anulată cu motiv; alergie notată |
| **Andrei Model** | vizită azi 11:00; două evaluări parodontale finalizate (acum 90 de zile și acum 2 zile) pentru comparație; avans 300 RON; constatări pe 4.6 și 1.1; 3.6 lipsă |
| **Elena Test** | vizită azi 14:00 (confirmată); radiografie pe 3.6, fotografie intraorală și un document PDF fictiv; o ajustare de 25 RON (manager) |

Pe lângă acestea: 2 cabinete, 3 servicii (CONS-DEMO 150, PROF-DEMO 250, REST-DEMO 400 RON) și programări în zilele următoare.

## 2. Scenarii pe roluri

La fiecare pas: **Fă** (acțiunea) și **Observă** (ce trebuie să se întâmple). Dacă ce vezi diferă, raportează conform secțiunii 4.

### Recepție (Radu)

1. **Agenda.** Fă: *Appointments*. Observă: ziua curentă, cabinetele „Cabinet Demo 1/2”, trei vizite la 09:00 (în desfășurare), 11:00 (programată), 14:00 (confirmată), cu orele fusului clinicii, nu ale browserului.
2. **Programare nouă.** Fă: **New appointment**; caută „Elena”, alege pacienta, doctorul, cabinetul, **Clinic-local start** (o zi liberă), tipul; **Create appointment**. Observă: programarea apare în agendă; orele se introduc ca ore ale clinicii. Dacă intervalul e ocupat, aplicația îl refuză cu mesaj.
3. **Încasare.** Fă: *Patients* → Ioana Exemplu → secțiunea **Cont pacient**. Observă: *Servicii efectuate 400 RON*, *Încasat net 200 RON*, *Sold 200 RON de plată*; mișcarea de 50 RON apare **anulată** cu motivul ei (istoricul nu se rescrie). Fă: tip **Încasare**, sumă `100.00`, metodă **Numerar**, vizita asociată, motiv, **Înregistrează**. Observă: încasatul crește cu exact 100 RON, soldul se recalculează; un dublu clic nu înregistrează de două ori.
4. **Încasări zi.** Fă: meniul **Încasări zi**. Observă: net pe metodă (numerar/card/transfer/alta), total net, anulările listate, mențiunea că **nu este raport fiscal**.
5. **Limite de rol.** Observă: nu ai *Clinic setup* sau *Staff*; nu poți face ajustări (doar managerul).

### Medic (Dr. Alex)

1. **Vizita în desfășurare.** Fă: *Appointments* → vizita Ioanei → deschide vizita. Observă: manopera **REST-DEMO · FDI 16 · occlusal** *Completed*, prețul catalogului ca snapshot (400 RON), timpul activ măsurat; manopera finalizată **nu a închis vizita** (butonul **Close visit** rămâne o acțiune separată). Deschiderea fișei sau a vizitei nu pornește niciun timp.
2. **Odontograma.** Fă: *Patients* → Ioana → **Odontogramă clinică**; apasă dintele **1.6**. Observă: constatarea *Carie* (ocluzală) **Finalizată**; 1.8 și 2.8 sunt **Lipsă**; istoricul din dreapta arată cine și când. Constatarea, tratamentul planificat și manopera efectuată sunt înregistrări distincte.
3. **Manoperă din dinte.** Fă: pe Elena Test, apasă dintele **3.6** → **Add performed procedure for this tooth**; alege vizita de azi, serviciul PROF-DEMO, o notă; **Save procedure**. Observă: dintele e precompletat, manopera e salvată *pending* și **timpul nu a pornit**. Fă: deschide vizita → pe cardul manoperii **Start**, **Pause**, **Resume**, **Complete**. Observă: pauza nu intră în timpul activ; segmentele de timp se păstrează; vizita rămâne deschisă.
4. **Planul.** Fă: Ioana → **Planuri de tratament** → *Plan restaurativ demonstrativ* → **Deschide planul**. Observă: două etape, versiunea acceptată, prima manoperă **Efectuat** și legată de manopera efectuată; estimarea nu este factură.
5. **Parodontal.** Fă: Andrei → **Evaluare parodontală**; la **Prima evaluare** alege data de acum 90 de zile, la **A doua evaluare** data de acum 2 zile; **Compară**. Observă: diferențele (adâncimile scad cu 1 mm, `Δ -1`), BOP pe a doua evaluare; evaluările vechi rămân neschimbate. Gol = nemăsurat, „Nu” = măsurat și absent.
6. **Documente.** Fă: Elena → **Documente și imagini**; la radiografie **Vizualizează**, apoi **Mărește**. Observă: imaginea se deschide autorizat, zoom 125%; fotografia și PDF-ul sunt listate; există **Descarcă**, **Compară**, **Arhivează**. Fișierele nu sunt accesibile fără autentificare.
7. **Pacient inexistent.** Fă: în bara de adrese pune un ID de pacient inventat (`/patients/00000000-0000-4000-8000-000000000000`). Observă: imediat, mesajul **„Pacient inexistent sau fără acces”** (același mesaj pentru un pacient din altă clinică).

### Manager (Mara)

1. **Configurare.** Fă: **Clinic setup**. Observă: fusul clinicii (Europe/Bucharest), programul de lucru (inițial *Unconfigured*: programarea este permisă), cele două cabinete cu **Rename/Deactivate**, blocarea disponibilității unui medic/cabinet.
2. **Catalog.** Fă: **Procedure catalog**. Observă: CONS-DEMO, PROF-DEMO, REST-DEMO cu durată și preț; **Edit/Deactivate**. Schimbarea prețului nu modifică retroactiv manoperele deja efectuate (au tariful istoric ca snapshot).
3. **Ajustare și anulare.** Fă: Elena → **Cont pacient** → tip **Ajustare de plată suplimentară**, sumă `10.00`, motiv, **Înregistrează**; apoi la mișcarea nouă **Anulează mișcarea**, motiv, **Confirmă anularea**. Observă: ambele mișcări rămân în istoric; efectul net este zero; doar managerul face ajustări.
4. **Personal.** Fă: **Staff** — invitații și activare conturi. (Nu este scenariul principal al acestui demo.)

## 3. Limite cunoscute

- **Date fictive**, generate local; imaginile sunt desenate local, fără date reale.
- **Fără fiscal:** nu există factură, bon sau raport fiscal; sumele sunt evidență internă.
- **SQLite local:** o singură bază pe laptop, fără replicare; nu înlocuiește verificările PostgreSQL din CI. Baza se creează cu `create_all`, fără migrații Alembic.
- **Formulare configurabile și anamneză (T12)** nu sunt în această versiune.
- Datele seed sunt relative la ziua primei porniri (vizitele „azi” rămân pe acea zi). Pe altă zi, agenda „de azi” nu mai are vizite seed; cele viitoare rămân.
- Browserul trebuie să fie pe Europe/Bucharest pentru ca orele să coincidă cu cele ale clinicii în câmpurile native.
- Pe ecranele *Procedure catalog*, *Clinic setup* și *Staff* separatorul „·” apare ca text `·` (defect cunoscut, în afara acestui livrabil).
- `demo-walkthrough.mjs` **adaugă date** (o programare, o încasare, o ajustare cu anulare, o manoperă): rulează-l pe o copie restaurată, nu pe baza pe care o arăți.

## 4. Cum raportezi o problemă

Scrie, într-un mesaj scurt:

1. **Ce:** ce ai făcut și ce ai văzut față de ce te așteptai (ex.: „am apăsat Start și cronometrul nu a pornit”).
2. **Unde:** rolul (recepție/medic/manager), pacientul fictiv, ecranul și adresa din bara browserului (fără parole), butonul apăsat.
3. **Ora:** data și ora exactă (`hh:mm`, ora clinicii) — ajută la găsirea cererii în loguri.
4. Opțional, o captură de ecran **fără date reale**. Nu atașa fișierul de conturi, parole sau tokenuri.

Loguri locale (pentru cine investighează): `~/.local/share/dentacare-pilot-demo/logs/api.log` și `web.log`.

## 5. Verificare automată a ghidului

`scripts/pilot/demo-walkthrough.mjs` parcurge acest ghid pe datele seed, pe toate cele trei roluri (agenda, programare, încasare, încasări zi; vizită, odontogramă, manoperă din dinte cu timer, plan, parodontal, documente cu hash, pacient inexistent, favicon; configurare, catalog, ajustare și anulare), cu verificări și clasificarea erorilor de consolă. Variabilele necesare sunt cele ale celorlalte scripturi de acceptanță (`PLAYWRIGHT_MODULE`, `CHROMIUM_EXECUTABLE`, `PILOT_WEB_URL`, `PILOT_API_URL` terminat în `/api`, `PILOT_CREDENTIALS`, `DENTACARE_PILOT_HOME`, `DENTACARE_APP_SHA`).
