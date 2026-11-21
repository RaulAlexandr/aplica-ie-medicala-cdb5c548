from datetime import date, timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.service import create_access_token
from app.database import Base, User, get_db
from app.inventory.models import InventoryMovement
from app.main import app

PASSWORD="correct horse battery staple"

@pytest.fixture
async def ctx():
    engine=create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)
    sessions=async_sessionmaker(engine,expire_on_commit=False)
    async def override():
        async with sessions() as session: yield session
    app.dependency_overrides[get_db]=override
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url="http://test") as client: yield client,sessions
    finally:
        app.dependency_overrides.clear();await engine.dispose()

async def register(c,email):
    r=await c.post("/api/auth/register",json={"clinic_name":"Clinică fictivă inventar","full_name":"Manager fictiv","email":email,"password":PASSWORD})
    assert r.status_code==201,r.text
    return {"Authorization":f"Bearer {r.json()['access_token']}"}

async def add_role(sessions,clinic_id,role):
    u=User(clinic_id=clinic_id,email=f"{role}-{uuid4().hex}@example.com",password_hash="unused",full_name=role,role=role,is_active=True)
    async with sessions() as s:s.add(u);await s.commit();return {"Authorization":f"Bearer {create_access_token(u)}"}

async def setup_stock(c,h,minimum="4",expires=None):
    tag=uuid4().hex[:8]
    item=await c.post("/api/inventory/items",headers=h,json={"name":f"Mănuși fictive {tag}","unit":"cutie","minimum_quantity":minimum});assert item.status_code==201,item.text
    l1=await c.post("/api/inventory/locations",headers=h,json={"name":f"Depozit fictiv {tag}"});assert l1.status_code==201,l1.text
    l2=await c.post("/api/inventory/locations",headers=h,json={"name":f"Cabinet fictiv {tag}"});assert l2.status_code==201,l2.text
    lot=await c.post("/api/inventory/lots",headers=h,json={"item_id":item.json()["id"],"code":f"LOT-{tag}","expires_on":expires});assert lot.status_code==201,lot.text
    return item.json()["id"],lot.json()["id"],l1.json()["id"],l2.json()["id"]

def movement(item,lot,loc,qty="10",**kw):
    return {"item_id":item,"lot_id":lot,"location_id":loc,"quantity":qty,"client_request_id":str(uuid4()),**kw}

@pytest.mark.asyncio
async def test_permissions_tenant_isolation_views_and_expiry(ctx):
    c,s=ctx;h=await register(c,"inv-manager@example.com");me=(await c.get("/api/auth/me",headers=h)).json()
    doctor=await add_role(s,me["clinic_id"],"doctor");assistant=await add_role(s,me["clinic_id"],"assistant");reception=await add_role(s,me["clinic_id"],"reception");foreign=await register(c,"inv-foreign@example.com")
    item,lot,loc,_dest=await setup_stock(c,h,expires=(date.today()+timedelta(days=10)).isoformat())
    assert (await c.post("/api/inventory/items",headers=doctor,json={"name":"No","unit":"u","minimum_quantity":"0"})).status_code==403
    assert (await c.post("/api/inventory/movements/receipt",headers=doctor,json=movement(item,lot,loc))).status_code==403
    assert (await c.get("/api/inventory",headers=foreign)).json()["items"]==[]
    assert (await c.post("/api/inventory/movements/receipt",headers=reception,json=movement(item,lot,loc))).status_code==403
    received=await c.post("/api/inventory/movements/receipt",headers=h,json=movement(item,lot,loc));assert received.status_code==201,received.text
    consumed=await c.post("/api/inventory/movements/consumption",headers=doctor,json=movement(item,lot,loc,"1"));assert consumed.status_code==201,consumed.text
    consumed_assistant=await c.post("/api/inventory/movements/consumption",headers=assistant,json=movement(item,lot,loc,"1"));assert consumed_assistant.status_code==201,consumed_assistant.text
    view=(await c.get("/api/inventory",headers=h)).json();assert view["balances"][0]["expiring_soon"] is True
    other=await setup_stock(c, h, minimum="2")
    zero=await c.get("/api/inventory",headers=h);assert any(x["item_id"]==other[0] and x["below_minimum"] for x in zero.json()["balances"])
    expired=await setup_stock(c,h,expires=(date.today()-timedelta(days=1)).isoformat())
    denied=await c.post("/api/inventory/movements/consumption",headers=doctor,json=movement(expired[0],expired[1],expired[2],"1"));assert denied.status_code==409 and "expirat" in denied.text

