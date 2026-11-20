# Probe T13 — produs 7cbbde7db0e4fd7437cd40f451e5bffa68a29de9

Runtime curat (start.sh fără --allow-dirty), SHA 7cbbde7db0e4fd7437cd40f451e5bffa68a29de9, API 127.0.0.1:8035, web 127.0.0.1:5195, SQLite fictivă nouă,
Chromium 151.0.7922.34, browser în UTC, clinică Europe/Bucharest, 2026-11-19 ~18:20 UTC. Date fictive, fără credențiale.

| Script | Rezultat | Page errors | Console / HTTP ≥400 |
|---|---|---|---|
| browser-acceptance.mjs (regresie nucleu) | 60/60 | 0 | favicon 404 (cosmetic); F06 409 (așteptat) |
| t13-browser-acceptance.mjs | 35/35 | 0 | favicon 404; 2× GET pacient 404 pentru altă clinică (izolare, așteptat) |

Commitul care adaugă aceste probe modifică numai acest director.
