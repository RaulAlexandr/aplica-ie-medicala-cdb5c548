# DentaCare: matricea T00–T35

Actualizat: 2026-10-31. Numerotarea și titlurile provin din [planul original](sources/PlanTotal.md), păstrat nemodificat. Aceasta este o stare documentară pentru audit, nu o certificare nouă a fiecărei cerințe.

[Stare curentă](CURRENT-STATUS.md) · [Restanțe](BACKLOG.md) · [Audit Astra](ASTRA-AUDIT.md).

| Task | Titlu original | Stare cunoscută | Dovadă/sursă | Ce trebuie verificat |
|---|---|---|---|---|
| T00 | Fundația | Fundație în main | PR #1 merged; candidat ulterior cu CI verde | Audit complet de securitate/corectitudine încă de făcut |
| T01 | Configurarea clinicii | Configurare în main | PR #2 merged, main 2b9f543 | Matrice roluri și acceptanță clinică nu rezultă doar din merge |
| T02 | Agenda operațională | Implementat în candidat, nu main | PR #3 adcdc820; integrare 430ebee; UI ede0446 | Audit calendar/conflicte/DST și acoperirea cerințelor |
| T03 | Catalogul manoperelor și tarifelor | Catalog în candidat, nu main | PR #4 draft 415915f; inclus în 430ebee/ede0446 | Localizări permise și acoperire HTTP/roluri/migrări de verificat |
| T04 | Vizita și manoperele efectuate | Backend și UI parțial acceptate | PR #5/#6; CI și demo Manus pe ede0446 | Segmente, durată manuală, audit overrides și probe rămase |
| T05 | Dosarul pacientului și cronologia | Dosar/note în UI; produs incomplet | Demo note/revizie persistate; S07 parțial, S08/S09 NOT RUN | Timeline automat, addendum, finding și acces/istoric de verificat |
| T06 | Odontograma clinică 2D | UI/demo separat, fără API clinic | Branch 003b617, 22 teste/build istorice; editări locale separate | Browser, integrare T05, persistență și audit real |
| T07 | Cele două intrări în aceeași manoperă | Planificat, fără livrare acceptată identificată | Plan original; inventar de confirmat de Astra | Astra verifică lipsa/fragmentele; nu implementează în audit |
| T08 | Planurile de tratament | Planificat, fără livrare acceptată identificată | Plan original; inventar de confirmat de Astra | Astra verifică lipsa/fragmentele; nu implementează în audit |
| T09 | Parodontologia: introducerea datelor | Planificat, fără livrare acceptată identificată | Plan original; inventar de confirmat de Astra | Astra verifică lipsa/fragmentele; nu implementează în audit |
| T10 | Parodontologia: evoluția | Planificat, fără livrare acceptată identificată | Plan original; inventar de confirmat de Astra | Astra verifică lipsa/fragmentele; nu implementează în audit |
| T11 | Documente și imagini | Planificat, fără livrare acceptată identificată | Plan original; inventar de confirmat de Astra | Astra verifică lipsa/fragmentele; nu implementează în audit |
| T12 | Formulare configurabile și anamneză | Planificat, fără livrare acceptată identificată | Plan original; inventar de confirmat de Astra | Astra verifică lipsa/fragmentele; nu implementează în audit |
| T13 | Sume datorate și încasări | Planificat, fără livrare acceptată identificată | Plan original; inventar de confirmat de Astra | Astra verifică lipsa/fragmentele; nu implementează în audit |
| T14 | Portalul pacientului | Planificat, fără livrare acceptată identificată | Plan original; inventar de confirmat de Astra | Astra verifică lipsa/fragmentele; nu implementează în audit |
| T15 | Comunicarea cu pacientul | Planificat, fără livrare acceptată identificată | Plan original; inventar de confirmat de Astra | Astra verifică lipsa/fragmentele; nu implementează în audit |
| T16 | Lista de așteptare și rechemări | Planificat, fără livrare acceptată identificată | Plan original; inventar de confirmat de Astra | Astra verifică lipsa/fragmentele; nu implementează în audit |
| T17 | Stocuri, loturi și mișcări | Planificat, fără livrare acceptată identificată | Plan original; inventar de confirmat de Astra | Astra verifică lipsa/fragmentele; nu implementează în audit |
| T18 | Materiale pentru proceduri | Planificat, fără livrare acceptată identificată | Plan original; inventar de confirmat de Astra | Astra verifică lipsa/fragmentele; nu implementează în audit |
| T19 | Echipa, turele și prezența | Planificat, fără livrare acceptată identificată | Plan original; inventar de confirmat de Astra | Astra verifică lipsa/fragmentele; nu implementează în audit |
| T20 | Chat intern și sarcini | Planificat, fără livrare acceptată identificată | Plan original; inventar de confirmat de Astra | Astra verifică lipsa/fragmentele; nu implementează în audit |
| T21 | Echipamente și mentenanță | Planificat, fără livrare acceptată identificată | Plan original; inventar de confirmat de Astra | Astra verifică lipsa/fragmentele; nu implementează în audit |
| T22 | Cabinet Ready | Planificat, fără livrare acceptată identificată | Plan original; inventar de confirmat de Astra | Astra verifică lipsa/fragmentele; nu implementează în audit |
| T23 | Sterilizare, dacă intră în produs | Planificat, fără livrare acceptată identificată | Plan original; inventar de confirmat de Astra | Astra verifică lipsa/fragmentele; nu implementează în audit |
| T24 | Rapoarte operaționale și financiare | Planificat, fără livrare acceptată identificată | Plan original; inventar de confirmat de Astra | Astra verifică lipsa/fragmentele; nu implementează în audit |
| T25 | Motor de automatizări | Planificat, fără livrare acceptată identificată | Plan original; inventar de confirmat de Astra | Astra verifică lipsa/fragmentele; nu implementează în audit |
| T26 | Feedback și campanii | Planificat, fără livrare acceptată identificată | Plan original; inventar de confirmat de Astra | Astra verifică lipsa/fragmentele; nu implementează în audit |
| T27 | AI pentru introducerea informației | Planificat, fără livrare acceptată identificată | Plan original; inventar de confirmat de Astra | Astra verifică lipsa/fragmentele; nu implementează în audit |
| T28 | Asistentul managerului | Planificat, fără livrare acceptată identificată | Plan original; inventar de confirmat de Astra | Astra verifică lipsa/fragmentele; nu implementează în audit |
| T29 | Reguli configurate prin conversație | Planificat, fără livrare acceptată identificată | Plan original; inventar de confirmat de Astra | Astra verifică lipsa/fragmentele; nu implementează în audit |
| T30 | Estimări și optimizare validate | Planificat, fără livrare acceptată identificată | Plan original; inventar de confirmat de Astra | Astra verifică lipsa/fragmentele; nu implementează în audit |
| T31 | Imagistică avansată | Planificat, fără livrare acceptată identificată | Plan original; inventar de confirmat de Astra | Astra verifică lipsa/fragmentele; nu implementează în audit |
| T32 | Mai multe sedii și integrări externe | Planificat, fără livrare acceptată identificată | Plan original; inventar de confirmat de Astra | Astra verifică lipsa/fragmentele; nu implementează în audit |
| T33 | Mediu de test și acceptanță clinică | Început prin CI/probe tehnice | CI PostgreSQL și demo browser Manus pe SQLite | Nu există acceptanță clinică/staging complet confirmat |
| T34 | Pregătirea pentru utilizare reală | Măsuri transversale parțiale | Auth, RBAC, migrare și integritate existente | Backup/restaurare/monitorizare/operare și evaluare completă |
| T35 | Pilot controlat și lansare | Planificat, fără livrare acceptată identificată | Plan original | Pilot/lansare neacceptate |

Un tip de notă treatment_plan nu reprezintă modulul T08. Un demo T06 nu demonstrează persistență/audit. T07 nu este condiție implicită pentru finalizarea T06. Securitatea și istoricul se aplică fiecărui task, chiar dacă T34 are acceptanță operațională separată.
