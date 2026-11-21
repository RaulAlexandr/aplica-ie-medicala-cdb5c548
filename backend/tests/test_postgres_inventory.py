"""PostgreSQL locking and idempotency proof for T17 stock movements."""
import asyncio
import os
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import get_db
from app.main import app

pytestmark=pytest.mark.postgres

@pytest.fixture
async def pg_client():
    url=os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url or not url.startswith("postgresql+"):pytest.skip("PostgreSQL CI URL required")
    engine=create_async_engine(url);sessions=async_sessionmaker(engine,expire_on_commit=False)
    async def override():
        async with sessions() as s:yield s
    app.dependency_overrides[get_db]=override
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url="http://test") as c:yield c
    finally:app.dependency_overrides.clear();await engine.dispose()

async def stock(c):
    reg=await c.post("/api/auth/register",json={"clinic_name":"Clinică PG inventar fictiv","full_name":"Manager PG","email":f"{uuid4().hex}@example.com","password":"correct horse battery staple"});assert reg.status_code==201,reg.text
    h={"Authorization":f"Bearer {reg.json()['access_token']}"}
    item=(await c.post("/api/inventory/items",headers=h,json={"name":"Articol PG fictiv","unit":"buc","minimum_quantity":"0"})).json()["id"]
    a=(await c.post("/api/inventory/locations",headers=h,json={"name":"Depozit PG fictiv"})).json()["id"]
    b=(await c.post("/api/inventory/locations",headers=h,json={"name":"Cabinet PG fictiv"})).json()["id"]
    lot=(await c.post("/api/inventory/lots",headers=h,json={"item_id":item,"code":f"LOT-{uuid4().hex[:8]}"})).json()["id"]
    receipt=await c.post("/api/inventory/movements/receipt",headers=h,json={"item_id":item,"lot_id":lot,"location_id":a,"quantity":"10","client_request_id":str(uuid4())});assert receipt.status_code==201,receipt.text
    return h,item,lot,a,b

@pytest.mark.asyncio
async def test_postgres_serializes_competing_consumption(pg_client):
    c=pg_client;h,item,lot,loc,_=await stock(c);ready=asyncio.Event();lock=asyncio.Lock();count=0
    url=os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL");engine=create_async_engine(url);sessions=async_sessionmaker(engine,expire_on_commit=False)
    async def barrier_override():
        nonlocal count
        async with sessions() as s:
            async with lock:
                count+=1
                if count==2:ready.set()
            await asyncio.wait_for(ready.wait(),10);yield s
    base_override=app.dependency_overrides[get_db];app.dependency_overrides[get_db]=barrier_override
    try:
        async def consume():return await asyncio.wait_for(c.post("/api/inventory/movements/consumption",headers=h,json={"item_id":item,"lot_id":lot,"location_id":loc,"quantity":"7","client_request_id":str(uuid4())}),15)
        results=await asyncio.gather(consume(),consume())
        assert sorted(r.status_code for r in results)==[201,409],[r.text for r in results]
        assert count==2
    finally:app.dependency_overrides[get_db]=base_override;await engine.dispose()

@pytest.mark.asyncio
async def test_postgres_serializes_transfer_and_retry_returns_same_transfer(pg_client):
    c=pg_client;h,item,lot,source,dest=await stock(c);ready=asyncio.Event();lock=asyncio.Lock();count=0
    url=os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL");engine=create_async_engine(url);sessions=async_sessionmaker(engine,expire_on_commit=False)
    async def barrier_override():
        nonlocal count
        async with sessions() as s:
            async with lock:
                count+=1
                if count==2:ready.set()
            await asyncio.wait_for(ready.wait(),10);yield s
    base_override=app.dependency_overrides[get_db];app.dependency_overrides[get_db]=barrier_override
    try:
        bodies=[{"item_id":item,"lot_id":lot,"location_id":source,"destination_location_id":dest,"quantity":"7","client_request_id":str(uuid4())} for _ in range(2)]
        async def transfer(body):return await asyncio.wait_for(c.post("/api/inventory/movements/transfer",headers=h,json=body),15)
        results=await asyncio.gather(*(transfer(x) for x in bodies));assert sorted(r.status_code for r in results)==[201,409],[r.text for r in results]
        winner=next((r,b) for r,b in zip(results,bodies) if r.status_code==201);first,body=winner
        app.dependency_overrides[get_db]=base_override
    finally:
        app.dependency_overrides[get_db]=base_override;await engine.dispose()
    retry=await c.post("/api/inventory/movements/transfer",headers=h,json=body)
    assert retry.status_code==201 and retry.json()["transfer_id"]==first.json()["transfer_id"]