@pytest.mark.asyncio
async def test_adjustment_transfer_retry_and_linked_atomic_correction(ctx):
    c,s=ctx;h=await register(c,"inv-flow@example.com");item,lot,loc,dest=await setup_stock(c,h,minimum="0")
    adjust=movement(item,lot,loc,"10",reason="Inventar fictiv",direction="increase")
    assert (await c.post("/api/inventory/movements/adjustment",headers=h,json=adjust)).status_code==201
    decrease=movement(item,lot,loc,"3",reason="Corecție numărare fictivă",direction="decrease")
    assert (await c.post("/api/inventory/movements/adjustment",headers=h,json=decrease)).status_code==201
    tr=movement(item,lot,loc,"4",destination_location_id=dest);first=await c.post("/api/inventory/movements/transfer",headers=h,json=tr);assert first.status_code==201,first.text
    retry=await c.post("/api/inventory/movements/transfer",headers=h,json=tr);assert retry.status_code==201 and retry.json()["transfer_id"]==first.json()["transfer_id"]
    conflict=await c.post("/api/inventory/movements/transfer",headers=h,json={**tr,"reason":"alt motiv"});assert conflict.status_code==409
    conflict_dest=await c.post("/api/inventory/movements/transfer",headers=h,json={**tr,"destination_location_id":loc});assert conflict_dest.status_code==409
    reverse_body={"client_request_id":str(uuid4()),"reason":"Transfer greșit fictiv"}
    reversed=await c.post(f"/api/inventory/movements/{first.json()['id']}/reverse",headers=h,json=reverse_body);assert reversed.status_code==200,reversed.text
    reverse_retry=await c.post(f"/api/inventory/movements/{first.json()['id']}/reverse",headers=h,json=reverse_body);assert reverse_retry.status_code==200 and reverse_retry.json()["id"]==reversed.json()["id"]
    again=await c.post(f"/api/inventory/movements/{first.json()['id']}/reverse",headers=h,json={"client_request_id":str(uuid4()),"reason":"A doua corecție fictivă"});assert again.status_code==409
    view=(await c.get("/api/inventory",headers=h)).json()["balances"]
    totals={x["location_id"]:x["quantity"] for x in view if x["item_id"]==item}
    assert totals[loc]=="7.000" and totals.get(dest,"0")=="0.000"
    entries=await c.get("/api/inventory/movements",headers=h,params={"item_id":item});assert len(entries.json())==6
    async with s() as db:
        rows=(await db.scalars(select(InventoryMovement))).all();assert any(x.reverses_movement_id for x in rows)

@pytest.mark.asyncio
async def test_retry_and_negative_adjustment(ctx):
    c,_=ctx;h=await register(c,"inv-negative@example.com");item,lot,loc,_=await setup_stock(c,h,minimum="0")
    dec=movement(item,lot,loc,"1",reason="Numărare fictivă",direction="decrease")
    assert (await c.post("/api/inventory/movements/adjustment",headers=h,json=dec)).status_code==409
    receipt=movement(item,lot,loc,"2");one=await c.post("/api/inventory/movements/receipt",headers=h,json=receipt);two=await c.post("/api/inventory/movements/receipt",headers=h,json=receipt)
    assert one.status_code==201 and two.json()["id"]==one.json()["id"]
