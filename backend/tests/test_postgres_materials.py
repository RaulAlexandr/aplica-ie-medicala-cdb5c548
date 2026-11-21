"""T18 PostgreSQL locking, idempotency and atomicity checks."""
import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import get_db
from app.main import app

pytestmark = pytest.mark.postgres
PASSWORD = "correct horse battery staple"

@pytest.fixture
async def pg_client():
    url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url or not url.startswith("postgresql+"):
        pytest.skip("PostgreSQL CI URL required")
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async def override():
        async with sessions() as session:
            yield session
    app.dependency_overrides[get_db] = override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()

async def setup(c, quantity="5"):
    reg = await c.post("/api/auth/register", json={"clinic_name":"Clinică T18 PG fictivă","full_name":"Manager PG","email":f"{uuid4().hex}@example.com","password":PASSWORD})
    assert reg.status_code == 201, reg.text
    h = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    me = (await c.get("/api/auth/me", headers=h)).json()
    patient = (await c.post("/api/patients", headers=h, json={"first_name":"Pacient","last_name":"PG"})).json()
    room = (await c.post("/api/rooms", headers=h, json={"name":f"Cabinet {uuid4().hex[:6]}"})).json()
    item = (await c.post("/api/inventory/items", headers=h, json={"name":f"Material {uuid4().hex[:6]}","unit":"buc","minimum_quantity":"0"})).json()
    location = (await c.post("/api/inventory/locations", headers=h, json={"name":f"Depozit {uuid4().hex[:6]}"})).json()
    lot = (await c.post("/api/inventory/lots", headers=h, json={"item_id":item["id"],"code":f"LOT-{uuid4().hex[:8]}"})).json()
    receipt = await c.post("/api/inventory/movements/receipt", headers=h, json={"item_id":item["id"],"lot_id":lot["id"],"location_id":location["id"],"quantity":quantity,"client_request_id":str(uuid4())})
    assert receipt.status_code == 201, receipt.text
    proc = await c.post("/api/procedures", headers=h, json={"code":f"T18-{uuid4().hex[:6]}","name":"Procedură PG","base_price":"10","default_duration_minutes":30})
    assert proc.status_code == 201, proc.text
    appointment_index = 0
    def appointment():
        nonlocal appointment_index
        starts_at = datetime(2026, 12, 2, 9, tzinfo=UTC) + timedelta(hours=appointment_index)
        appointment_index += 1
        return c.post("/api/appointments", headers=h, json={"patient_id":patient["id"],"doctor_id":me["id"],"room_id":room["id"],"starts_at":starts_at.isoformat(),"duration_minutes":30,"status":"scheduled","appointment_type":"Control"})
    async def performed():
        appt = await appointment()
        assert appt.status_code == 201, appt.text
        result = await c.post("/api/performed-procedures", headers=h, json={"appointment_id":appt.json()["id"],"procedure_catalog_id":proc.json()["id"]})
        assert result.status_code == 201, result.text
        return appt.json()["id"], result.json()["id"]
    return h, item["id"], lot["id"], location["id"], proc.json()["id"], appointment, performed

async def barrier_client(c):
    url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    engine = create_async_engine(url); sessions = async_sessionmaker(engine, expire_on_commit=False)
    ready = asyncio.Event(); lock = asyncio.Lock(); count = 0
    async def override():
        nonlocal count
        async with sessions() as session:
            async with lock:
                count += 1
                if count == 2: ready.set()
            await asyncio.wait_for(ready.wait(), 10)
            yield session
    original = app.dependency_overrides[get_db]
    app.dependency_overrides[get_db] = override
    return original, engine, lambda: count

async def reserve_request(c, h, appointment_id, item_id, qty, request_id=None):
    return await c.post("/api/materials/reservations", headers=h, json={"appointment_id":appointment_id,"item_id":item_id,"quantity":qty,"client_request_id":request_id or str(uuid4())})

