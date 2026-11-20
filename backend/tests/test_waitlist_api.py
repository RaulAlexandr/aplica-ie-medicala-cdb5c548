from datetime import date, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import (
    Appointment,
    Base,
    RecallHistory,
    User,
    WaitlistEntry,
    WaitlistOffer,
    get_db,
    utcnow,
)
from app.main import app


@pytest.fixture
async def ctx():
    engine=create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as c: await c.run_sync(Base.metadata.create_all)
    sessions=async_sessionmaker(engine,expire_on_commit=False)
    async def override():
        async with sessions() as s: yield s
    app.dependency_overrides[get_db]=override
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url="http://test") as client: yield client,sessions
    finally: app.dependency_overrides.clear();await engine.dispose()

async def register(c,email):
    r=await c.post("/api/auth/register",json={"clinic_name":"Clinic fake","full_name":"Manager fake","email":email,"password":"correct horse battery staple"});assert r.status_code==201
    return {"Authorization":f"Bearer {r.json()['access_token']}"}

@pytest.mark.asyncio
async def test_matching_offer_confirmation_and_foreign_clinic(ctx):
    c,sessions=ctx;h=await register(c,"waitlist@example.com");other=await register(c,"foreign@example.com")
    patient=(await c.post("/api/patients",headers=h,json={"first_name":"Fictiv","last_name":"Patient"})).json()
    room=(await c.post("/api/rooms",headers=h,json={"name":"Room fake"})).json()
    slot=(date.today()+timedelta(days=10));weekday=slot.weekday()
    entry=(await c.post("/api/waitlist/entries",headers=h,json={"patient_id":patient["id"],"desired_service":"Control fictiv","desired_duration_minutes":30,"windows":[{"weekday":weekday,"start":"10:00:00","end":"12:00:00"}],"valid_from":slot.isoformat(),"valid_until":slot.isoformat(),"priority":4})).json()
    slot_start=f"{slot.isoformat()}T10:30:00+02:00"
    params={"doctor_id":(await c.get("/api/auth/me",headers=h)).json()["id"],"room_id":room["id"],"starts_at":slot_start,"duration_minutes":45}
    match=await c.post("/api/waitlist/slots/candidates",headers=h,json=params);assert match.status_code==200 and [x["id"] for x in match.json()]==[entry["id"]]
    short=await c.post("/api/waitlist/slots/candidates",headers=h,json={**params,"duration_minutes":20});assert short.status_code==200 and short.json()==[]
    outside=await c.post("/api/waitlist/slots/candidates",headers=h,json={**params,"starts_at":f"{slot.isoformat()}T11:30:00+02:00"});assert outside.status_code==200 and outside.json()==[]
    async with sessions() as s:
        s.add(Appointment(clinic_id=patient["clinic_id"],patient_id=patient["id"],doctor_id=params["doctor_id"],room_id=room["id"],starts_at=datetime.fromisoformat(slot_start),ends_at=datetime.fromisoformat(slot_start)+timedelta(minutes=30),duration_minutes=30,status="scheduled",appointment_type="Existing fake"));await s.commit()
    blocked=await c.post("/api/waitlist/slots/candidates",headers=h,json=params);assert blocked.status_code==200 and blocked.json()==[]
    async with sessions() as s:
        appt=(await s.scalars(select(Appointment))).first();await s.delete(appt);await s.commit()
    offer=await c.post(f"/api/waitlist/entries/{entry['id']}/offers",headers=h,json=params);assert offer.status_code==201
    duplicate=await c.post(f"/api/waitlist/entries/{entry['id']}/offers",headers=h,json=params);assert duplicate.status_code==409
    async with sessions() as s:
        persisted=await s.get(WaitlistEntry,entry["id"]);persisted.status="active";await s.commit()
    database_guard=await c.post(f"/api/waitlist/entries/{entry['id']}/offers",headers=h,json=params);assert database_guard.status_code==409
    hidden=await c.post(f"/api/waitlist/offers/{offer.json()['id']}/confirm",headers=other);assert hidden.status_code==404
    async with sessions() as s:
        old=await s.get(WaitlistOffer,offer.json()["id"]);old.expires_at=utcnow()-timedelta(minutes=1);await s.commit()
    expired=await c.post(f"/api/waitlist/offers/{offer.json()['id']}/confirm",headers=h);assert expired.status_code==409
    params["starts_at"]=f"{slot.isoformat()}T11:00:00+02:00"
    second=await c.post(f"/api/waitlist/entries/{entry['id']}/offers",headers=h,json=params);assert second.status_code==201
    booked=await c.post(f"/api/waitlist/offers/{second.json()['id']}/confirm",headers=h);assert booked.status_code==201 and booked.json()["patient_id"]==patient["id"]
    async with sessions() as s: assert len((await s.scalars(select(Appointment))).all())==1

@pytest.mark.asyncio
async def test_recall_history_and_expired_offer(ctx):
    c,sessions=ctx;h=await register(c,"recalls@example.com")
    patient=(await c.post("/api/patients",headers=h,json={"first_name":"Fictiv","last_name":"Patient"})).json()
    recall=await c.post("/api/waitlist/recalls",headers=h,json={"patient_id":patient["id"],"due_date":date.today().isoformat(),"reason":"Control periodic fictiv"});assert recall.status_code==201
    acted=await c.post(f"/api/waitlist/recalls/{recall.json()['id']}/actions",headers=h,json={"action":"contacted"})
    assert acted.status_code==200 and acted.json()["history"][0]["action"]=="contacted"
    async with sessions() as s: assert len((await s.scalars(select(RecallHistory))).all())==1
    identity=(await c.get("/api/auth/me",headers=h)).json()
    async with sessions() as s:
        u=await s.get(User,identity["id"]);u.role="assistant";await s.commit()
    assert (await c.get("/api/waitlist/recalls",headers=h)).status_code==200
    assert (await c.post(f"/api/waitlist/recalls/{recall.json()['id']}/actions",headers=h,json={"action":"refused"})).status_code==403
