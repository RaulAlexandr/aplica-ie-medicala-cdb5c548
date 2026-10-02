import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.main import app


@pytest.fixture
async def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with sessions() as session:
            yield session
    app.dependency_overrides[get_db] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()
    await engine.dispose()


async def register(client, clinic="Bright Smiles", email="manager@example.com"):
    response = await client.post("/api/auth/register", json={"clinic_name": clinic, "full_name": "Clinic Manager", "email": email, "password": "correct horse battery staple"})
    assert response.status_code == 201
    return response.json()


@pytest.mark.asyncio
async def test_procedure_catalog_crud(client):
    tokens = await register(client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    # Create a procedure
    create_resp = await client.post("/api/procedures", headers=headers, json={
        "code": "EXAM-001",
        "name": "Dental Examination",
        "description": "Comprehensive dental checkup",
        "category": "Diagnostic",
        "default_duration_minutes": 30,
        "base_price": "150.00",
        "currency": "RON"
    })
    assert create_resp.status_code == 201
    procedure = create_resp.json()
    assert procedure["code"] == "EXAM-001"
    assert procedure["name"] == "Dental Examination"
    assert procedure["base_price"] == "150.00"
    assert procedure["currency"] == "RON"
    assert procedure["is_active"] is True

    # List procedures
    list_resp = await client.get("/api/procedures", headers=headers)
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 1

    # Get single procedure
    get_resp = await client.get(f"/api/procedures/{procedure['id']}", headers=headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == procedure["id"]

    # Update procedure
    update_resp = await client.patch(f"/api/procedures/{procedure['id']}", headers=headers, json={
        "name": "Comprehensive Dental Examination",
        "base_price": "175.50"
    })
    assert update_resp.status_code == 200
    updated = update_resp.json()
    assert updated["name"] == "Comprehensive Dental Examination"
    assert updated["base_price"] == "175.50"

    # Deactivate procedure
    deactivate_resp = await client.post(f"/api/procedures/{procedure['id']}/deactivate", headers=headers)
    assert deactivate_resp.status_code == 200
    assert deactivate_resp.json()["is_active"] is False

    # Activate procedure
    activate_resp = await client.post(f"/api/procedures/{procedure['id']}/activate", headers=headers)
    assert activate_resp.status_code == 200
    assert activate_resp.json()["is_active"] is True


@pytest.mark.asyncio
async def test_procedure_code_uniqueness(client):
    tokens = await register(client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    # Create first procedure
    await client.post("/api/procedures", headers=headers, json={
        "code": "CLEAN-001",
        "name": "Teeth Cleaning",
        "default_duration_minutes": 45,
        "base_price": "100.00",
        "currency": "RON"
    })

    # Try to create duplicate code (same case)
    dup_resp = await client.post("/api/procedures", headers=headers, json={
        "code": "CLEAN-001",
        "name": "Another Cleaning",
        "default_duration_minutes": 60,
        "base_price": "200.00",
        "currency": "RON"
    })
    assert dup_resp.status_code == 409

    # Try to create duplicate code (different case)
    dup_case_resp = await client.post("/api/procedures", headers=headers, json={
        "code": "clean-001",
        "name": "Yet Another Cleaning",
        "default_duration_minutes": 30,
        "base_price": "50.00",
        "currency": "RON"
    })
    assert dup_case_resp.status_code == 409


@pytest.mark.asyncio
async def test_procedure_validation(client):
    tokens = await register(client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    # Invalid duration (too low)
    resp = await client.post("/api/procedures", headers=headers, json={
        "code": "TEST-001",
        "name": "Test",
        "default_duration_minutes": 0,
        "base_price": "10.00",
        "currency": "RON"
    })
    assert resp.status_code == 422

    # Invalid duration (too high)
    resp = await client.post("/api/procedures", headers=headers, json={
        "code": "TEST-002",
        "name": "Test",
        "default_duration_minutes": 2000,
        "base_price": "10.00",
        "currency": "RON"
    })
    assert resp.status_code == 422

    # Negative price
    resp = await client.post("/api/procedures", headers=headers, json={
        "code": "TEST-003",
        "name": "Test",
        "default_duration_minutes": 30,
        "base_price": "-10.00",
        "currency": "RON"
    })
    assert resp.status_code == 422

    # Price with too many decimal places
    resp = await client.post("/api/procedures", headers=headers, json={
        "code": "TEST-004",
        "name": "Test",
        "default_duration_minutes": 30,
        "base_price": "10.123",
        "currency": "RON"
    })
    assert resp.status_code == 422

    # Empty code
    resp = await client.post("/api/procedures", headers=headers, json={
        "code": "",
        "name": "Test",
        "default_duration_minutes": 30,
        "base_price": "10.00",
        "currency": "RON"
    })
    assert resp.status_code == 422

    # Whitespace-only code
    resp = await client.post("/api/procedures", headers=headers, json={
        "code": "   ",
        "name": "Test",
        "default_duration_minutes": 30,
        "base_price": "10.00",
        "currency": "RON"
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_procedure_tenant_isolation(client):
    first = await register(client)
    headers1 = {"Authorization": f"Bearer {first['access_token']}"}

    # Create procedure in first clinic
    await client.post("/api/procedures", headers=headers1, json={
        "code": "FILL-001",
        "name": "Filling",
        "default_duration_minutes": 60,
        "base_price": "200.00",
        "currency": "RON"
    })

    # Second clinic should not see first clinic's procedures
    second = await register(client, "Other Clinic", "other@example.com")
    headers2 = {"Authorization": f"Bearer {second['access_token']}"}

    list_resp = await client.get("/api/procedures", headers=headers2)
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 0

    # Second clinic can create their own procedure with same code
    create_resp = await client.post("/api/procedures", headers=headers2, json={
        "code": "FILL-001",
        "name": "Filling",
        "default_duration_minutes": 60,
        "base_price": "250.00",
        "currency": "RON"
    })
    assert create_resp.status_code == 201


@pytest.mark.asyncio
async def test_procedure_search_and_filtering(client):
    tokens = await register(client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    # Create test procedures
    await client.post("/api/procedures", headers=headers, json={
        "code": "SEARCH-001",
        "name": "Root Canal",
        "category": "Endodontics",
        "default_duration_minutes": 90,
        "base_price": "500.00",
        "currency": "RON"
    })
    await client.post("/api/procedures", headers=headers, json={
        "code": "SEARCH-002",
        "name": "Extraction",
        "category": "Surgery",
        "default_duration_minutes": 45,
        "base_price": "300.00",
        "currency": "RON"
    })
    await client.post("/api/procedures", headers=headers, json={
        "code": "SEARCH-003",
        "name": "Cleaning",
        "category": "Hygiene",
        "default_duration_minutes": 30,
        "base_price": "100.00",
        "currency": "RON",
        "is_active": False
    })

    # Search by name (across all active states)
    search_resp = await client.get("/api/procedures", headers=headers, params={"search": "Root"})
    assert search_resp.status_code == 200
    assert len(search_resp.json()) == 1
    assert search_resp.json()[0]["name"] == "Root Canal"

    # Search by category
    search_resp = await client.get("/api/procedures", headers=headers, params={"search": "Surgery"})
    assert search_resp.status_code == 200
    assert len(search_resp.json()) == 1
    assert search_resp.json()[0]["name"] == "Extraction"

    # Filter active only
    active_resp = await client.get("/api/procedures", headers=headers, params={"active": True})
    assert active_resp.status_code == 200
    results = active_resp.json()
    assert len(results) == 2
    assert all(p["is_active"] for p in results)

    # Filter inactive only
    inactive_resp = await client.get("/api/procedures", headers=headers, params={"active": False})
    assert inactive_resp.status_code == 200
    assert len(inactive_resp.json()) == 1
    assert all(not p["is_active"] for p in inactive_resp.json())

    # Include all (no active filter - omit the parameter)
    all_resp = await client.get("/api/procedures", headers=headers)
    assert all_resp.status_code == 200
    assert len(all_resp.json()) == 3


@pytest.mark.asyncio
async def test_procedure_price_roundtrip(client):
    tokens = await register(client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    # Create procedure with exact decimal price
    create_resp = await client.post("/api/procedures", headers=headers, json={
        "code": "ROUND-001",
        "name": "Round Trip Test",
        "default_duration_minutes": 30,
        "base_price": "123.45",
        "currency": "RON"
    })
    assert create_resp.status_code == 201
    procedure = create_resp.json()
    assert procedure["base_price"] == "123.45"

    # Update with same exact price
    update_resp = await client.patch(f"/api/procedures/{procedure['id']}", headers=headers, json={
        "base_price": "123.45"
    })
    assert update_resp.status_code == 200
    assert update_resp.json()["base_price"] == "123.45"

    # Update with different exact price
    update_resp = await client.patch(f"/api/procedures/{procedure['id']}", headers=headers, json={
        "base_price": "999.99"
    })
    assert update_resp.status_code == 200
    assert update_resp.json()["base_price"] == "999.99"


@pytest.mark.asyncio
async def test_procedure_not_found(client):
    tokens = await register(client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    # Get non-existent procedure
    resp = await client.get("/api/procedures/00000000-0000-0000-0000-000000000000", headers=headers)
    assert resp.status_code == 404

    # Update non-existent procedure
    resp = await client.patch("/api/procedures/00000000-0000-0000-0000-000000000000", headers=headers, json={
        "name": "Updated"
    })
    assert resp.status_code == 404

    # Deactivate non-existent procedure
    resp = await client.post("/api/procedures/00000000-0000-0000-0000-000000000000/deactivate", headers=headers)
    assert resp.status_code == 404

    # Activate non-existent procedure
    resp = await client.post("/api/procedures/00000000-0000-0000-0000-000000000000/activate", headers=headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_procedure_activate_already_active(client):
    tokens = await register(client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    # Create active procedure
    create_resp = await client.post("/api/procedures", headers=headers, json={
        "code": "ACT-001",
        "name": "Active Test",
        "default_duration_minutes": 30,
        "base_price": "100.00",
        "currency": "RON"
    })
    procedure = create_resp.json()

    # Try to activate already active procedure
    resp = await client.post(f"/api/procedures/{procedure['id']}/activate", headers=headers)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_procedure_deactivate_already_inactive(client):
    tokens = await register(client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    # Create and deactivate procedure
    create_resp = await client.post("/api/procedures", headers=headers, json={
        "code": "INACT-001",
        "name": "Inactive Test",
        "default_duration_minutes": 30,
        "base_price": "100.00",
        "currency": "RON"
    })
    procedure = create_resp.json()
    await client.post(f"/api/procedures/{procedure['id']}/deactivate", headers=headers)

    # Try to deactivate already inactive procedure
    resp = await client.post(f"/api/procedures/{procedure['id']}/deactivate", headers=headers)
    assert resp.status_code == 409
