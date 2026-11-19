# Pilot local DentaCare

Pilotul folosește doar date fictive și rulează în browser pe laptop. Nu folosi pentru fișe reale. Setup-ul instalează dependențele într-un virtualenv separat al utilizatorului și în `frontend/node_modules`; nu instalează global. Este necesar Python 3.12 și Node 22.

## Pornire

Din terminal, copiază comenzile de mai jos; schimbă calea doar dacă ai ales alt checkout candidat:

```bash
export CANDIDATE=/home/raul/Projects/worktrees/aplica-ie-pilot-candidate
export DENTACARE_PILOT_HOME="$HOME/.local/share/dentacare-pilot-candidate"
export PILOT_CONFIG="$HOME/.config/dentacare-pilot-candidate/pilot.env"
mkdir -p "$(dirname "$PILOT_CONFIG")"
if [ ! -f "$PILOT_CONFIG" ]; then cp "$CANDIDATE/docs/pilot/pilot.env.example" "$PILOT_CONFIG"; fi
"$CANDIDATE/scripts/pilot/setup.sh" --checkout "$CANDIDATE" --config "$PILOT_CONFIG"
"$CANDIDATE/scripts/pilot/start.sh" --checkout "$CANDIDATE" --config "$PILOT_CONFIG"
```

Setup-ul și start-ul raportează SHA-ul checkout-ului. Un checkout cu modificări locale este refuzat implicit; `--allow-dirty` marchează explicit o pornire de dezvoltare și nu trebuie folosit pentru demonstrarea unui release.

Deschide adresa afișată (config candidat: `http://127.0.0.1:5199`). API-ul este `http://127.0.0.1:8029`, health check `http://127.0.0.1:8029/health`. Originea CORS și `VITE_API_URL` sunt setate de același config. Dacă oricare port este ocupat, start-ul raportează adresa și se oprește fără să atingă serviciul existent. Pentru alt port, copiază [pilot.env.example](pilot.env.example) în `~/.config/dentacare-pilot-candidate/pilot.env`, editează porturile libere și aliniază config înainte de pornire.

La primul start se face seed automat. Conturile generate se află local, cu permisiuni 600, în `~/.local/share/dentacare-pilot-candidate/credentials.json`. Deschide fișierul local pentru email/parolă la autentificare; managerul, medicul și recepția au rolurile respective în aceeași clinică fictivă. Seed-ul creează medicul și recepția prin endpointurile reale de invitație și acceptare. Tokenurile invitațiilor nu sunt salvate. Nu copia acest fișier în Git, loguri sau rapoarte.

Pacienții, cabinetele, serviciile și programările sunt identificabile demonstrativ. Data programărilor se raportează la ziua rulării seed-ului. Rerularea seed-ului nu dublează obiectele. Se poate rerula din checkout-ul curent:

```bash
"$CANDIDATE/scripts/pilot/seed.sh" --checkout "$CANDIDATE" --config "$PILOT_CONFIG"
```

## Oprire, restart și date

```bash
"$CANDIDATE/scripts/pilot/stop.sh" --checkout "$CANDIDATE" --config "$PILOT_CONFIG"
"$CANDIDATE/scripts/pilot/start.sh" --checkout "$CANDIDATE" --config "$PILOT_CONFIG"
```

Baza fictivă este `~/.local/share/dentacare-pilot-candidate/fictional-clinic.sqlite3`; poate fi schimbată prin `PILOT_DB_PATH` în config. Scripturile nu șterg, nu recreează și nu resetează o bază existentă. Seed-ul doar adaugă înregistrări cu ID-uri fixe dacă lipsesc. Oprirea semnalează doar PID-urile proprii și verifică directorul/comanda procesului; nu folosește `pkill`/`killall`. Logurile sunt în `~/.local/share/dentacare-pilot-candidate/logs/`.

Schema SQLite este inițializată nedistructiv prin SQLAlchemy `create_all(checkfirst=True)`, cu adaptarea DDL SQLite pentru JSONB utilizat de modelul timeline. Nu se rulează Alembic și nu se pretinde că SQLite verifică migrațiile sau constrângerile PostgreSQL. Pentru schemă nouă care nu este creată automat, folosește un fișier DB nou și păstrează backupul bazei vechi.

## Backup și probă de restaurare

Backupul pilotului conține **baza SQLite și fișierele T11** (documente/imagini clinice), plus un `MANIFEST.json` cu SHA-256. Fișierele T11 sunt în `PILOT_DOCUMENTS_DIR` (implicit `$DENTACARE_PILOT_HOME/documents`, permisiuni 0700/0600, în afara checkout-ului; `start.sh` îl transmite API-ului ca `DOCUMENT_STORAGE_DIR`). Oprește aplicația ca DB și fișierele să fie în aceeași stare:

```bash
"$CANDIDATE/scripts/pilot/stop.sh" --checkout "$CANDIDATE" --config "$PILOT_CONFIG"
"$CANDIDATE/scripts/pilot/backup.sh" --checkout "$CANDIDATE" --config "$PILOT_CONFIG"
```

Rezultatul este un director nou `backups/pilot-<UTC>/` cu `fictional-clinic.sqlite3`, `documents/` și `MANIFEST.json`. Backupul verifică `PRAGMA integrity_check` și că fiecare rând `clinical_documents` are fișierul cu SHA-256 identic; încărcările parțiale din `.incoming` nu sunt incluse.

Restaurarea de probă scrie într-un director nou, fără să înlocuiască datele active sau sursa, și reverifică manifestul și fiecare document:

```bash
"$CANDIDATE/scripts/pilot/restore.sh" --checkout "$CANDIDATE" --config "$PILOT_CONFIG" "$DENTACARE_PILOT_HOME/backups/pilot-NUME" "$DENTACARE_PILOT_HOME/restore-check"
```

`restore-check` trebuie să nu existe. Pentru a porni pe copie, setează în config `PILOT_DB_PATH=.../restore-check/fictional-clinic.sqlite3` și `PILOT_DOCUMENTS_DIR=.../restore-check/documents`. Un backup vechi `.sqlite3` (numai DB) este acceptat la restaurare, dar scriptul avertizează că nu conține fișiere T11. Nu modifica main și nu restaura peste date existente pentru a reveni la un SHA. Folosește `git checkout <SHA>` într-un worktree separat și indică explicit acel checkout scripturilor.

## Mediu și limite

Dacă sandboxul nu permite bind pe loopback, rulează comenzile într-un terminal local obișnuit. Scripturile nu fac configurări globale și nu migrează baza SQLite. Browserul trebuie să acceseze URL-ul loopback afișat de pe același laptop. Datele de cont sunt fictive și generate local. Hostingul și executabilul sunt în afara acestui pilot.