@pytest.mark.asyncio
async def test_postgres_last_material_reservation_is_serialized(pg_client):
    c=pg_client;h,item,_lot,_loc,_proc,appointment,_performed=await setup(c,"5")
    a,b=await asyncio.gather(appointment(),appointment());assert a.status_code==b.status_code==201
    base,engine,count=await barrier_client(c)
    try:
        results=await asyncio.gather(reserve_request(c,h,a.json()["id"],item,"4"),reserve_request(c,h,b.json()["id"],item,"4"))
        assert sorted(x.status_code for x in results)==[201,409],[x.text for x in results]
        assert count()==2
    finally:app.dependency_overrides[get_db]=base;await engine.dispose()

@pytest.mark.asyncio
async def test_postgres_same_material_confirmation_is_idempotent_under_concurrency(pg_client):
    c=pg_client;h,item,lot,location,_proc,_appointment,performed=await setup(c,"5");_appt,pid=await performed()
    request_id=str(uuid4());body={"client_request_id":request_id,"lines":[{"item_id":item,"lot_id":lot,"location_id":location,"quantity":"2"}]}
    base,engine,count=await barrier_client(c)
    try:
        results=await asyncio.gather(*(c.post(f"/api/materials/performed-procedures/{pid}/confirm",headers=h,json=body) for _ in range(2)))
        assert [x.status_code for x in results]==[201,201],[x.text for x in results]
        assert results[0].json()["id"]==results[1].json()["id"]
        assert count()==2
    finally:app.dependency_overrides[get_db]=base;await engine.dispose()

@pytest.mark.asyncio
async def test_postgres_two_confirmations_competing_for_one_lot_are_serialized(pg_client):
    c=pg_client;h,item,lot,location,_proc,_appointment,performed=await setup(c,"5");one,two=await asyncio.gather(performed(),performed())
    base,engine,count=await barrier_client(c)
    try:
        async def confirm(pid):return await c.post(f"/api/materials/performed-procedures/{pid}/confirm",headers=h,json={"client_request_id":str(uuid4()),"lines":[{"item_id":item,"lot_id":lot,"location_id":location,"quantity":"4"}]})
        results=await asyncio.gather(confirm(one[1]),confirm(two[1]))
        assert sorted(x.status_code for x in results)==[201,409],[x.text for x in results]
        assert count()==2
    finally:app.dependency_overrides[get_db]=base;await engine.dispose()

@pytest.mark.asyncio
async def test_postgres_failed_confirmation_rolls_back_all_material_lines(pg_client):
    c=pg_client;h,item,lot,location,_proc,_appointment,performed=await setup(c,"5");_appt,pid=await performed()
    second_item=(await c.post("/api/inventory/items",headers=h,json={"name":f"Material atomic {uuid4().hex[:6]}","unit":"buc","minimum_quantity":"0"})).json()
    second_lot=(await c.post("/api/inventory/lots",headers=h,json={"item_id":second_item["id"],"code":f"LOT-{uuid4().hex[:8]}"})).json()
    receipt=await c.post("/api/inventory/movements/receipt",headers=h,json={"item_id":second_item["id"],"lot_id":second_lot["id"],"location_id":location,"quantity":"1","client_request_id":str(uuid4())})
    assert receipt.status_code==201,receipt.text
    before=(await c.get("/api/inventory",headers=h)).json()
    first={"item_id":item,"lot_id":lot,"location_id":location,"quantity":"1"}
    second={"item_id":second_item["id"],"lot_id":second_lot["id"],"location_id":location,"quantity":"2"}
    if second_lot["id"] < lot:
        first,second={**second,"quantity":"1"},{**first,"quantity":"6"}
    lines=[first,second]
    response=await c.post(f"/api/materials/performed-procedures/{pid}/confirm",headers=h,json={"client_request_id":str(uuid4()),"lines":lines})
    assert response.status_code==409,response.text
    after=(await c.get("/api/inventory",headers=h)).json()
    assert before["balances"]==after["balances"]
