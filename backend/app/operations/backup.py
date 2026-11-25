"""Verified PostgreSQL and document backup bundles."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

from sqlalchemy.engine import make_url


class OperationError(RuntimeError):
    """An unsafe or incompatible operations request."""


def verify_compatibility(archive_heads: list[str], current_heads: list[str]) -> None:
    if not archive_heads or sorted(archive_heads) != sorted(current_heads):
        raise OperationError("Versiunea Alembic din arhivă nu este compatibilă.")


def require_empty_database(object_count: int | str) -> None:
    if str(object_count).strip() != "0":
        raise OperationError("Baza de restaurare nu este goală.")


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_member(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise OperationError("Arhiva conține o cale nesigură.")
    return path


def verify_archive(path: str | Path, extract_dir: str | Path, expected_heads: list[str]) -> dict:
    destination = Path(extract_dir)
    destination.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    try:
        with tarfile.open(path, "r:gz") as archive:
            members = archive.getmembers()
            for member in members:
                rel = _safe_member(member.name)
                if member.name in seen or not member.isfile():
                    raise OperationError("Arhiva conține elemente duplicate sau speciale.")
                seen.add(member.name)
                target = destination.joinpath(*rel.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise OperationError("Arhiva este incompletă.")
                with source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
        manifest_path = destination / "manifest.json"
        checksum_path = destination / "manifest.sha256"
        manifest_bytes = manifest_path.read_bytes()
        if hashlib.sha256(manifest_bytes).hexdigest() != checksum_path.read_text().strip():
            raise OperationError("Manifestul arhivei este corupt.")
        manifest = json.loads(manifest_bytes)
        verify_compatibility(manifest.get("alembic_heads", []), expected_heads)
        files = manifest.get("files")
        if not isinstance(files, dict) or set(files) != seen - {"manifest.json", "manifest.sha256"}:
            raise OperationError("Inventarul fișierelor din manifest nu corespunde.")
        for name, digest in files.items():
            if _sha(destination.joinpath(*_safe_member(name).parts)) != digest:
                raise OperationError("Un fișier din arhivă nu corespunde hash-ului.")
        if "database.dump" not in files:
            raise OperationError("Arhiva nu conține baza de date.")
        return manifest
    except (tarfile.TarError, OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        if isinstance(exc, OperationError):
            raise
        raise OperationError("Arhiva este invalidă sau incompletă.") from exc


def _pg_env(database_url: str) -> tuple[dict[str, str], str]:
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql":
        raise OperationError("Operațiile PostgreSQL necesită o adresă PostgreSQL.")
    env = os.environ.copy()
    for key, value in {"PGHOST": url.host, "PGPORT": str(url.port or 5432), "PGUSER": url.username,
                       "PGPASSWORD": url.password, "PGDATABASE": url.database}.items():
        if value is not None:
            env[key] = unquote(str(value))
    return env, str(url.database)


def _run(args: list[str], env: dict[str, str]) -> str:
    result = subprocess.run(args, env=env, capture_output=True, text=True, check=False)
    if result.returncode:
        raise OperationError(f"Comanda {Path(args[0]).name} a eșuat (cod {result.returncode}).")
    return result.stdout


def _alembic_heads() -> list[str]:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(Config("alembic.ini")).get_heads()


def create_backup(output: str | Path, document_dir: str | Path, database_url: str, *, confirm_quiescent: bool) -> Path:
    if not confirm_quiescent:
        raise OperationError("Oprește scrierile aplicației și confirmă cu --confirm-quiescent.")
    output = Path(output)
    docs = Path(document_dir)
    if output.exists() or not docs.is_dir():
        raise OperationError("Destinația există deja sau directorul de documente lipsește.")
    heads = _alembic_heads()
    env, database = _pg_env(database_url)
    with tempfile.TemporaryDirectory(prefix="dentacare-backup-") as temp:
        root = Path(temp)
        deployed_heads = _run(["psql", "-X", "-A", "-t", "--dbname", database,
                               "-c", "SELECT version_num FROM alembic_version ORDER BY version_num"], env).split()
        verify_compatibility(deployed_heads, heads)
        dump = root / "database.dump"
        _run(["pg_dump", "--format=custom", "--no-owner", "--no-acl", "--file", str(dump), "--dbname", database], env)
        _run(["pg_restore", "--list", str(dump)], env)
        files = {"database.dump": _sha(dump)}
        for source in sorted(docs.rglob("*")):
            if source.is_symlink():
                raise OperationError("Directorul de documente conține o legătură simbolică.")
            if source.is_file():
                relative = (Path("documents") / source.relative_to(docs)).as_posix()
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                files[relative] = _sha(target)
        manifest = {"format_version": 1, "app_version": "0.1.0", "created_at": datetime.now(UTC).isoformat(),
                    "alembic_heads": deployed_heads, "database": database, "table_counts": {}, "files": files}
        # Source revision and row counts are queried through psql without placing credentials in argv.
        counts = _run(["psql", "-X", "-A", "-t", "--dbname", database,
                       "-c", "SELECT json_object_agg(table_name, row_count)::text FROM (SELECT table_name, (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM %I', table_name), false, true, '')))[1]::text::int AS row_count FROM unnest(ARRAY['clinics','users','patients','appointments','clinical_notes','patient_financial_entries','clinical_documents','login_attempts','patient_access_log']) AS table_name) counts"], env).strip()
        manifest["table_counts"] = json.loads(counts) if counts else {}
        manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        (root / "manifest.json").write_bytes(manifest_bytes)
        (root / "manifest.sha256").write_text(hashlib.sha256(manifest_bytes).hexdigest() + "\n")
        output.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(output, "w:gz") as archive:
            for source in sorted(root.rglob("*")):
                if source.is_file():
                    info = archive.gettarinfo(str(source), arcname=source.relative_to(root).as_posix())
                    info.mode = 0o600
                    with source.open("rb") as stream:
                        archive.addfile(info, stream)
    output.chmod(0o600)
    return output


def restore_backup(archive_path: str | Path, database_url: str, document_dir: str | Path) -> dict:
    docs_target = Path(document_dir)
    if docs_target.exists():
        raise OperationError("Directorul de restaurare trebuie să nu existe.")
    env, database = _pg_env(database_url)
    # Refuse any existing user object before touching either destination.
    nonempty = _run(["psql", "-X", "-A", "-t", "--dbname", database, "-c",
                     "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND c.relkind IN ('r','v','m','S','f')"], env).strip()
    require_empty_database(nonempty)
    expected = _alembic_heads()
    with tempfile.TemporaryDirectory(prefix="dentacare-restore-") as temp:
        root = Path(temp)
        manifest = verify_archive(archive_path, root, expected)
        dump = root / "database.dump"
        _run(["pg_restore", "--list", str(dump)], env)
        _run(["pg_restore", "--single-transaction", "--exit-on-error", "--no-owner", "--no-acl",
              "--dbname", database, str(dump)], env)
        restored_heads = _run(["psql", "-X", "-A", "-t", "--dbname", database, "-c",
                               "SELECT version_num FROM alembic_version ORDER BY version_num"], env).split()
        verify_compatibility(restored_heads, expected)
        counts = _run(["psql", "-X", "-A", "-t", "--dbname", database, "-c",
                       "SELECT json_object_agg(table_name, row_count)::text FROM (SELECT table_name, (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM %I', table_name), false, true, '')))[1]::text::int AS row_count FROM unnest(ARRAY['clinics','users','patients','appointments','clinical_notes','patient_financial_entries','clinical_documents','login_attempts','patient_access_log']) AS table_name) counts"], env).strip()
        actual_counts = json.loads(counts) if counts else {}
        if actual_counts != manifest.get("table_counts"):
            raise OperationError("Numărul de rânduri după restaurare nu corespunde manifestului.")
        staging = docs_target.with_name(f".{docs_target.name}.restore-{os.getpid()}")
        try:
            (root / "documents").mkdir(exist_ok=True)
            shutil.copytree(root / "documents", staging, dirs_exist_ok=False)
            for name, digest in manifest["files"].items():
                if name.startswith("documents/") and _sha(staging.joinpath(*_safe_member(name).parts[1:])) != digest:
                    raise OperationError("Hash-ul documentului restaurat nu corespunde.")
            staging.rename(docs_target)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
    return {"status": "restored", "database": database, "table_counts": actual_counts,
            "alembic_heads": expected}


def main() -> int:
    parser = argparse.ArgumentParser(description="Backup și restaurare PostgreSQL DentaCare.")
    subparsers = parser.add_subparsers(dest="action", required=True)
    backup = subparsers.add_parser("backup")
    backup.add_argument("archive")
    backup.add_argument("--documents", required=True)
    backup.add_argument("--confirm-quiescent", action="store_true")
    restore = subparsers.add_parser("restore")
    restore.add_argument("archive")
    restore.add_argument("--documents", required=True)
    args = parser.parse_args()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise OperationError("Configurează DATABASE_URL.")
    if args.action == "backup":
        result = {"archive": str(create_backup(args.archive, args.documents, database_url,
                                                confirm_quiescent=args.confirm_quiescent))}
    else:
        result = restore_backup(args.archive, database_url, args.documents)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
