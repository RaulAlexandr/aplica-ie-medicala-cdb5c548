# Backup și restaurare

## Backup

Instalează utilitarele PostgreSQL (`pg_dump`, `pg_restore`, `psql`) pe gazdă. Configurează `DATABASE_URL` și `DOCUMENT_STORAGE_DIR` în mediul protejat al serviciului. Pune aplicația în mentenanță și oprește scrierile până se termină arhivarea; baza și fișierele nu au o tranzacție comună.

Din directorul `backend`:

```bash
umask 077
python -m app.operations.backup backup /var/backups/dentacare-$(date -u +%F-%H%M%S).tar.gz \
  --documents "$DOCUMENT_STORAGE_DIR" --confirm-quiescent
```

Arhiva conține dump PostgreSQL custom, documente, manifest cu versiunea aplicației, head-urile Alembic, numărul de rânduri pe tabelele operaționale și SHA-256 pentru fiecare fișier. Păstrează arhiva criptată la rest și limitează accesul; conține date medicale și credențiale indirecte.

## Restaurare și verificare

Pregătește o bază PostgreSQL nouă și un director de documente care încă nu există. Restaurarea verifică hash-urile și compatibilitatea Alembic înainte să încarce dump-ul. Refuză orice bază cu tabele, vederi sau secvențe existente.

```bash
python -m app.operations.backup restore /var/backups/dentacare-2026-11-25-120000.tar.gz \
  --documents "$DOCUMENT_STORAGE_DIR"
alembic current
psql "$DATABASE_URL" -c 'SELECT count(*) FROM patients;'
find "$DOCUMENT_STORAGE_DIR" -type f -print
curl -i http://127.0.0.1:8000/health/ready
```

Manifestul compară head-ul Alembic, totalurile tabelelor principale și hash-urile documentelor. Readiness trebuie să întoarcă HTTP 200 cu ambele verificări `ok`.
Execută restaurarea numai în destinații izolate. Dacă un pas de după încărcarea bazei eșuează, elimină baza și directorul țintă parțial restaurate și reia operația în destinații noi; PostgreSQL și sistemul de fișiere nu pot face commit atomic împreună.

## Readiness 503

```bash
curl -i http://127.0.0.1:8000/health/ready
systemctl status dentacare
journalctl -u dentacare --since '15 minutes ago' --no-pager
```

`checks.database=unavailable`: verifică serviciul PostgreSQL, rețeaua și `DATABASE_URL` din managerul de secrete, apoi reîncearcă. `checks.documents=unavailable`: verifică montarea, spațiul și permisiunile directorului configurat. Endpoint-ul nu expune detalii de conexiune sau cale.

## Căutarea unei erori după ID

ID-ul cererii este întors în `X-Request-ID`; răspunsurile 500 includ și `request_id`, cu mesaj generic.

```bash
journalctl -u dentacare --since today -o cat | jq -c 'select(.request_id == "ID_DIN_RASPUNS")'
```

Logul 500 conține stack-ul cu fișier, linie și funcție. Nu include mesajul excepției, antete, tokenuri sau query string-uri.

## Retenția jurnalelor de securitate

Valorile implicite sunt 90 de zile pentru încercările de autentificare și 365 pentru accesul T34. Suprascrie prin `LOGIN_ATTEMPT_RETENTION_DAYS` și `PATIENT_ACCESS_LOG_RETENTION_DAYS`.

```bash
python -m app.operations.retention
python -m app.operations.retention --apply
```

Prima comandă raportează numărul care ar fi șters fără modificări. Aplicarea elimină doar rânduri expirate din `login_attempts` și `patient_access_log`; istoricul clinic și financiar nu este vizat.
