# T24 v1 — rapoarte operaționale și financiare

API-ul raportează datele unei singure clinici pentru o perioadă închisă de zile
calendaristice locale: `[from, to]`, inclusiv, în fusul configurat al clinicii.
Parametrii sunt date ISO `YYYY-MM-DD`; `to` nu poate preceda `from`, iar
perioada nu poate depăși 366 de zile. Filtrele opționale `doctor_id` și
`room_id` restrâng înregistrările la medic/cabinet. Orice ID din altă clinică
este răspuns 404. Acces doar pentru `clinic_manager` și `administrator`.

Fiecare răspuns de indicator include cheia, valoarea și unitatea, definiția,
perioada locală, fusul clinicii, filtrele aplicate, sursa și URL-ul relativ
`records` pentru înregistrările care compun rezultatul. Lista este paginată și
expune numai identificator, entitate, dată locală, status, sumă când există și
numele pacientului. Rândurile listate trebuie să corespundă exact numărătorului/sumei
din indicator.

Schema comună a unui record este `id`, `entity`, `date` (ISO cu fusul clinicii),
`status`, `amount` când există și `patient_name` când există. Sunt permise numai
următoarele câmpuri suplimentare de reconciliere, după indicator: `method` și
`reverses_kind` pentru încasări/reversal-uri; `estimated_seconds`,
`measured_seconds` și `manual_duration` pentru manopere; `age_days` pentru itemi
de tratament. Pentru încasări, `amount` este magnitudinea intrării, `status` este
tipul T13 (`payment`, `refund` sau `reversal`), iar `method` al unui reversal
este metoda mișcării anulate. Pentru `financial_reversals`, `reverses_kind`
identifică tipul mișcării țintă. Pentru ocupare, `amount` este numărul de minute
al programării active sau al instanței `WorkingHours` pentru acea zi locală.
Records nu returnează note, alergii, diagnostic sau alte date clinice.

## Indicatori

| Cheie | Valoare și unitate | Definiție, includeri și excluderi | Sursă |
|---|---|---|---|
| `appointments_total` | număr de programări | Programări cu `starts_at` în intervalul UTC rezultat din zilele locale `[from, to]`; include toate statusurile. Filtrele medic/cabinet sunt aplicate direct. | `Appointment` |
| `appointments_cancelled` | număr de programări | Subsetul de mai sus cu status `cancelled`. | `Appointment` |
| `appointments_no_show` | număr de programări | Subsetul de mai sus cu status `no_show`. | `Appointment` |
| `appointments_completed` | număr de programări | Subsetul de mai sus cu status `completed`. | `Appointment` |
| `cancellation_rate` | procent | `cancelled / total * 100`; nedefinit când totalul este zero. Anulările sunt deja incluse în total. | `Appointment` |
| `no_show_rate` | procent | `no_show / total * 100`; nedefinit când totalul este zero. | `Appointment` |
| `room_occupancy` | minute și procent | Minutele programate în programări active, status `scheduled`, `confirmed`, `arrived` sau `in_progress`, împărțite la minutele disponibile din programul clinicii (`WorkingHours`) atribuit cabinetului selectat, în perioada inclusiv. Modelul T02 curent ține programul la nivel de clinică, aplicat cabinetului selectat. Programul se calculează local pentru fiecare dată, apoi se convertește în timp real UTC (ziua schimbării orei poate avea 23/25 h). Nedefinit cu motiv dacă programul relevant lipsește sau nu are minute lucrătoare; niciodată zero/100% în acest caz. Necesită `room_id`; fără cabinet, 422. Records includ programările active și intervalele WorkingHours care formează numărătorul și numitorul. | `Appointment`, `WorkingHours`, `Room` |
| `procedure_estimated_duration` | secunde | Pentru manopere `completed` înregistrate în interval: suma duratelor orientative capturate la creare din snapshotul catalogului. Nu folosește valoarea curentă a catalogului. | `PerformedProcedure` |
| `procedure_measured_duration` | secunde | Pentru aceleași manopere: suma `total_duration_seconds` din segmente efective. Manopere cu durată manuală/override nu intră în măsurate. | `PerformedProcedure`, `ProcedureExecutionSegment` |
| `procedure_manual_duration` | număr de manopere | Numărul manoperelor finalizate care au durată manuală/override; nu se adaugă la durata măsurată. V1 păstrează valoarea manuală distinctă dacă modelul expune sursa; altfel se identifică explicit prin markerul T04 `Manual duration override:` din notes. | `PerformedProcedure` |
| `treatment_items_uncontinued` | număr de itemi și listă de vârste în zile | Itemi din planuri acceptate, activi și neefectuați la capătul perioadei (`created_at` al itemului ≤ sfârșitul perioadei). Vârsta este diferența în zile calendaristice locale dintre data creării și `to`. Exclude itemii efectuați, anulați/arhivați, planurile neacceptate sau inactive. Filtrul medic înseamnă autorul planului; filtrul cabinet include numai itemi legați de programare în acel cabinet. | `TreatmentPlan`, `TreatmentPlanStage`, `TreatmentPlanItem`, `TreatmentPlanAcceptance` |
| `services_performed` | sumă RON | Suma `current_price`, cu fallback la `procedure_base_price` dacă prețul curent lipsește, pentru manopere `completed` în interval. Prețurile sunt snapshoturile/override-urile istorice; nu reprezintă încasări. | `PerformedProcedure` |
| `receipts_net_by_method` | sume RON pe metodă | Netul pentru fiecare metodă este payment `+`, refund `−`, reversal de payment `−`, reversal de refund `+`; anulările/reversal-urile de ajustări nu sunt bani și se exclud. Intrările și reversal-urile sunt datate după `created_at`; o intrare anulată este neutralizată în perioada în care s-a înregistrat reversal-ul, păstrând urmele fiecărui rând. | `PatientFinancialEntry` |
| `financial_reversals` | număr și sumă RON | Mișcări de tip `reversal` din perioadă, grupate după tipul intrării reversate. Suma este pozitivă ca magnitudine; semnul impactului monetar apare în `receipts_net_by_method`. | `PatientFinancialEntry` |
| `consumptions` | indisponibil | Indicatorul nu este calculat în T24 v1; consumurile sunt „indisponibil până la T17”. | T17 — nesursat |

Toate sumele financiare sunt evidență internă, nu raport fiscal. Nu sunt facturi,
bonuri/chitanțe fiscale, TVA sau contabilitate. Semnele și neutralizarea anulărilor
urmează contractul T13 v1.

## Rute

- `GET /api/reports/{indicator}?from=YYYY-MM-DD&to=YYYY-MM-DD[&doctor_id=UUID&room_id=UUID]`
- `GET /api/reports/{indicator}/records?from=...&to=...[&doctor_id=...&room_id=...&limit=...&offset=...]`

Un indicator necunoscut răspunde 404; date/perioadă/combinație de filtre
nevalidă răspunde 422. Valoarea `undefined` include un motiv lizibil și nu se
convertește în zero. Valorile sumabile sunt calculate din aceeași selecție de
înregistrări pe care o returnează ruta `records`.

## Interfață

Pagina de rapoarte permite alegerea perioadei și afișează fiecare card cu
valoarea, unitatea, definiția și un link „Vezi înregistrările”. Consumurile
afișează explicit indisponibilitatea T17. Secțiunea financiară afișează
„Evidență internă, nu raport fiscal”.
