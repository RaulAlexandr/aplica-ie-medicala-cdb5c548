Am reunit cerințele din „Optimizează lista aplicației”, „Randare odontogramă aplicație”, „Sugestii odontogramă aplicație”, contextul din „project 1” și stadiul verificat în GitHub 
Planul de mai jos acoperă viziunea completă discutată, nu doar primul MVP. Unele componente — sterilizare, integrare cu alte softuri și AI pentru imagistică — trebuie confirmate separat înaintea implementării.


Etapa A — aplicația de bază utilizabilă 

| Task | Ce îi cerem | Când îl considerăm terminat |
|---|---|---|
| **T00 — Fundația** | Autentificare, clinici, pacienți, programări, integritate și CI | **Realizat**, cu limitarea browser documentată |
| **T01 — Configurarea clinicii** | Setări clinică și fus orar, cabinete, invitarea/activarea/dezactivarea personalului, roluri | Managerul configurează o clinică nouă din interfață, fără SQL sau apeluri manuale |
| **T02 — Agenda operațională** | Zi pe cabinete, săptămână pe zile, filtre, detalii, tip și note, asistent, schimbare status, reprogramare, program de lucru și indisponibilități | Recepția gestionează o zi de lucru; toate modificările respectă conflictele și permisiunile |
| **T03 — Catalogul manoperelor și tarifelor** | Denumire, categorie, durată orientativă, preț, monedă, localizări permise, activare/arhivare | Clinica își configurează serviciile; schimbarea tarifului nu modifică retrospectiv serviciile deja înregistrate |

T01 rezolvă o dependență practică: pentru a testa corect rolurile, medicii și programările, trebuie să poți crea echipa și cabinetele fără ajutor tehnic. Este motivul pentru care îl pun înaintea catalogului propus anterior.


Etapa B — fluxul clinic central

| Task | Ce îi cerem | Când îl considerăm terminat |
|---|---|---|
| **T04 — Vizita și manoperele efectuate** | „Începe programarea”, manopere multiple, pornire/finalizare separată, pauză/reluare, tarif și localizare, finalizarea vizitei | Medicul finalizează o manoperă, discută cu pacientul și închide vizita mai târziu; timpul manoperei rămâne corect |
| **T05 — Dosarul pacientului și cronologia** | Toate câmpurile necesare în UI, revizii vizibile, vizite, manopere, note/constatări, alerte, ciorne și corecții cu istoric | Din dosar se poate reconstrui cine a făcut ce și când, fără suprascrierea tăcută a istoricului |
| **T06 — Odontograma clinică 2D** | Dinți FDI, suprafețe, stări și constatări multiple, dinți lipsă, panou individual, perspective opționale și istoric | În timpul unei obturații pe 1.6 se poate înregistra caria de pe 1.7 fără tarif sau cronometru nou |
| **T07 — Cele două intrări în aceeași manoperă** | „Manoperă → dinte” și „Dinte → manoperă”, formular mare precompletat, aceleași reguli și înregistrări | Ambele trasee creează același tip de înregistrare, fără dubluri sau cronometre separate accidental |
| **T08 — Planurile de tratament** | Etape, manopere, dinți/suprafețe, estimări, priorități, statusuri, acceptare și legături cu programările și tratamentele efectuate | Un plan cu mai multe etape poate fi urmărit până la finalizare; planificarea nu este confundată cu efectuarea |
| **T09 — Parodontologia: introducerea datelor** | Șase situri/dinte, adâncime, recesiune, BOP, placă, supurație; mobilitate și furcație separat, per dinte | Medicul completează rapid o evaluare; siturile invalide și duplicatele sunt respinse |
| **T10 — Parodontologia: evoluția** | Evaluare nouă, istoric, comparație între două sesiuni, indicatori calculați pe datele efectiv măsurate | Evaluările vechi rămân intacte; se văd diferențele fără a trata măsurătorile lipsă ca valori zero |

Dependențe: T04 folosește T02–T03; T06 folosește dosarul și istoricul din T05; T07 leagă T04 de T06; T08 folosește catalogul și manoperele; T10 urmează după T09.
Regulile stabilite cu Serena trebuie păstrate pe tot parcursul:
- Deschiderea fișei sau selectarea unui dinte nu pornește timpul.
- Constatarea, tratamentul planificat și manopera efectuată sunt înregistrări distincte.
- Finalizarea manoperei nu închide programarea.
- O durată introdusă ulterior este marcată ca atare; nu apare ca măsurare reală.
- Un medic nu are două manopere cronometrate simultan.
- Odontograma rămâne 2D și simplă; detaliile apar la nevoie.
- Evaluările clinice finalizate se corectează cu istoric, nu prin înlocuirea datelor vechi.


