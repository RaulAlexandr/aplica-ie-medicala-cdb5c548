#!/usr/bin/env python3
"""Compare a restored pilot copy with its source (read-only on both sides).

usage: verify_copy.py SOURCE_DIR COPY_DIR      (each holds fictional-clinic.sqlite3 and documents/)

Checks, per clinic data set: patients, T11 document rows with file SHA-256 recomputed from disk,
periodontal assessments, treatment plans with items (incl. the linked performed procedure) and T13
financial movements with the per-patient balance. Prints counts and digests only: no names, no
credentials. Exit code 1 on any difference.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path


def rows(path: Path, sql: str) -> list[tuple]:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        return [tuple(r) for r in conn.execute(sql)]


def digest(items: list[tuple]) -> str:
    return hashlib.sha256(json.dumps(sorted(map(str, items))).encode()).hexdigest()[:16]


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(base: Path) -> dict:
    db = base / "fictional-clinic.sqlite3"
    docs = base / "documents"
    out: dict = {}
    out["patients"] = rows(db, "select id, first_name, last_name, date_of_birth, allergies from patients")
    documents = rows(db, "select id, patient_id, category, original_filename, size_bytes, sha256, storage_key, clinic_id from clinical_documents")
    on_disk = []
    for doc_id, _patient, _category, _name, _size, recorded, key, clinic in documents:
        path = docs / str(clinic) / key[:2] / key
        on_disk.append((doc_id, recorded, file_sha(path) if path.is_file() else "MISSING"))
    out["documents"] = [d[:6] for d in documents]
    out["document_files"] = on_disk
    out["assessments"] = rows(db, "select id, patient_id, clinical_date, status, previous_assessment_id, finalized_at from periodontal_assessments")
    out["assessment_sites"] = rows(db, "select count(*), coalesce(sum(probing_depth_mm),0) from periodontal_site_measurements")
    out["plans"] = rows(db, "select id, patient_id, title, status, version, accepted_version from treatment_plans")
    out["plan_items"] = rows(db, "select id, plan_id, procedure_code, tooth_fdi, status, performed_procedure_id from treatment_plan_items")
    out["performed"] = rows(db, "select id, patient_id, procedure_code, status, current_price from performed_procedures")
    out["entries"] = rows(db, "select id, patient_id, kind, amount, method, reason, reverses_entry_id from patient_financial_entries")
    balance: dict[str, Decimal] = {}
    reversed_ids = {e[6] for e in out["entries"] if e[6]}
    for _id, patient, kind, amount, _method, _reason, _rev in out["entries"]:
        value = Decimal(str(amount))
        if kind in ("payment", "adjustment_credit") and _id not in reversed_ids:
            balance[patient] = balance.get(patient, Decimal(0)) + value
        elif kind in ("refund", "adjustment_debit") and _id not in reversed_ids:
            balance[patient] = balance.get(patient, Decimal(0)) - value
    services: dict[str, Decimal] = {}
    for _id, patient, _code, status, price in out["performed"]:
        if status == "completed" and price is not None:
            services[patient] = services.get(patient, Decimal(0)) + Decimal(str(price))
    out["net_received"] = sorted((p, str(v)) for p, v in balance.items())
    out["services_completed"] = sorted((p, str(v)) for p, v in services.items())
    out["balance_due"] = sorted((p, str(services.get(p, Decimal(0)) - balance.get(p, Decimal(0)))) for p in set(services) | set(balance))
    return out


def main() -> int:
    source, copy = Path(sys.argv[1]), Path(sys.argv[2])
    a, b = snapshot(source), snapshot(copy)
    result = {"checks": [], "ok": True}
    for key in a:
        same = a[key] == b[key]
        result["ok"] &= same
        result["checks"].append({"name": key, "rows": len(a[key]), "digest_source": digest(a[key]), "digest_copy": digest(b[key]), "identical": same})
    missing = [d for d in b["document_files"] if d[2] == "MISSING" or d[1] != d[2]]
    result["documents_hash_verified_in_copy"] = len(b["document_files"]) - len(missing)
    result["ok"] &= not missing
    result["balance_due_by_patient_count"] = len(b["balance_due"])
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
