# Medii și execuție pentru verificare

Configurația CI verificată este Python 3.12, Node 22, PostgreSQL 16. React/TypeScript/Vite/Vitest în frontend și FastAPI/SQLAlchemy async/Pydantic/Alembic în backend. Verifică efectiv versiunea executabilului folosit.

## Demo izolat SQLite

Folosește numai o bază nouă, cu date fictive. Din rădăcina checkout-ului propriu:

```bash
cd backend
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
export DATABASE_URL=sqlite+aiosqlite:////tmp/dentacare-astra-demo.db
export ENVIRONMENT=development
export JWT_SECRET=local-demo-only-not-for-production
export CORS_ORIGINS=http://127.0.0.1:5178
.venv/bin/python - <<'PY'
import asyncio
from app.database import Base, engine
async def initialize():
    async with engine.begin() as connection:
        await connection.run_sync(lambda connection: Base.metadata.create_all(connection, checkfirst=True))
    await engine.dispose()
asyncio.run(initialize())
PY
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8010
```

Păstrează o bază preexistentă; dacă nu aparține probei, alege alt nume liber. create_all nu migrează coloanele existente și nu dovedește upgrade PostgreSQL.

În alt terminal, din frontend-ul aceluiași checkout:

```bash
npm ci
VITE_API_URL=http://127.0.0.1:8010/api npm run dev -- --host 127.0.0.1 --port 5178 --strictPort
```

Verifică health HTTP 200 și aplicația în browser. Dacă portul este folosit, alege alte porturi pentru propriile servicii și aliniază URL/CORS; nu opri serviciile altei sesiuni. În cloud, localhost nu este laptopul utilizatorului; configurează URL-urile forwarded și originea exactă vizibilă browserului.

## PostgreSQL și verificări

Folosește PostgreSQL de test izolat, disponibil nativ sau în CI; Docker nu este o cerință implicită. Migrațiile se aplică numai bazei de test care aparține acestei probe. Nu modifica instanțe/date existente fără scope explicit.

Comenzile exacte de test sunt în workflow. Selecția curentă non-PostgreSQL ignoră separat cele două fișiere PostgreSQL istorice, iar jobul postgres selectează explicit trei fișiere. Inspectează selecția înainte de a declara un test executat.

Sandboxurile Codex au refuzat loopback și au blocat async SQLite în sesiuni anterioare. Manus cloud a reușit inițializarea și traseul browser. O eroare EPERM/DNS este o limită a mediului respectiv, nu diagnostic global al laptopului.

Pentru audit consemnează comanda, versiunea, cwd, SHA și rezultatul. Testele/mock-urile, compilarea DDL, create_all, migrarea și browserul sunt probe distincte. Fără audit fix automat, modificări de manifest/lockfile ori scăderea protecțiilor pentru a obține verde.
