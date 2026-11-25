"""T32B clinic location contracts."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.main import app


@pytest.fixture
async def api():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with sessions() as session:
            yield session

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, sessions
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        await engine.dispose()


async def manager(client, name):
    slug = name.lower().replace(" ", "-")
    response = await client.post("/api/auth/register", json={
        "clinic_name": name, "full_name": "Manager", "email": f"{slug}@example.com",
        "password": "correct horse battery staple",
    })
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.mark.asyncio
async def test_locations_are_clinic_scoped_and_default_site_is_returned(api):
    client, _sessions = api
    first = await manager(client, "Clinic One")
    second = await manager(client, "Clinic Two")
    created = await client.post("/api/locations", headers=first, json={
        "name": "Centru", "address": "Strada Test 1", "phone": "0700000000",
    })
    assert created.status_code == 201
    different_timezone = await client.post("/api/locations", headers=first, json={"name": "Vest", "timezone": "America/Los_Angeles"})
    assert different_timezone.status_code == 422
    first_sites = await client.get("/api/locations", headers=first)
    second_sites = await client.get("/api/locations", headers=second)
    assert {site["name"] for site in first_sites.json()} == {"Centru", "Principal"}
    assert [site["name"] for site in second_sites.json()] == ["Principal"]
    first_preferences = await client.get("/api/locations/preferences", headers=first)
    assert first_preferences.json()["all_locations"] is True
    assert first_preferences.json()["default_location_id"]
    saved_preferences = await client.put("/api/locations/preferences", headers=first, json={
        "default_location_id": created.json()["id"], "all_locations": False,
        "location_ids": [created.json()["id"]],
    })
    assert saved_preferences.status_code == 200
    assert saved_preferences.json()["all_locations"] is False
    assert saved_preferences.json()["default_location_id"] == created.json()["id"]
    archived = await client.patch(f"/api/locations/{created.json()['id']}", headers=first, json={"is_active": False})
    assert archived.status_code == 200
    reassigned = await client.get("/api/locations/preferences", headers=first)
    assert reassigned.json()["default_location_id"] != created.json()["id"]
    assert reassigned.json()["default_location_id"] in reassigned.json()["location_ids"]
    foreign_update = await client.patch(f"/api/locations/{first_sites.json()[0]['id']}", headers=second, json={"name": "Hijack"})
    assert foreign_update.status_code == 404


@pytest.mark.asyncio
async def test_room_requires_a_clinic_owned_location(api):
    client, _sessions = api
    headers = await manager(client, "Room Clinic")
    other = await manager(client, "Other Room Clinic")
    foreign_site = (await client.get("/api/locations", headers=other)).json()[0]["id"]
    rejected = await client.post("/api/rooms", headers=headers, json={"name": "Cabinet A", "location_id": foreign_site})
    assert rejected.status_code == 422
    await client.post("/api/locations", headers=headers, json={"name": "Second site"})
    missing = await client.post("/api/rooms", headers=headers, json={"name": "Cabinet B"})
    assert missing.status_code == 422


@pytest.mark.asyncio
async def test_site_filters_reports_inventory_and_guarded_room_moves(api):
    client, sessions = api
    headers = await manager(client, "Filter Clinic")
    sites = (await client.get("/api/locations", headers=headers)).json()
    principal = next(site for site in sites if site["name"] == "Principal")
    second = (await client.post("/api/locations", headers=headers, json={"name": "Nord"})).json()
    room = (await client.post("/api/rooms", headers=headers, json={"name": "Cabinet Nord", "location_id": second["id"]})).json()
    patient = (await client.post("/api/patients", headers=headers, json={"first_name": "Test", "last_name": "Site"})).json()
    manager_user = (await client.get("/api/auth/me", headers=headers)).json()
    appointment = await client.post("/api/appointments", headers=headers, json={
        "patient_id": patient["id"], "doctor_id": manager_user["id"], "room_id": room["id"],
        "starts_at": "2027-01-04T10:00:00+00:00", "duration_minutes": 30, "appointment_type": "Checkup",
    })
    assert appointment.status_code == 201
    assert "location_id" not in appointment.json()
    all_rooms = await client.get("/api/rooms", headers=headers)
    site_rooms = await client.get(f"/api/rooms?location_id={second['id']}", headers=headers)
    assert len(all_rooms.json()) == 1 and [x["id"] for x in site_rooms.json()] == [room["id"]]
    assert (await client.patch(f"/api/locations/{second['id']}", headers=headers, json={"name": "Nord", "is_active": False})).status_code == 409
    assert (await client.patch(f"/api/rooms/{room['id']}", headers=headers, json={"location_id": principal["id"]})).status_code == 409
    empty_room = (await client.post("/api/rooms", headers=headers, json={"name": "Cabinet liber", "location_id": principal["id"]})).json()
    moved = await client.patch(f"/api/rooms/{empty_room['id']}", headers=headers, json={"location_id": second["id"]})
    assert moved.status_code == 200 and moved.json()["room"]["location_id"] == second["id"]

    statements = []
    def count_select(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().lower().startswith("select"):
            statements.append(statement)
    engine = sessions.kw["bind"].sync_engine
    event.listen(engine, "before_cursor_execute", count_select)
    try:
        all_agenda = await client.get("/api/appointments?day=2027-01-04", headers=headers)
        all_count = len(statements)
        statements.clear()
        site_agenda = await client.get(f"/api/appointments?day=2027-01-04&location_id={second['id']}", headers=headers)
        site_count = len(statements)
    finally:
        event.remove(engine, "before_cursor_execute", count_select)
    assert all_agenda.status_code == site_agenda.status_code == 200
    assert len(all_agenda.json()) == len(site_agenda.json()) == 1
    assert all_count == site_count

    report_all = await client.get("/api/reports/appointments_total?from=2027-01-04&to=2027-01-04", headers=headers)
    report_site = await client.get(f"/api/reports/appointments_total?from=2027-01-04&to=2027-01-04&site_id={second['id']}", headers=headers)
    assert report_all.json()["value"] == report_site.json()["value"] == 1
    assert report_site.json()["filters"]["site_id"] == second["id"]

    for name, site_id in (("Stoc principal", principal["id"]), ("Stoc Nord", second["id"])):
        response = await client.post("/api/inventory/locations", headers=headers, json={"name": name, "site_id": site_id})
        assert response.status_code == 201
    stock_all = await client.get("/api/inventory", headers=headers)
    stock_site = await client.get(f"/api/inventory?site_id={second['id']}", headers=headers)
    assert {x["name"] for x in stock_all.json()["locations"]} == {"Stoc principal", "Stoc Nord"}
    assert [x["name"] for x in stock_site.json()["locations"]] == ["Stoc Nord"]
