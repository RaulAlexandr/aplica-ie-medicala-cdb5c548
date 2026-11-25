"""PostgreSQL backup/restore round-trip used by CI."""
import json
import os
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

from sqlalchemy.engine import make_url

from app.operations.backup import _pg_env, create_backup, restore_backup


def main() -> None:
    url = os.environ["DATABASE_URL"]
    pg_env, source_db = _pg_env(url)
    marker = str(uuid4())
    subprocess.run(["psql", "-X", "-v", "ON_ERROR_STOP=1", "--dbname", source_db,
                    "-c", "INSERT INTO clinics (id, name, timezone, created_at, updated_at) VALUES ('" + marker + "', 'CI backup fictiv', 'Europe/Bucharest', now(), now())"], env=pg_env, check=True)
    target_db = "dentacare_restore_" + marker.replace("-", "")[:12]
    target_url = make_url(url).set(database=target_db)
    subprocess.run(["createdb", "--maintenance-db", source_db, target_db], env=pg_env, check=True)
    try:
        with tempfile.TemporaryDirectory(prefix="dentacare-ci-roundtrip-") as temp:
            root = Path(temp)
            docs = root / "documents"
            docs.mkdir()
            (docs / "sample.txt").write_text("document fictiv pentru testul de backup\n")
            archive = root / "backup.tar.gz"
            create_backup(archive, docs, url, confirm_quiescent=True)
            restored_docs = root / "restored-documents"
            result = restore_backup(archive, target_url.render_as_string(hide_password=False), restored_docs)
            if result["table_counts"]["clinics"] < 1 or (restored_docs / "sample.txt").read_text() != (docs / "sample.txt").read_text():
                raise RuntimeError("Backup round-trip comparison failed")
            print(json.dumps({"status": "passed", "target_database": target_db,
                              "clinic_rows": result["table_counts"]["clinics"], "documents": "hashes verified"}))
    finally:
        subprocess.run(["dropdb", "--maintenance-db", source_db, "--if-exists", target_db], env=pg_env, check=True)
        subprocess.run(["psql", "-X", "-v", "ON_ERROR_STOP=1", "--dbname", source_db,
                        "-c", "DELETE FROM clinics WHERE id = '" + marker + "'"], env=pg_env, check=True)


if __name__ == "__main__":
    main()
