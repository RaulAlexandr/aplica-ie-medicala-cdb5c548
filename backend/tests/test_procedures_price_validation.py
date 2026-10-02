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


async def register(client):
    response = await client.post("/api/auth/register", json={"clinic_name": "Test Clinic", "full_name": "Test Manager", "email": "test@example.com", "password": "correct horse battery staple"})
    assert response.status_code == 201
    return response.json()


@pytest.mark.asyncio
async def test_price_validation_json_number_int(client):
    """JSON number (int) should be rejected with 422."""
    tokens = await register(client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    resp = await client.post("/api/procedures", headers=headers, json={
        "code": "TEST-001",
        "name": "Test",
        "default_duration_minutes": 30,
        "base_price": 100,
        "currency": "RON"
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_price_validation_json_number_float(client):
    """JSON number (float) should be rejected with 422."""
    tokens = await register(client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    resp = await client.post("/api/procedures", headers=headers, json={
        "code": "TEST-002",
        "name": "Test",
        "default_duration_minutes": 30,
        "base_price": 100.50,
        "currency": "RON"
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_price_validation_malformed_string(client):
    """Malformed string should be rejected with 422."""
    tokens = await register(client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    resp = await client.post("/api/procedures", headers=headers, json={
        "code": "TEST-003",
        "name": "Test",
        "default_duration_minutes": 30,
        "base_price": "abc",
        "currency": "RON"
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_price_validation_non_finite_infinity(client):
    """Infinity should be rejected with 422."""
    tokens = await register(client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    resp = await client.post("/api/procedures", headers=headers, json={
        "code": "TEST-004",
        "name": "Test",
        "default_duration_minutes": 30,
        "base_price": "Infinity",
        "currency": "RON"
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_price_validation_non_finite_nan(client):
    """NaN should be rejected with 422."""
    tokens = await register(client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    resp = await client.post("/api/procedures", headers=headers, json={
        "code": "TEST-005",
        "name": "Test",
        "default_duration_minutes": 30,
        "base_price": "NaN",
        "currency": "RON"
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_price_validation_empty_string(client):
    """Empty string should be rejected with 422."""
    tokens = await register(client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    resp = await client.post("/api/procedures", headers=headers, json={
        "code": "TEST-006",
        "name": "Test",
        "default_duration_minutes": 30,
        "base_price": "",
        "currency": "RON"
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_price_validation_too_many_decimals(client):
    """Too many decimal places should be rejected with 422."""
    tokens = await register(client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    resp = await client.post("/api/procedures", headers=headers, json={
        "code": "TEST-007",
        "name": "Test",
        "default_duration_minutes": 30,
        "base_price": "10.123",
        "currency": "RON"
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_price_validation_negative(client):
    """Negative price should be rejected with 422."""
    tokens = await register(client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    resp = await client.post("/api/procedures", headers=headers, json={
        "code": "TEST-008",
        "name": "Test",
        "default_duration_minutes": 30,
        "base_price": "-10.00",
        "currency": "RON"
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_price_validation_update_with_json_number(client):
    """Update with JSON number should be rejected with 422."""
    tokens = await register(client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    create_resp = await client.post("/api/procedures", headers=headers, json={
        "code": "TEST-009",
        "name": "Test",
        "default_duration_minutes": 30,
        "base_price": "100.00",
        "currency": "RON"
    })
    procedure_id = create_resp.json()["id"]
    resp = await client.patch(f"/api/procedures/{procedure_id}", headers=headers, json={
        "base_price": 200
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_price_validation_update_with_malformed(client):
    """Update with malformed string should be rejected with 422."""
    tokens = await register(client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    create_resp = await client.post("/api/procedures", headers=headers, json={
        "code": "TEST-010",
        "name": "Test",
        "default_duration_minutes": 30,
        "base_price": "100.00",
        "currency": "RON"
    })
    procedure_id = create_resp.json()["id"]
    resp = await client.patch(f"/api/procedures/{procedure_id}", headers=headers, json={
        "base_price": "not-a-number"
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_price_validation_valid_string_price(client):
    """Valid string price should be accepted."""
    tokens = await register(client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    resp = await client.post("/api/procedures", headers=headers, json={
        "code": "TEST-011",
        "name": "Test",
        "default_duration_minutes": 30,
        "base_price": "123.45",
        "currency": "RON"
    })
    assert resp.status_code == 201
    assert resp.json()["base_price"] == "123.45"
