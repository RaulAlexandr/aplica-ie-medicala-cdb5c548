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

@pytest.mark.asyncio
async def test_duplicate_item_location_lot_and_rename_return_409(ctx):
    c,_=ctx;h=await register(c,"inv-duplicates@example.com")
    item1=await c.post("/api/inventory/items",headers=h,json={"name":"Articol duplicat fictiv","unit":"buc","minimum_quantity":"0"});assert item1.status_code==201
    item2=await c.post("/api/inventory/items",headers=h,json={"name":"Alt articol fictiv","unit":"buc","minimum_quantity":"0"});assert item2.status_code==201
    duplicate_item=await c.post("/api/inventory/items",headers=h,json={"name":"Articol duplicat fictiv","unit":"buc","minimum_quantity":"0"});assert duplicate_item.status_code==409 and "articol" in duplicate_item.text.lower()
    rename_item=await c.patch(f"/api/inventory/items/{item2.json()['id']}",headers=h,json={"name":"Articol duplicat fictiv","unit":"buc","minimum_quantity":"0","is_active":True});assert rename_item.status_code==409
    loc1=await c.post("/api/inventory/locations",headers=h,json={"name":"Locație duplicată fictivă"});assert loc1.status_code==201
    loc2=await c.post("/api/inventory/locations",headers=h,json={"name":"Altă locație fictivă"});assert loc2.status_code==201
    duplicate_location=await c.post("/api/inventory/locations",headers=h,json={"name":"Locație duplicată fictivă"});assert duplicate_location.status_code==409 and "loca" in duplicate_location.text.lower()
    rename_location=await c.patch(f"/api/inventory/locations/{loc2.json()['id']}",headers=h,json={"name":"Locație duplicată fictivă","is_active":True});assert rename_location.status_code==409
    lot=await c.post("/api/inventory/lots",headers=h,json={"item_id":item1.json()["id"],"code":"COD-LOT-FICTIV"});assert lot.status_code==201
    duplicate_lot=await c.post("/api/inventory/lots",headers=h,json={"item_id":item1.json()["id"],"code":"COD-LOT-FICTIV"});assert duplicate_lot.status_code==409 and "lot" in duplicate_lot.text.lower()

@pytest.mark.asyncio
async def test_inventory_names_units_and_lot_codes_trim_and_reject_blank(ctx):
    c,_=ctx;h=await register(c,"inv-whitespace@example.com")
    blank_name=await c.post("/api/inventory/items",headers=h,json={"name":"   ","unit":"buc","minimum_quantity":"0"});assert blank_name.status_code==422
    blank_unit=await c.post("/api/inventory/items",headers=h,json={"name":"Material fictiv","unit":"   ","minimum_quantity":"0"});assert blank_unit.status_code==422
    created=await c.post("/api/inventory/items",headers=h,json={"name":"  Material fictiv  ","unit":" buc ","minimum_quantity":"0"});assert created.status_code==201
    view=(await c.get("/api/inventory",headers=h)).json();assert view["items"][0]["name"]=="Material fictiv" and view["items"][0]["unit"]=="buc"
    blank_location=await c.post("/api/inventory/locations",headers=h,json={"name":"  "});assert blank_location.status_code==422
    location=await c.post("/api/inventory/locations",headers=h,json={"name":"  Depozit fictiv  "});assert location.status_code==201
    assert (await c.patch(f"/api/inventory/locations/{location.json()['id']}",headers=h,json={"name":"   ","is_active":True})).status_code==422
    blank_lot=await c.post("/api/inventory/lots",headers=h,json={"item_id":created.json()["id"],"code":" "});assert blank_lot.status_code==422
    lot=await c.post("/api/inventory/lots",headers=h,json={"item_id":created.json()["id"],"code":"  LOT-ALB  "});assert lot.status_code==201
    assert (await c.get("/api/inventory",headers=h)).json()["balances"][0]["lot"]=="LOT-ALB"
    assert (await c.patch(f"/api/inventory/items/{created.json()['id']}",headers=h,json={"name":"  ","unit":"buc","minimum_quantity":"0","is_active":True})).status_code==422

