# Probe consolidare pilot — produs 158dc4d4d5b0eaa00de43150c4a5263ae56877b0

Runtime curat (`start.sh` fără `--allow-dirty`), SHA 158dc4d4d5b0eaa00de43150c4a5263ae56877b0, API 127.0.0.1:8033, web 127.0.0.1:5193,
SQLite fictivă nouă, Chromium 151.0.7922.34 (Playwright local), 2026-11-19 ~17:56 UTC. Date exclusiv fictive.
Fără credențiale, tokenuri sau cookie-uri în fișiere.

| Script | Rezultat | Page errors | Console/HTTP ≥400 (clasificare) |
|---|---|---|---|
| browser-acceptance.mjs (nucleu/T05–T07/F06) | 60/60 | 0 | favicon 404 (cosmetic); F06 409 așteptat |
| t08-browser-acceptance.mjs | 26/26 | 0 | favicon 404; 503 simulat (eroare intenționată) |
| periodontal-acceptance.mjs | 26/26 | 0 | favicon 404; 422 finalizare goală (intenționat) |
| t11-browser-acceptance.mjs | 30/30 | 0 | favicon 404; 503 simulat; 415 imagine defectă (intenționat) |
| consolidated-acceptance.mjs | 40/40 | 0 | favicon 404; 2× GET pacient 404 pentru a doua clinică (izolare, așteptat) |

Backup DB + documente cu runtime oprit (`backup-restore.txt`), restaurare într-o copie separată și
verificare read-only a pacientului, vizitei, manoperei, constatării, planului, celor 2 evaluări finalizate
și a documentului (SHA-256 fișier = rând DB = fixture încărcat): `restore-verification.json`.
Commitul care adaugă aceste probe modifică numai acest director.
