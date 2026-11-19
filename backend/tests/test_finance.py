"""T13 internal patient account over the real HTTP API (SQLite): services projection from completed
T04 procedures, payments/advances/refunds/adjustments, single reversal, idempotency, references,
roles, clinic isolation, currency/amount validation and the clinic-zone daily report."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.service import create_access_token
from app.database import AuditEvent, Base, PatientFinancialEntry, User, get_db
from app.main import app

PASSWORD = "correct horse battery staple"


@pytest.fixture
async def env():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, sessions
    app.dependency_overrides.clear()
    await engine.dispose()


def h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def clinic(client, sessions, tag: str) -> dict:
    response = await client.post("/api/auth/register", json={
        "clinic_name": f"Clinica Fictivă T13 {tag}", "full_name": f"Manager {tag}",
        "email": f"t13-{tag}-{uuid4().hex[:6]}@example.com", "password": PASSWORD})
    assert response.status_code == 201, response.text
    manager = h(response.json()["access_token"])
    me = (await client.get("/api/auth/me", headers=manager)).json()
    room = (await client.post("/api/rooms", headers=manager, json={"name": "Cabinet T13"})).json()["id"]
    roles = {role: await add_user(sessions, me["clinic_id"], role) for role in ("doctor", "assistant", "reception", "administrator")}
    services = []
    for code, price in (("OBT", "250.00"), ("DET", "120.50"), ("RX", "80.00")):
        created = await client.post("/api/procedures", headers=manager, json={
            "code": f"{code}{tag}", "name": f"Serviciu {code}", "default_duration_minutes": 30, "base_price": price,
            "currency": "RON"})
        assert created.status_code == 201, created.text
        services.append(created.json()["id"])
    return {"manager": manager, "me": me, "room": room, "services": services, **roles}


async def add_user(sessions, clinic_id: str, role: str) -> dict[str, str]:
    user = User(id=uuid4(), clinic_id=UUID(clinic_id), email=f"{role}-{uuid4().hex[:6]}@example.com",
                password_hash="unused", full_name=f"{role} fictiv", role=role, is_active=True)
    async with sessions() as session:
        session.add(user)
        await session.commit()
    return h(create_access_token(user))


async def patient(client, ctx, first="Ana") -> str:
    response = await client.post("/api/patients", headers=ctx["manager"], json={"first_name": first, "last_name": "Fictiv"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def visit(client, ctx, patient_id: str, day: int = 1) -> str:
    response = await client.post("/api/appointments", headers=ctx["manager"], json={
        "patient_id": patient_id, "doctor_id": ctx["me"]["id"], "room_id": ctx["room"],
        "starts_at": f"2026-12-{day:02d}T09:00:00+00:00", "duration_minutes": 30, "appointment_type": "Tratament"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def procedure(client, ctx, visit_id: str, service: int, final: str | None = "completed") -> str:
    created = await client.post("/api/performed-procedures", headers=ctx["manager"], json={
        "appointment_id": visit_id, "procedure_catalog_id": ctx["services"][service]})
    assert created.status_code == 201, created.text
    pid = created.json()["id"]
    if final in ("completed", "running", "paused"):
        assert (await client.post(f"/api/performed-procedures/{pid}/start", headers=ctx["manager"])).status_code == 200
    if final == "paused":
        assert (await client.post(f"/api/performed-procedures/{pid}/pause", headers=ctx["manager"])).status_code == 200
    if final == "completed":
        assert (await client.post(f"/api/performed-procedures/{pid}/complete", headers=ctx["manager"])).status_code == 200
    if final == "cancelled":
        assert (await client.post(f"/api/performed-procedures/{pid}/cancel", headers=ctx["manager"], json={"transition": "cancel", "notes": "Anulat fictiv"})).status_code == 200
    return pid


async def post_entry(client, auth, patient_id, **body):
    payload = {"client_request_id": str(uuid4()), **body}
    return await client.post(f"/api/finance/patients/{patient_id}/entries", headers=auth, json=payload)


async def account(client, auth, patient_id) -> dict:
    response = await client.get(f"/api/finance/patients/{patient_id}/account", headers=auth)
    assert response.status_code == 200, response.text
    return response.json()


async def entry_count(sessions) -> int:
    async with sessions() as session:
        return await session.scalar(select(func.count()).select_from(PatientFinancialEntry))


async def test_services_projection_counts_only_completed_and_follows_price_override(env):
    client, sessions = env
    ctx = await clinic(client, sessions, "a")
    pa = await patient(client, ctx)
    v1 = await visit(client, ctx, pa, 1)
    v2 = await visit(client, ctx, pa, 2)
    done1 = await procedure(client, ctx, v1, 0)                  # 250.00
    await procedure(client, ctx, v1, 1)                          # 120.50
    await procedure(client, ctx, v1, 2, "cancelled")             # not owed
    await procedure(client, ctx, v2, 2, None)                    # pending
    await procedure(client, ctx, v2, 1, "paused")                # paused (one running per doctor: pause first)
    await procedure(client, ctx, v2, 0, "running")               # running
    data = await account(client, ctx["reception"], pa)
    assert data["totals"]["services_completed"] == "370.50"
    assert data["totals"]["balance"] == "370.50" and data["totals"]["balance_status"] == "due"
    assert data["services"]["in_progress_count"] == 3 and data["services"]["cancelled_count"] == 1
    visits = {v["appointment_id"]: v for v in data["services"]["visits"]}
    assert visits[v1]["completed_total"] == "370.50" and visits[v2]["completed_total"] == "0.00"
    assert all(p["amount"] is None for p in visits[v2]["procedures"])
    assert data["currency"] == "RON" and data["timezone"] == "Europe/Bucharest"

    # Manager price override after completion (existing T04 revision flow) is reflected, not copied.
    override = await client.post(f"/api/performed-procedures/{done1}/override-price", headers=ctx["manager"],
                                 json={"new_price": "200.00", "reason": "Reducere fidelitate (fictiv)"})
    assert override.status_code == 200, override.text
    data = await account(client, ctx["doctor"], pa)
    assert data["totals"]["services_completed"] == "320.50"
    overridden = next(p for p in data["services"]["visits"][0]["procedures"] if p["id"] == done1)
    assert overridden["amount"] == "200.00" and overridden["price_overridden"] is True
    revisions = (await client.get(f"/api/performed-procedures/{done1}/revisions", headers=ctx["manager"])).json()
    assert any(r["field"] == "current_price" and r["new_value"] == "200.00" for r in revisions)


async def test_payment_advance_refund_adjustments_and_balance(env):
    client, sessions = env
    ctx = await clinic(client, sessions, "b")
    pa = await patient(client, ctx)
    v1 = await visit(client, ctx, pa)
    await procedure(client, ctx, v1, 0)  # 250.00
    pay = await post_entry(client, ctx["reception"], pa, kind="payment", amount="100.00", method="cash", appointment_id=v1)
    assert pay.status_code == 201, pay.text
    body = pay.json()
    assert body["created_at"].endswith("+00:00") and body["created_by"]["name"] == "reception fictiv"
    assert body["appointment_id"] == v1 and body["currency"] == "RON"
    assert (await account(client, ctx["manager"], pa))["totals"]["balance"] == "150.00"
    # Advance: paying more than owed produces an explicit credit (negative balance).
    adv = await post_entry(client, ctx["reception"], pa, kind="payment", amount="200.00", method="card")
    assert adv.status_code == 201
    totals = (await account(client, ctx["manager"], pa))["totals"]
    assert totals["balance"] == "-50.00" and totals["balance_status"] == "credit" and totals["paid_net"] == "300.00"
    refund = await post_entry(client, ctx["reception"], pa, kind="refund", amount="50.00", method="cash", reason="Rest avans")
    assert refund.status_code == 201
    totals = (await account(client, ctx["manager"], pa))["totals"]
    assert totals["balance"] == "0.00" and totals["balance_status"] == "settled" and totals["refunds"] == "50.00"
    debit = await post_entry(client, ctx["manager"], pa, kind="adjustment_debit", amount="10.10", reason="Materiale suplimentare")
    credit = await post_entry(client, ctx["administrator"], pa, kind="adjustment_credit", amount="0.05", reason="Rotunjire")
    assert debit.status_code == 201 and credit.status_code == 201
    totals = (await account(client, ctx["manager"], pa))["totals"]
    assert totals["balance"] == "10.05" and totals["adjustments_debit"] == "10.10" and totals["adjustments_credit"] == "0.05"
    summary = (await client.get(f"/api/finance/appointments/{v1}/summary", headers=ctx["doctor"])).json()
    assert summary["services_completed"] == "250.00" and summary["paid_net_for_visit"] == "100.00"
    async with sessions() as session:
        audits = await session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.entity_type == "financial_entry"))
    assert audits == 5


async def test_reversal_is_single_traceable_and_cannot_be_reversed(env):
    client, sessions = env
    ctx = await clinic(client, sessions, "c")
    pa = await patient(client, ctx)
    v1 = await visit(client, ctx, pa)
    await procedure(client, ctx, v1, 0)
    pay = (await post_entry(client, ctx["reception"], pa, kind="payment", amount="250.00", method="transfer")).json()
    assert (await account(client, ctx["manager"], pa))["totals"]["balance"] == "0.00"
    missing_reason = await client.post(f"/api/finance/entries/{pay['id']}/reverse", headers=ctx["reception"],
                                       json={"reason": "  ", "client_request_id": str(uuid4())})
    assert missing_reason.status_code == 422
    rev = await client.post(f"/api/finance/entries/{pay['id']}/reverse", headers=ctx["reception"],
                            json={"reason": "Încasare înregistrată greșit", "client_request_id": str(uuid4())})
    assert rev.status_code == 201, rev.text
    assert rev.json()["kind"] == "reversal" and rev.json()["reverses_entry_id"] == pay["id"] and rev.json()["amount"] == "250.00"
    data = await account(client, ctx["manager"], pa)
    assert data["totals"]["balance"] == "250.00" and data["totals"]["payments"] == "0.00"
    original = next(e for e in data["entries"] if e["id"] == pay["id"])
    assert original["reversed_by"]["reason"] == "Încasare înregistrată greșit" and original["reversed_by"]["created_at"].endswith("+00:00")
    again = await client.post(f"/api/finance/entries/{pay['id']}/reverse", headers=ctx["manager"],
                              json={"reason": "a doua oară", "client_request_id": str(uuid4())})
    assert again.status_code == 409
    rev_of_rev = await client.post(f"/api/finance/entries/{rev.json()['id']}/reverse", headers=ctx["manager"],
                                   json={"reason": "anulare anulare", "client_request_id": str(uuid4())})
    assert rev_of_rev.status_code == 409
    assert await entry_count(sessions) == 2
    # Adjustment reversal is manager-only.
    adj = (await post_entry(client, ctx["manager"], pa, kind="adjustment_credit", amount="50.00", reason="Gest comercial")).json()
    by_reception = await client.post(f"/api/finance/entries/{adj['id']}/reverse", headers=ctx["reception"],
                                     json={"reason": "x", "client_request_id": str(uuid4())})
    assert by_reception.status_code == 403
    by_manager = await client.post(f"/api/finance/entries/{adj['id']}/reverse", headers=ctx["manager"],
                                   json={"reason": "Ajustare greșită", "client_request_id": str(uuid4())})
    assert by_manager.status_code == 201
    assert (await account(client, ctx["manager"], pa))["totals"]["balance"] == "250.00"
    # No update/delete routes exist for movements.
    for method in ("PATCH", "PUT", "DELETE"):
        response = await client.request(method, f"/api/finance/entries/{pay['id']}", headers=ctx["manager"])
        assert response.status_code in (404, 405)


async def test_idempotent_retries_return_the_existing_movement(env):
    client, sessions = env
    ctx = await clinic(client, sessions, "d")
    pa = await patient(client, ctx)
    key = str(uuid4())
    first = await post_entry(client, ctx["reception"], pa, kind="payment", amount="75.00", method="cash", client_request_id=key)
    second = await post_entry(client, ctx["reception"], pa, kind="payment", amount="75.00", method="cash", client_request_id=key)
    assert first.status_code == 201 and second.status_code == 200 and first.json()["id"] == second.json()["id"]
    different = await post_entry(client, ctx["reception"], pa, kind="payment", amount="76.00", method="cash", client_request_id=key)
    assert different.status_code == 409
    assert await entry_count(sessions) == 1
    rkey = str(uuid4())
    r1 = await client.post(f"/api/finance/entries/{first.json()['id']}/reverse", headers=ctx["reception"], json={"reason": "greșit", "client_request_id": rkey})
    r2 = await client.post(f"/api/finance/entries/{first.json()['id']}/reverse", headers=ctx["reception"], json={"reason": "greșit", "client_request_id": rkey})
    assert r1.status_code == 201 and r2.status_code == 200 and r1.json()["id"] == r2.json()["id"]
    assert await entry_count(sessions) == 2


async def test_references_roles_isolation_currency_and_amounts(env):
    client, sessions = env
    ctx = await clinic(client, sessions, "e")
    other = await clinic(client, sessions, "f")
    pa = await patient(client, ctx, "Ana")
    pb = await patient(client, ctx, "Bianca")
    vb = await visit(client, ctx, pb)
    foreign_patient = await patient(client, other, "Străin")
    foreign_visit = await visit(client, other, foreign_patient)

    wrong_visit = await post_entry(client, ctx["reception"], pa, kind="payment", amount="10.00", method="cash", appointment_id=vb)
    assert wrong_visit.status_code == 422
    foreign_ref = await post_entry(client, ctx["reception"], pa, kind="payment", amount="10.00", method="cash", appointment_id=foreign_visit)
    assert foreign_ref.status_code == 422

    for role in ("doctor", "assistant"):
        assert (await post_entry(client, ctx[role], pa, kind="payment", amount="10.00", method="cash")).status_code == 403
        assert (await client.get(f"/api/finance/patients/{pa}/account", headers=ctx[role])).status_code == 200
        assert (await client.get("/api/finance/daily", params={"date": "2026-11-19"}, headers=ctx[role])).status_code == 403
    for kind in ("adjustment_debit", "adjustment_credit"):
        assert (await post_entry(client, ctx["reception"], pa, kind=kind, amount="5.00", reason="x")).status_code == 403
    assert (await post_entry(client, ctx["reception"], pa, kind="reversal", amount="5.00", reason="x")).status_code == 422

    paid = (await post_entry(client, ctx["reception"], pa, kind="payment", amount="10.00", method="cash")).json()
    for path in (f"/api/finance/patients/{pa}/account",):
        assert (await client.get(path, headers=other["manager"])).status_code == 404
    assert (await post_entry(client, other["manager"], pa, kind="payment", amount="10.00", method="cash")).status_code == 404
    assert (await client.post(f"/api/finance/entries/{paid['id']}/reverse", headers=other["manager"],
                              json={"reason": "x", "client_request_id": str(uuid4())})).status_code == 404
    assert (await client.get(f"/api/finance/appointments/{vb}/summary", headers=other["manager"])).status_code == 404
    other_day = (await client.get("/api/finance/daily", params={"date": datetime.now(UTC).date().isoformat()}, headers=other["manager"])).json()
    assert other_day["entries"] == [] and other_day["net"] == "0.00"
    # The same client_request_id in another clinic is independent.
    same_key = await post_entry(client, other["manager"], foreign_patient, kind="payment", amount="10.00", method="cash",
                                client_request_id=paid["client_request_id"])
    assert same_key.status_code == 201

    bad = [
        {"amount": "0.00"}, {"amount": "-5.00"}, {"amount": "1.234"}, {"amount": 10}, {"amount": 10.5}, {"amount": "abc"},
        {"amount": "12345678901.00"}, {"currency": "EUR"}, {"method": None}, {"method": "crypto"},
    ]
    for override in bad:
        body = {"kind": "payment", "amount": "10.00", "method": "cash", **override}
        assert (await post_entry(client, ctx["reception"], pa, **body)).status_code == 422, override
    assert (await post_entry(client, ctx["reception"], pa, kind="refund", amount="1.00", method="cash")).status_code == 422
    assert (await post_entry(client, ctx["manager"], pa, kind="adjustment_debit", amount="1.00", reason="  ")).status_code == 422
    assert (await post_entry(client, ctx["manager"], pa, kind="adjustment_debit", amount="1.00", method="cash", reason="x")).status_code == 422
    assert (await post_entry(client, ctx["reception"], pa, kind="payment", amount="1.00", method="cash", currency="ron")).status_code == 201


async def test_daily_report_uses_the_clinic_day_and_nets_by_method(env):
    client, sessions = env
    ctx = await clinic(client, sessions, "g")
    pa = await patient(client, ctx)
    clinic_id = UUID(ctx["me"]["clinic_id"])
    actor = UUID(ctx["me"]["id"])

    def row(kind, amount, at, method=None, reason=None, reverses=None):
        return PatientFinancialEntry(id=uuid4(), clinic_id=clinic_id, patient_id=UUID(pa), kind=kind, amount=amount,
                                     currency="RON", method=method, reason=reason, reverses_entry_id=reverses,
                                     client_request_id=str(uuid4()), created_by_id=actor, created_at=at)

    late = row("payment", "100.00", datetime(2026, 11, 19, 22, 30, tzinfo=UTC), "cash")      # 20 Nov 00:30 Bucharest
    before = row("payment", "40.00", datetime(2026, 11, 19, 21, 30, tzinfo=UTC), "card")     # 19 Nov 23:30 Bucharest
    card = row("payment", "60.00", datetime(2026, 11, 20, 9, 0, tzinfo=UTC), "card")
    refund = row("refund", "15.00", datetime(2026, 11, 20, 10, 0, tzinfo=UTC), "cash", "Rest")
    async with sessions() as session:
        session.add_all([late, before, card, refund])
        await session.flush()
        session.add(row("reversal", "40.00", datetime(2026, 11, 20, 11, 0, tzinfo=UTC), reason="greșit", reverses=before.id))
        session.add(row("adjustment_credit", "5.00", datetime(2026, 11, 20, 12, 0, tzinfo=UTC), reason="gest"))
        await session.commit()
    day = (await client.get("/api/finance/daily", params={"date": "2026-11-20"}, headers=ctx["reception"])).json()
    assert day["timezone"] == "Europe/Bucharest" and day["range"]["start"] == "2026-11-19T22:00:00+00:00"
    assert day["by_method"] == {"cash": "85.00", "card": "20.00", "transfer": "0.00", "other": "0.00"}
    assert day["received"] == "160.00" and day["refunded"] == "15.00" and day["reversed"] == "-40.00" and day["net"] == "105.00"
    assert all(e["created_at"].endswith("+00:00") for e in day["entries"]) and len(day["entries"]) == 4
    previous = (await client.get("/api/finance/daily", params={"date": "2026-11-19"}, headers=ctx["manager"])).json()
    assert previous["by_method"]["card"] == "40.00" and previous["net"] == "40.00"
    assert (await client.get("/api/finance/daily", params={"date": "2026-02-30"}, headers=ctx["manager"])).status_code == 422


async def test_schema_constraints_reject_invalid_movements_on_sqlite(env):
    """Same CHECK/unique set as the migration (verified on PostgreSQL in test_postgres_finance.py)."""
    from decimal import Decimal

    from sqlalchemy.exc import IntegrityError
    client, sessions = env
    ctx = await clinic(client, sessions, "h")
    pa = UUID(await patient(client, ctx))
    clinic_id, actor = UUID(ctx["me"]["clinic_id"]), UUID(ctx["me"]["id"])
    payment_id, request = uuid4(), str(uuid4())

    async def insert(**overrides) -> None:
        values = {"id": uuid4(), "clinic_id": clinic_id, "patient_id": pa, "kind": "payment", "amount": Decimal("10.00"),
                  "currency": "RON", "method": "cash", "reason": None, "reverses_entry_id": None,
                  "client_request_id": str(uuid4()), "created_by_id": actor, "created_at": datetime.now(UTC)}
        values.update(overrides)
        async with sessions() as session:
            session.add(PatientFinancialEntry(**values))
            await session.commit()

    await insert(id=payment_id, client_request_id=request)
    bad = [{"client_request_id": request}, {"kind": "invoice"}, {"amount": Decimal(0)}, {"amount": Decimal(-1)},
           {"currency": "EUR"}, {"method": None}, {"method": "crypto"}, {"kind": "adjustment_debit", "reason": "x"},
           {"kind": "refund", "reason": None}, {"kind": "refund", "reason": "  "},
           {"kind": "reversal", "method": None, "reason": "x"}, {"reverses_entry_id": payment_id}]
    for override in bad:
        with pytest.raises(IntegrityError):
            await insert(**override)
    await insert(kind="reversal", method=None, reason="greșit", reverses_entry_id=payment_id)
    with pytest.raises(IntegrityError):
        await insert(kind="reversal", method=None, reason="a doua", reverses_entry_id=payment_id)
    assert await entry_count(sessions) == 2