Etapa C — documente, pacient și partea financiară

| Task | Ce îi cerem | Când îl considerăm terminat |
|---|---|---|
| **T11 — Documente și imagini** | Încărcare protejată, asociere cu pacient/vizită/dinte, acces controlat, fotografii și radiografii uzuale, zoom și comparație | Fișierele sunt accesibile doar persoanelor autorizate și apar în contextul clinic corect |
| **T12 — Formulare configurabile și anamneză** | Întrebări și secțiuni configurabile, condiții, versiuni, link temporar de completare, identificarea dublurilor, verificare de către personal | Răspunsurile intră în fișă cu sursa și starea de validare; schimbarea formularului păstrează răspunsurile istorice |
| **T13 — Sume datorate și încasări** | Servicii efectuate, avansuri, încasări, sold, ajustări și rambursări cu istoric | Totalul serviciilor este separat de banii încasați; anulările/corecțiile sunt trasabile |
| **T14 — Portalul pacientului** | Autentificare separată, identitate clinică, programări, confirmare/solicitare reprogramare, formulare, planuri și documente publicate, reprezentant pentru minor | Pacientul vede exclusiv informațiile publicate pentru el, fără note interne sau date din altă clinică |
| **T15 — Comunicarea cu pacientul** | Integrare inițială cu un canal ales, șabloane, preferințe, istoric și stare de livrare; apoi alte canale | Confirmările și reminderele ajung corect, fără duplicate; anularea/reprogramarea oprește mesajele devenite inutile |
| **T16 — Lista de așteptare și rechemări** | Disponibilitate pacient, potrivire după durată/medic/resurse, ofertarea locului, rezervare temporară și confirmare unică | Un interval eliberat poate fi ocupat de un singur pacient, chiar dacă mai mulți încearcă simultan |

Pentru T15 alegem furnizorul și canalul când ajungem acolo. Nu trebuie cumpărate sau configurate acum servicii pentru toate canalele. Facturarea fiscală și integrarea contabilă se definesc separat de evidența internă a sumelor.


Etapa D — organizarea clinicii

| Task | Ce îi cerem | Când îl considerăm terminat |
|---|---|---|
| **T17 — Stocuri, loturi și mișcări** | Recepții, consum, transfer, ajustări, locații, loturi, expirări și praguri minime | Cantitățile și istoricul rămân corecte inclusiv la operațiuni simultane; corecțiile nu șterg mișcările |
| **T18 — Materiale pentru proceduri** | Rețete orientative de consum, rezervări, necesar pentru programări, consum confirmat și reaprovizionare | Estimările sunt separate de consumul real; sistemul arată lipsurile pentru intervențiile viitoare |
| **T19 — Echipa, turele și prezența** | Profiluri, documente administrative protejate, disponibilitate, concedii, repartizare medic/asistent pe cabinet, clock-in/out | Se știe cine lucrează unde și când; prezența nu este confundată cu durata tratamentelor |
| **T20 — Chat intern și sarcini** | Mesaje directe/grup, canale, mesaje către cabinetul ocupat în acel moment, necitite, responsabil/termen/status, predare de tură | Mesajul către Cabinetul 3 ajunge echipei repartizate acolo; sarcinile sunt urmărite până la rezolvare |
| **T21 — Echipamente și mentenanță** | Registru, documente, service, verificări, indisponibilitate și dependențe ale programărilor | Un echipament indisponibil semnalează programările afectate și responsabilul |
| **T22 — Cabinet Ready** | Checklisturi pe procedură, materiale/instrumente/documente, particularități aprobate, pregătit/în pregătire/blocat, timpi de pregătire | Echipa vede exact ce lipsește; pregătirea fizică este confirmată de personal, nu dedusă doar din inventar |
| **T23 — Sterilizare, dacă intră în produs** | Definirea și implementarea trasabilității instrumentelor și ciclurilor, cu validarea fluxului de către clinică | Există un flux distinct și verificabil; o bifă de checklist nu este prezentată drept trasabilitate completă |

T18 depinde de catalog, programări și T17. T20 folosește repartizările din T19. T22 leagă procedurile, materialele, echipa și echipamentele.


