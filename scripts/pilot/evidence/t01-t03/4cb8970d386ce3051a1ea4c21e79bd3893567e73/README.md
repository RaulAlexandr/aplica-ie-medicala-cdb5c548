# Probe T01/T03 gaps — produs 4cb8970d386ce3051a1ea4c21e79bd3893567e73

Runtime curat (start.sh fără --allow-dirty) pornit la acd2d0270c24b179e5e01f6b4bc79e62783c009e; commiturile ulterioare
până la 4cb8970d386ce3051a1ea4c21e79bd3893567e73 schimbă numai scripts/pilot/t01-t03-acceptance.mjs (codul aplicației este identic).
API 127.0.0.1:8041, web 127.0.0.1:5181, SQLite fictivă nouă sub ~/.local/share/dentacare-pilot-t01t03 (migrare la head
t03_catalog_allowed_locations), Chromium 151.0.7922.34, 2026-11-20 ~15:27–15:33 EET. Date fictive, fără credențiale.

| Script | Rezultat | Page errors | Console / HTTP ≥400 |
|---|---|---|---|
| t01-t03-acceptance.mjs | 37/37 | 0 | favicon 404 (cosmetic); 1× POST /auth/login 401 pentru membrul dezactivat (așteptat) |
| browser-acceptance.mjs (regresie nucleu) | 60/60 | 0 | — (409 F06 așteptat, înregistrat în checks) |
| t13-browser-acceptance.mjs, rularea 1 | 31/32 FAIL | 0 | cursă în script: citește „Ziua raportului” înainte de încărcarea fusului clinicii; captura arăta ziua corectă |
| t13-browser-acceptance.mjs, rularea 2 | 35/35 | 0 | favicon 404; 2× GET pacient 404 pentru altă clinică (izolare, așteptat) |

Rulări anterioare ale t01-t03-acceptance.mjs pe același runtime au eșuat din motive de script, nu de aplicație:
aserțiune compusă pe `<option>.isDisabled()` (corectată prin citirea `disabled` din DOM) și coliziune de programare
la reluare (spațiu de sloturi lărgit). Capturile: staff reactivat cu istoric; T07 vizită blocat fără dinte;
plan T08 blocat fără dinte; catalog cu localizări.

Commitul care adaugă aceste probe modifică numai acest director.
