# T03 v1 — localizări permise ale unui serviciu din catalog

Status: pilot local, date fictive. Completează T03 (catalog) pentru T04 (manoperă efectuată), T07 (ambele trasee)
și T08 (item de plan). Migrare: `t03_catalog_allowed_locations` (down: `t13_add_patient_financial_entries`).

## Ce localizări există azi

Modelul are o singură formă de localizare, aceeași în T04 și T08:

- `tooth_fdi` — opțional, dinte permanent FDI `11–18, 21–28, 31–38, 41–48`;
- `tooth_surface` — opțional, listă de suprafețe separate prin virgulă (`mesial, distal, buccal, lingual, palatal,
  occlusal, incisal, vestibular, labial, approximal, interproximal`).

Nu există arcadă, cadran, sextant sau dinți temporari. Diferențe preexistente, păstrate neschimbate: T04 acceptă
suprafețe fără dinte; T08 refuză suprafețe fără dinte (422). T07 nu are alt endpoint: ambele trasee
(„manoperă → dinte” din vizită și „dinte → manoperă” din dosar/odontogramă) folosesc același formular
(`ProcedureForm`) și același `POST /api/performed-procedures`.

## Setul ales

`procedure_catalog.allowed_location`, `VARCHAR(20) NOT NULL DEFAULT 'any'`, cu
`CHECK (allowed_location IN ('any', 'no_tooth', 'tooth', 'tooth_surface'))`.

| Valoare | Înțeles | Dinte | Suprafețe |
|---|---|---|---|
| `any` | comportamentul de dinainte de T03 (implicit) | opțional | opțional (regulile T04/T08 de mai sus rămân) |
| `no_tooth` | serviciu fără localizare dentară (consultație, igienizare generală) | interzis | interzise |
| `tooth` | dinte obligatoriu (extracție, coroană) | obligatoriu | opțional |
| `tooth_surface` | dinte și cel puțin o suprafață (obturație) | obligatoriu | cel puțin una |

Setul este minim și se mapează direct pe cele două câmpuri existente; nu introduce forme noi de localizare.
Validarea suprafețelor valide pentru un anumit dinte (ex. incisal vs. ocluzal) rămâne cea existentă.

## Reguli

- **Migrare additivă.** Rândurile existente primesc `any`, deci serviciile existente funcționează exact ca înainte.
  `server_default` rămâne, astfel încât inserările care nu cunosc coloana primesc tot `any`.
- **Catalog.** `POST /api/procedures` acceptă `allowed_location` (implicit `any`); `PATCH /api/procedures/{id}`
  îl poate schimba; răspunsurile îl includ. Valoare necunoscută → 422. Numai managerul/adminul editează catalogul.
- **Validare la creare** (422, mesaj explicit cu codul serviciului):
  - T04/T07: `POST /api/performed-procedures`;
  - T08: crearea planului cu itemi, `POST /api/treatment-plans/{plan}/stages/{stage}/items`;
  - T08: `PATCH /api/treatment-plans/items/{id}` **numai dacă cererea schimbă `tooth_fdi`/`tooth_surface`**;
    editarea altor câmpuri ale unui item existent nu revalidează localizarea.
  - Mesaje: `Service {code} is recorded without a tooth; remove the tooth and surfaces`,
    `Service {code} requires an FDI tooth`, `Service {code} requires an FDI tooth and at least one surface`.
  - Crearea unui plan cu un item nepermis este refuzată integral (nu rămâne plan parțial).
- **Fără efect retroactiv.** Schimbarea regulii unui serviciu nu modifică și nu invalidează manoperele sau itemii deja
  înregistrați: pot fi citiți, porniți, finalizați, asociați, iar itemii pot fi editați fără schimbarea localizării.
- **UI.** Formularul de catalog are câmpul „Localizări permise” (implicit „Oricare”) și avertizează la editare că
  regula nouă se aplică doar înregistrărilor viitoare. În T07 și T08:
  - un serviciu `no_tooth` golește și blochează dintele/suprafețele;
  - cu un dinte deja ales (traseul „dinte → manoperă” sau dinte ales înainte), serviciile `no_tooth` nu sunt oferite;
  - pentru `tooth`/`tooth_surface`, opțiunea „fără dinte” este dezactivată, iar salvarea rămâne blocată cu mesaj
    până la alegerea dintelui și, la `tooth_surface`, a cel puțin unei suprafețe;
  - un serviciu fără câmpul `allowed_location` în răspuns este tratat ca `any`.
  Serverul rămâne autoritar: UI-ul doar evită oferirea localizărilor nepermise.

## Teste

- `backend/tests/test_t03_allowed_locations.py` — catalog, matricea regulă × localizare pentru T04/T07 și T08,
  mesaje, lipsa efectului retroactiv.
- `backend/tests/test_postgres_t03_locations.py` — PostgreSQL: bază populată la t13, upgrade la head-ul configurat,
  date păstrate, `any` pe rândurile existente, CHECK/NOT NULL, comportamentul API după migrare.
- `frontend/src/catalog/locations.test.tsx` — formularul de catalog, T07 pe ambele trasee, T08.