Etapa E — rapoarte, automatizări și AI

| Task | Ce îi cerem | Când îl considerăm terminat |
|---|---|---|
| **T24 — Rapoarte operaționale și financiare** | Ocupare, anulări, neprezentări, durate estimate/reale, tratamente necontinuate, consumuri, servicii și încasări | Fiecare indicator arată perioada, definiția și înregistrările din care rezultă |
| **T25 — Motor de automatizări** | Declanșator, condiții, acțiuni, aprobări, jurnal, oprire, reluare după erori, prevenirea duplicatelor și simulare | O regulă poate fi previzualizată, activată și oprită; nu repetă accidental o acțiune |
| **T26 — Feedback și campanii** | Feedback după vizite, sarcini pentru probleme, invitații la recenzii, campanii către destinatari eligibili | Preferințele sunt respectate; recenziile publice nu sunt condiționate de feedback pozitiv |
| **T27 — AI pentru introducerea informației** | Dictare/text structurat și extragere din documente, cu sursă și confirmare | Medicul verifică propunerea înainte ca aceasta să devină informație clinică validată |
| **T28 — Asistentul managerului** | Întrebări despre datele clinicii, explicații, perioade și surse; acțiuni doar prin funcțiile și permisiunile existente | Răspunsurile pot fi verificate în date; AI-ul nu inventează rezultate și nu accesează alte clinici |
| **T29 — Reguli configurate prin conversație** | Transformarea cererii în regulă structurată, previzualizare, aprobare și dezactivare | „Verifică materialele pentru șapte zile” produce o configurație inspectabilă în motorul T25 |
| **T30 — Estimări și optimizare validate** | Estimarea duratelor, întârzierilor și necesarului, folosind date suficiente și evaluare măsurabilă | Predicțiile au limite explicite; sugestiile respectă disponibilitatea tuturor resurselor |

AI-ul este parte din viziunea finală, dar nu trebuie introdus înaintea datelor și funcțiilor pe care le utilizează. Prima analiză operațională poate funcționa cu reguli și statistici, fără predicții prezentate artificial ca certe.


Etapa F — extensii și livrare

| Task | Ce îi cerem | Când îl considerăm terminat |
|---|---|---|
| **T31 — Imagistică avansată** | Vizualizare DICOM/STL, după definirea formatelor și cazurilor reale de utilizare | Fișierele de referință sunt vizualizate corect și asociate pacientului; analiza AI a imaginilor rămâne proiect separat |
| **T32 — Mai multe sedii și integrări externe** | Acces pe sedii, configurări și rapoarte; import/export și conectori pentru sisteme nominalizate | Datele și permisiunile sunt păstrate; importurile repetate nu creează dubluri |
| **T33 — Mediu de test și acceptanță clinică** | Mediu accesibil cu date fictive, scenarii browser reproductibile, testare de către Serena și echipă | Fluxurile pot fi demonstrate și evaluate fără pregătire manuală ascunsă |
| **T34 — Pregătirea pentru utilizare reală** | Backup și restaurare testată, monitorizare, erori, securitate, administrarea accesului, export/retenție și proceduri operaționale | Există dovezi pentru recuperare și operare, plus validarea cerințelor aplicabile înainte de date reale |
| **T35 — Pilot controlat și lansare** | Pilot cu scop și participanți definiți, suport, criterii de oprire/revenire, remedierea problemelor și acceptanță finală | Clinica acceptă fluxurile incluse, iar problemele critice sunt închise |

T33 trebuie început mai devreme, după primele taskuri, și extins continuu. La fel, securitatea și istoricul sunt cerințe ale fiecărui task; T34 verifică pregătirea întregului sistem, nu începe securitatea de la zero.


Cum aș grupa livrările
- Prima demonstrație utilizabilă: T01–T04 — configurezi clinica, programezi pacientul și înregistrezi manopere.
- Nucleul clinic: T05–T10 — dosar, odontogramă, planuri și paro.
- Relația cu pacientul: T11–T16 — documente, formulare, bani, portal și comunicare.
- Operațiunile clinicii: T17–T23 — materiale, echipă, echipamente și pregătire.
- Analiză și automatizare: T24–T30.
- Extensii și lansare: T31–T35, cu testarea și pregătirea operațională desfășurate progresiv.

Acestea sunt unități de livrare, nu estimări de durată. Un task mare poate necesita două PR-uri, dar trebuie păstrat un rezultat de acceptanță clar.