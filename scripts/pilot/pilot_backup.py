"""Pilot backup/restore of SQLite + T11 document files with a SHA-256 manifest.

backup  DB DOCUMENTS_DIR DEST_DIR  -> DEST_DIR/{fictional-clinic.sqlite3, documents/, MANIFEST.json}
restore BACKUP DEST_DIR            -> verified isolated copy; BACKUP may be a legacy single .sqlite3

Both directions check SQLite integrity and that every clinical_documents row has a file whose
SHA-256 matches the recorded value. Contains no credentials; files keep 0600/0700 permissions.
"""

import hashlib
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

DB_NAME = "fictional-clinic.sqlite3"
DOCS_NAME = "documents"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_db(src: Path, dest: Path) -> None:
    with sqlite3.connect(src) as source, sqlite3.connect(dest) as target:
        source.backup(target)
        result = target.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise SystemExit(f"integrity_check: {result}")
    os.chmod(dest, 0o600)


def copy_documents(src: Path, dest: Path) -> list[Path]:
    dest.mkdir(mode=0o700)
    copied = []
    if src.is_dir():
        for path in sorted(src.rglob("*")):
            rel = path.relative_to(src)
            if rel.parts and rel.parts[0] == ".incoming":
                continue  # temporary partial uploads are never part of a backup
            target = dest / rel
            if path.is_dir():
                target.mkdir(mode=0o700, exist_ok=True)
            elif path.is_file():
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                shutil.copy2(path, target)
                os.chmod(target, 0o600)
                copied.append(rel)
    return copied


def verify_documents(db: Path, docs: Path) -> tuple[int, int]:
    """Return (rows, orphan_files). Raises on a missing or modified file."""
    with sqlite3.connect(db) as conn:
        has_table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='clinical_documents'").fetchone()
        rows = conn.execute("SELECT clinic_id, storage_key, sha256 FROM clinical_documents").fetchall() if has_table else []
    expected = set()
    for clinic_id, key, digest in rows:
        path = docs / str(clinic_id) / key[:2] / key
        expected.add(path.relative_to(docs))
        if not path.is_file():
            raise SystemExit(f"Fișier lipsă pentru documentul cu cheia {key}")
        if sha256(path) != digest:
            raise SystemExit(f"Fișier modificat pentru documentul cu cheia {key}")
    present = {p.relative_to(docs) for p in docs.rglob("*") if p.is_file()} if docs.is_dir() else set()
    return len(rows), len(present - expected)


def backup(db: Path, docs: Path, dest: Path) -> None:
    dest.mkdir(mode=0o700)
    copy_db(db, dest / DB_NAME)
    files = copy_documents(docs, dest / DOCS_NAME)
    rows, orphans = verify_documents(dest / DB_NAME, dest / DOCS_NAME)
    manifest = {
        "format": "dentacare-pilot-backup/1",
        "db": {"name": DB_NAME, "sha256": sha256(dest / DB_NAME)},
        "documents": {str(rel): sha256(dest / DOCS_NAME / rel) for rel in files},
        "document_rows": rows,
        "orphan_files": orphans,
    }
    (dest / "MANIFEST.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
    os.chmod(dest / "MANIFEST.json", 0o600)
    print(f"Backup valid: DB ok, {rows} documente T11 verificate, {len(files)} fișiere, {orphans} fișiere fără rând.")


def restore(src: Path, dest: Path) -> None:
    if src.is_file():
        dest.mkdir(mode=0o700)
        copy_db(src, dest / DB_NAME)
        (dest / DOCS_NAME).mkdir(mode=0o700)
        print("ATENȚIE: backup vechi numai DB; nu conține fișiere T11. Documentele din bază nu vor avea conținut.")
        return
    manifest = json.loads((src / "MANIFEST.json").read_text())
    if sha256(src / DB_NAME) != manifest["db"]["sha256"]:
        raise SystemExit("Manifest: baza din backup a fost modificată")
    for rel, digest in manifest["documents"].items():
        if sha256(src / DOCS_NAME / rel) != digest:
            raise SystemExit(f"Manifest: fișier modificat {rel}")
    dest.mkdir(mode=0o700)
    copy_db(src / DB_NAME, dest / DB_NAME)
    files = copy_documents(src / DOCS_NAME, dest / DOCS_NAME)
    rows, orphans = verify_documents(dest / DB_NAME, dest / DOCS_NAME)
    if len(files) != len(manifest["documents"]):
        raise SystemExit("Numărul de fișiere restaurate diferă de manifest")
    print(f"Restore valid: DB ok, {rows} documente T11 cu fișier și SHA-256 corecte, {orphans} fișiere fără rând.")


if __name__ == "__main__":
    command, *args = sys.argv[1:]
    if command == "backup" and len(args) == 3:
        backup(Path(args[0]), Path(args[1]), Path(args[2]))
    elif command == "restore" and len(args) == 2:
        restore(Path(args[0]), Path(args[1]))
    else:
        raise SystemExit("usage: pilot_backup.py backup DB DOCS DEST | restore BACKUP DEST")