@pytest.mark.asyncio
async def test_inventory_retry_normalizes_decimal_scale(ctx):
    c,_=ctx;h=await register(c,"inv-decimal-retry@example.com");item,lot,loc,_=await setup_stock(c,h,minimum="0")
    body=movement(item,lot,loc,"10");first=await c.post("/api/inventory/movements/receipt",headers=h,json=body);assert first.status_code==201
    retry=await c.post("/api/inventory/movements/receipt",headers=h,json={**body,"quantity":"10.000"})
    assert retry.status_code==201 and retry.json()["id"]==first.json()["id"]
    changed=await c.post("/api/inventory/movements/receipt",headers=h,json={**body,"quantity":"10.001"});assert changed.status_code==409

@pytest.mark.asyncio
async def test_movement_and_correction_signatures_are_sha256(ctx):
    c,s=ctx;h=await register(c,"inv-signature@example.com");item,lot,loc,_=await setup_stock(c,h,minimum="0")
    body=movement(item,lot,loc,"10",reason="ă"*500);created=await c.post("/api/inventory/movements/receipt",headers=h,json=body);assert created.status_code==201,created.text
    reverse=await c.post(f"/api/inventory/movements/{created.json()['id']}/reverse",headers=h,json={"client_request_id":str(uuid4()),"reason":"ș"*500});assert reverse.status_code==200,reverse.text
    async with s() as db:
        rows=(await db.scalars(select(InventoryMovement).order_by(InventoryMovement.created_at))).all()
        assert len(rows)==2 and all(len(row.signature)==64 for row in rows)

@pytest.mark.asyncio
async def test_cannot_deactivate_item_or_location_with_stock(ctx):
    c,_=ctx;h=await register(c,"inv-deactivation@example.com");item,lot,loc,_=await setup_stock(c,h,minimum="0")
    receipt=await c.post("/api/inventory/movements/receipt",headers=h,json=movement(item,lot,loc,"5"));assert receipt.status_code==201
    item_view=(await c.get("/api/inventory",headers=h)).json()["items"][0]
    location_view=next(x for x in (await c.get("/api/inventory",headers=h)).json()["locations"] if x["id"]==loc)
    disable_item=await c.patch(f"/api/inventory/items/{item}",headers=h,json={**item_view,"is_active":False});assert disable_item.status_code==409 and "stoc" in disable_item.text.lower()
    disable_location=await c.patch(f"/api/inventory/locations/{loc}",headers=h,json={"name":location_view["name"],"is_active":False});assert disable_location.status_code==409 and "stoc" in disable_location.text.lower()
    consume=await c.post("/api/inventory/movements/consumption",headers=h,json=movement(item,lot,loc,"5"));assert consume.status_code==201
    balances=(await c.get("/api/inventory",headers=h)).json()["balances"]
    assert next(x for x in balances if x["location_id"]==loc)["quantity"]=="0.000"
    assert (await c.patch(f"/api/inventory/items/{item}",headers=h,json={**item_view,"is_active":False})).status_code==200
    disable_empty_location=await c.patch(f"/api/inventory/locations/{loc}",headers=h,json={"name":location_view["name"],"is_active":False});assert disable_empty_location.status_code==200,disable_empty_location.text

@pytest.mark.asyncio
async def test_inactive_item_does_not_raise_low_stock_alert(ctx):
    c,_=ctx;h=await register(c,"inv-inactive-low@example.com");item,_,_,_=await setup_stock(c,h,minimum="5")
    data=(await c.get("/api/inventory",headers=h)).json();record=next(x for x in data["items"] if x["id"]==item)
    deactivated=await c.patch(f"/api/inventory/items/{item}",headers=h,json={"name":record["name"],"unit":record["unit"],"minimum_quantity":record["minimum_quantity"],"is_active":False});assert deactivated.status_code==200
    data=(await c.get("/api/inventory",headers=h)).json();record=next(x for x in data["items"] if x["id"]==item)
    assert record["below_minimum"] is False
    assert all(not x["below_minimum"] for x in data["balances"] if x["item_id"]==item)
