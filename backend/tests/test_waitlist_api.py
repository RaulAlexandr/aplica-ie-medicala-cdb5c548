from datetime import date, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import (
    Appointment,
    Base,
    Clinic,
    Patient,
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

@pytest.mark.asyncio
async def test_matching_order_doctor_any_and_expired_validity(ctx):
    c, _ = ctx
    h = await register(c, "matching@example.com")
    patient = (await c.post("/api/patients", headers=h, json={"first_name":"Fictiv", "last_name":"Match"})).json()
    identity = (await c.get("/api/auth/me", headers=h)).json()
    doctor_id = identity["id"]
    day = date.today() + timedelta(days=10)
    common = {"patient_id":patient["id"], "desired_duration_minutes":30,
              "windows":[{"weekday":day.weekday(),"start":"10:00:00","end":"11:00:00"}],
              "valid_from":day.isoformat(),"valid_until":day.isoformat()}
    ids=[]
    for priority, preferred in [(2, None),(5, "00000000-0000-0000-0000-000000000000"),(5, None)]:
        payload={**common,"priority":priority}
        if preferred: payload["preferred_doctor_id"]=preferred
        result=await c.post("/api/waitlist/entries",headers=h,json=payload)
        if preferred: assert result.status_code==404
        else: ids.append(result.json()["id"])
    # A valid preferred doctor entry and an expired entry are added directly to isolate matching.
    async with ctx[1]() as s:
        from app.database import Clinic
        clinic=await s.scalar(select(Clinic))
        other_doctor=User(clinic_id=clinic.id,email="other-doctor@example.com",password_hash="test",full_name="Doctor fake",role="doctor",is_active=True)
        s.add(other_doctor);await s.flush()
        preferred=WaitlistEntry(clinic_id=clinic.id,patient_id=patient["id"],desired_duration_minutes=30,
            preferred_doctor_id=doctor_id,windows=common["windows"],valid_from=day,valid_until=day,priority=5,status="active",created_by_id=identity["id"],created_at=datetime.now().astimezone()-timedelta(days=1))
        wrong_doctor=WaitlistEntry(clinic_id=clinic.id,patient_id=patient["id"],desired_duration_minutes=30,
            preferred_doctor_id=other_doctor.id,windows=common["windows"],valid_from=day,valid_until=day,priority=99,status="active",created_by_id=identity["id"])
        s.add_all([preferred,wrong_doctor])
        future_expiry=WaitlistEntry(clinic_id=clinic.id,patient_id=patient["id"],desired_duration_minutes=30,
            windows=common["windows"],valid_from=day-timedelta(days=3),valid_until=day-timedelta(days=2),priority=100,status="active",created_by_id=identity["id"])
        genuinely_expired=WaitlistEntry(clinic_id=clinic.id,patient_id=patient["id"],desired_duration_minutes=30,
            windows=common["windows"],valid_from=date.today()-timedelta(days=3),valid_until=date.today()-timedelta(days=2),priority=101,status="active",created_by_id=identity["id"])
        s.add_all([future_expiry,genuinely_expired]); await s.commit()
    room=(await c.post("/api/rooms",headers=h,json={"name":"Room fake"})).json()
    params={"doctor_id":doctor_id,"room_id":room["id"],"starts_at":f"{day.isoformat()}T10:00:00+02:00","duration_minutes":30}
    result=await c.post("/api/waitlist/slots/candidates",headers=h,json=params)
    assert result.status_code==200
    assert [item["id"] for item in result.json()]==[str(preferred.id),ids[1],ids[0]]
    async with ctx[1]() as s:
        assert (await s.get(WaitlistEntry,future_expiry.id)).status=="active"
        assert (await s.get(WaitlistEntry,genuinely_expired.id)).status=="expired"
    too_short=await c.post("/api/waitlist/slots/candidates",headers=h,json={**params,"duration_minutes":20})
    assert too_short.json()==[]

@pytest.mark.asyncio
async def test_dst_matching_and_overnight_window_policy(ctx):
    c,_=ctx;h=await register(c,"dst@example.com")
    patient=(await c.post("/api/patients",headers=h,json={"first_name":"Fake","last_name":"DST"})).json()
    identity=(await c.get("/api/auth/me",headers=h)).json();room=(await c.post("/api/rooms",headers=h,json={"name":"DST room"})).json()
    entry=await c.post("/api/waitlist/entries",headers=h,json={"patient_id":patient["id"],"desired_duration_minutes":30,
        "windows":[{"weekday":6,"start":"02:00:00","end":"04:30:00"}],"valid_from":"2026-10-25","valid_until":"2026-10-25"})
    assert entry.status_code==201
    matched=await c.post("/api/waitlist/slots/candidates",headers=h,json={"doctor_id":identity["id"],"room_id":room["id"],"starts_at":"2026-10-25T00:30:00+00:00","duration_minutes":30})
    assert matched.status_code==200 and matched.json()[0]["id"]==entry.json()["id"]
    overnight=await c.post("/api/waitlist/entries",headers=h,json={"patient_id":patient["id"],"desired_duration_minutes":30,
        "windows":[{"weekday":6,"start":"23:00:00","end":"01:00:00"}],"valid_from":"2026-10-25","valid_until":"2026-10-26"})
    assert overnight.status_code==422

@pytest.mark.asyncio
async def test_confirmation_records_history_audit_and_duplicate_conflicts(ctx):
    from app.database import AppointmentHistory, AuditEvent
    c,sessions=ctx;h=await register(c,"confirm-audit@example.com")
    patient=(await c.post("/api/patients",headers=h,json={"first_name":"Fake","last_name":"Confirm"})).json()
    room=(await c.post("/api/rooms",headers=h,json={"name":"Confirm room"})).json();doctor=(await c.get("/api/auth/me",headers=h)).json()["id"]
    day=date.today()+timedelta(days=12)
    entry=(await c.post("/api/waitlist/entries",headers=h,json={"patient_id":patient["id"],"desired_duration_minutes":30,"windows":[{"weekday":day.weekday(),"start":"10:00:00","end":"12:00:00"}],"valid_from":day.isoformat(),"valid_until":day.isoformat()})).json()
    body={"doctor_id":doctor,"room_id":room["id"],"starts_at":f"{day}T10:00:00+02:00","duration_minutes":30}
    offer=(await c.post(f"/api/waitlist/entries/{entry['id']}/offers",headers=h,json=body)).json()
    first=await c.post(f"/api/waitlist/offers/{offer['id']}/confirm",headers=h);again=await c.post(f"/api/waitlist/offers/{offer['id']}/confirm",headers=h)
    assert first.status_code==201 and again.status_code==409
    async with sessions() as s:
        assert len((await s.scalars(select(Appointment))).all())==1
        assert len((await s.scalars(select(AppointmentHistory))).all())==1
        assert len((await s.scalars(select(AuditEvent).where(AuditEvent.entity_type=="appointment",AuditEvent.action=="created"))).all())==1
        assert (await s.get(WaitlistEntry,entry["id"])).status=="booked"

@pytest.mark.asyncio
async def test_endpoint_role_matrix(ctx):
    from uuid import uuid4
    c,sessions=ctx;h=await register(c,"role-matrix@example.com")
    identity=(await c.get("/api/auth/me",headers=h)).json()
    room=(await c.post("/api/rooms",headers=h,json={"name":"Role room"})).json()
    day=date.today()+timedelta(days=30);unknown=str(uuid4())
    paths=[
      ("GET","/api/waitlist/entries",None,{"reception","clinic_manager","administrator","assistant"}),
      ("POST","/api/waitlist/entries",{"patient_id":unknown,"desired_duration_minutes":30,"windows":[{"weekday":day.weekday(),"start":"09:00:00","end":"10:00:00"}],"valid_from":day.isoformat(),"valid_until":day.isoformat()},{"reception","clinic_manager","administrator"}),
      ("POST","/api/waitlist/slots/candidates",{"doctor_id":unknown,"room_id":unknown,"starts_at":f"{day}T09:00:00+02:00","duration_minutes":30},{"reception","clinic_manager","administrator"}),
      ("POST",f"/api/waitlist/entries/{unknown}/offers",{"doctor_id":identity["id"],"room_id":room["id"],"starts_at":f"{day}T09:00:00+02:00","duration_minutes":30},{"reception","clinic_manager","administrator"}),
      ("POST",f"/api/waitlist/offers/{unknown}/confirm",None,{"reception","clinic_manager","administrator"}),
      ("POST",f"/api/waitlist/offers/{unknown}/cancel",None,{"reception","clinic_manager","administrator"}),
      ("GET","/api/waitlist/offers",None,{"reception","clinic_manager","administrator","assistant"}),
      ("POST",f"/api/waitlist/entries/{unknown}/cancel",None,{"reception","clinic_manager","administrator"}),
      ("GET","/api/waitlist/recalls",None,{"doctor","reception","clinic_manager","administrator","assistant"}),
      ("GET",f"/api/waitlist/recalls/{unknown}",None,{"doctor","reception","clinic_manager","administrator","assistant"}),
      ("POST","/api/waitlist/recalls",{"patient_id":unknown,"due_date":day.isoformat(),"reason":"Fictiv"},{"doctor","reception","clinic_manager","administrator"}),
      ("POST",f"/api/waitlist/recalls/{unknown}/actions",{"action":"contacted"},{"doctor","reception","clinic_manager","administrator"}),
    ]
    roles=["reception","clinic_manager","administrator","doctor","assistant"]
    for method,path,payload,allowed in paths:
        unauth=await c.request(method,path,json=payload) if payload is not None else await c.request(method,path)
        assert unauth.status_code==401,(method,path,unauth.status_code)
        for role in roles:
            async with sessions() as s:
                user=await s.get(User,identity["id"]);user.role=role;await s.commit()
            response=await c.request(method,path,headers=h,json=payload) if payload is not None else await c.request(method,path,headers=h)
            assert (response.status_code!=403)==(role in allowed),(method,path,role,response.status_code,response.text)
    async with sessions() as s:
        user=await s.get(User,identity["id"]);user.role="clinic_manager";await s.commit()

@pytest.mark.asyncio
async def test_offer_partial_unique_index_and_expiry_releases_interval(ctx):
    c,sessions=ctx;h=await register(c,"offer-index@example.com")
    patient=(await c.post("/api/patients",headers=h,json={"first_name":"Fake","last_name":"Offer"})).json()
    room=(await c.post("/api/rooms",headers=h,json={"name":"Offer room"})).json();doctor=(await c.get("/api/auth/me",headers=h)).json()["id"]
    day=date.today()+timedelta(days=14);payload={"patient_id":patient["id"],"desired_duration_minutes":30,"windows":[{"weekday":day.weekday(),"start":"10:00:00","end":"12:00:00"}],"valid_from":day.isoformat(),"valid_until":day.isoformat()}
    first=(await c.post("/api/waitlist/entries",headers=h,json=payload)).json();second=(await c.post("/api/waitlist/entries",headers=h,json=payload)).json()
    slot={"doctor_id":doctor,"room_id":room["id"],"starts_at":f"{day}T10:00:00+02:00","duration_minutes":30,"expires_in_minutes":1}
    offer=(await c.post(f"/api/waitlist/entries/{first['id']}/offers",headers=h,json=slot)).json()
    async with sessions() as s:
        clinic=await s.scalar(select(Clinic));duplicate=WaitlistOffer(clinic_id=clinic.id,entry_id=second["id"],doctor_id=doctor,room_id=room["id"],starts_at=datetime.fromisoformat(slot["starts_at"]),duration_minutes=30,expires_at=utcnow()+timedelta(minutes=5),status="active",created_by_id=doctor)
        s.add(duplicate)
        with pytest.raises(IntegrityError): await s.commit()
        await s.rollback()
        old=await s.get(WaitlistOffer,offer["id"]);old.expires_at=utcnow()-timedelta(seconds=1);await s.commit()
    released=await c.post(f"/api/waitlist/entries/{second['id']}/offers",headers=h,json=slot)
    assert released.status_code==201
    expired_confirm=await c.post(f"/api/waitlist/offers/{offer['id']}/confirm",headers=h)
    assert expired_confirm.status_code==409

@pytest.mark.asyncio
async def test_cancel_offer_restores_active_entry_and_normal_appointment_conflict(ctx):
    c,sessions=ctx;h=await register(c,"cancel-conflict@example.com")
    patient=(await c.post("/api/patients",headers=h,json={"first_name":"Fake","last_name":"Cancel"})).json();room=(await c.post("/api/rooms",headers=h,json={"name":"Cancel room"})).json();doctor=(await c.get("/api/auth/me",headers=h)).json()["id"]
    day=date.today()+timedelta(days=15);body={"patient_id":patient["id"],"desired_duration_minutes":30,"windows":[{"weekday":day.weekday(),"start":"10:00:00","end":"12:00:00"}],"valid_from":day.isoformat(),"valid_until":day.isoformat()}
    entry=(await c.post("/api/waitlist/entries",headers=h,json=body)).json();slot={"doctor_id":doctor,"room_id":room["id"],"starts_at":f"{day}T10:00:00+02:00","duration_minutes":30}
    offer=(await c.post(f"/api/waitlist/entries/{entry['id']}/offers",headers=h,json=slot)).json()
    assert (await c.post(f"/api/waitlist/entries/{entry['id']}/cancel",headers=h)).json()["status"]=="cancelled"
    async with sessions() as s:
        persisted=await s.get(WaitlistOffer,offer["id"]);assert persisted.status=="cancelled"
    entry2=(await c.post("/api/waitlist/entries",headers=h,json=body)).json();offer2=(await c.post(f"/api/waitlist/entries/{entry2['id']}/offers",headers=h,json={**slot,"starts_at":f"{day}T11:00:00+02:00"})).json()
    async with sessions() as s:
        s.add(Appointment(clinic_id=patient["clinic_id"],patient_id=patient["id"],doctor_id=doctor,room_id=room["id"],starts_at=datetime.fromisoformat(f"{day}T11:00:00+02:00"),ends_at=datetime.fromisoformat(f"{day}T11:30:00+02:00"),duration_minutes=30,status="scheduled",appointment_type="Programare normală"));await s.commit()
    conflict=await c.post(f"/api/waitlist/offers/{offer2['id']}/confirm",headers=h)
    assert conflict.status_code==409
    async with sessions() as s: assert len((await s.scalars(select(Appointment))).all())==1

@pytest.mark.asyncio
async def test_recall_doctor_actions_period_and_terminal_transition(ctx):
    c,sessions=ctx;h=await register(c,"recall-actions@example.com");identity=(await c.get("/api/auth/me",headers=h)).json()
    patient=(await c.post("/api/patients",headers=h,json={"first_name":"Fake","last_name":"Recall"})).json()
    async with sessions() as s:
        user=await s.get(User,identity["id"]);user.role="doctor";await s.commit()
    created=await c.post("/api/waitlist/recalls",headers=h,json={"patient_id":patient["id"],"due_date":(date.today()-timedelta(days=2)).isoformat(),"reason":"Control fictiv"})
    assert created.status_code==201
    recall_id=created.json()["id"]
    due_today=await c.post("/api/waitlist/recalls",headers=h,json={"patient_id":patient["id"],"due_date":date.today().isoformat(),"reason":"Scadent azi fictiv"})
    assert due_today.status_code==201
    assert len((await c.get("/api/waitlist/recalls",headers=h,params={"start":(date.today()-timedelta(days=7)).isoformat(),"end":date.today().isoformat()})).json())==2
    contacted=await c.post(f"/api/waitlist/recalls/{recall_id}/actions",headers=h,json={"action":"contacted"})
    assert contacted.status_code==200 and contacted.json()["history"][-1]["actor_id"]==identity["id"] and contacted.json()["history"][-1]["occurred_at"]
    scheduled=await c.post(f"/api/waitlist/recalls/{recall_id}/actions",headers=h,json={"action":"scheduled"})
    assert scheduled.status_code==200 and scheduled.json()["history"][-1]["actor_id"]==identity["id"] and scheduled.json()["history"][-1]["occurred_at"]
    terminal=await c.post(f"/api/waitlist/recalls/{recall_id}/actions",headers=h,json={"action":"contacted"})
    assert terminal.status_code==409
    refused=await c.post("/api/waitlist/recalls",headers=h,json={"patient_id":patient["id"],"due_date":date.today().isoformat(),"reason":"Alt motiv fictiv"})
    assert refused.status_code==201
    refused_action=await c.post(f"/api/waitlist/recalls/{refused.json()['id']}/actions",headers=h,json={"action":"refused"})
    assert refused_action.status_code==200 and refused_action.json()["history"][-1]["actor_id"]==identity["id"] and refused_action.json()["history"][-1]["occurred_at"]
    async with sessions() as s:
        user=await s.get(User,identity["id"]);user.role="reception";await s.commit()
    reception_recall=await c.post("/api/waitlist/recalls",headers=h,json={"patient_id":patient["id"],"due_date":date.today().isoformat(),"reason":"Rechemare fictivă"})
    assert reception_recall.status_code==201
    async with sessions() as s: assert len((await s.scalars(select(RecallHistory))).all())==3

@pytest.mark.asyncio
async def test_foreign_clinic_ids_are_hidden_for_entries_offers_and_recalls(ctx):
    c,_=ctx;owner=await register(c,"owner-clinic@example.com")
    patient=(await c.post("/api/patients",headers=owner,json={"first_name":"Fake","last_name":"Owner"})).json()
    room=(await c.post("/api/rooms",headers=owner,json={"name":"Owner room"})).json();doctor=(await c.get("/api/auth/me",headers=owner)).json()["id"]
    day=date.today()+timedelta(days=18);entry=(await c.post("/api/waitlist/entries",headers=owner,json={"patient_id":patient["id"],"desired_duration_minutes":30,"windows":[{"weekday":day.weekday(),"start":"10:00:00","end":"12:00:00"}],"valid_from":day.isoformat(),"valid_until":day.isoformat()})).json()
    offer=(await c.post(f"/api/waitlist/entries/{entry['id']}/offers",headers=owner,json={"doctor_id":doctor,"room_id":room["id"],"starts_at":f"{day}T10:00:00+02:00","duration_minutes":30})).json()
    recall=(await c.post("/api/waitlist/recalls",headers=owner,json={"patient_id":patient["id"],"due_date":day.isoformat(),"reason":"Fictiv"})).json()
    foreign=await register(c,"foreign-clinic-2@example.com")
    assert (await c.get("/api/waitlist/entries",headers=foreign)).json()==[]
    assert (await c.get("/api/waitlist/offers",headers=foreign)).json()==[]
    assert (await c.get(f"/api/waitlist/recalls/{recall['id']}",headers=foreign)).status_code==404
    assert (await c.post(f"/api/waitlist/recalls/{recall['id']}/actions",headers=foreign,json={"action":"contacted"})).status_code==404
    assert (await c.post(f"/api/waitlist/entries/{entry['id']}/cancel",headers=foreign)).status_code==404
    assert (await c.post(f"/api/waitlist/offers/{offer['id']}/confirm",headers=foreign)).status_code==404
    assert (await c.post(f"/api/waitlist/offers/{offer['id']}/cancel",headers=foreign)).status_code==404

@pytest.mark.asyncio
async def test_confirmation_rechecks_doctor_unavailability(ctx):
    c,sessions=ctx;h=await register(c,"unavailable-doctor@example.com")
    patient=(await c.post("/api/patients",headers=h,json={"first_name":"Fake","last_name":"Unavailable"})).json();room=(await c.post("/api/rooms",headers=h,json={"name":"Unavailable room"})).json();doctor=(await c.get("/api/auth/me",headers=h)).json()["id"]
    day=date.today()+timedelta(days=21);start=f"{day}T10:00:00+02:00"
    entry=(await c.post("/api/waitlist/entries",headers=h,json={"patient_id":patient["id"],"desired_duration_minutes":30,"windows":[{"weekday":day.weekday(),"start":"10:00:00","end":"12:00:00"}],"valid_from":day.isoformat(),"valid_until":day.isoformat()})).json()
    offer=(await c.post(f"/api/waitlist/entries/{entry['id']}/offers",headers=h,json={"doctor_id":doctor,"room_id":room["id"],"starts_at":start,"duration_minutes":30})).json()
    blocked=await c.post("/api/availability/unavailability",headers=h,json={"resource_type":"doctor","resource_id":doctor,"starts_at":start,"ends_at":f"{day}T10:30:00+02:00","reason":"Program indisponibilitate fictiv"})
    assert blocked.status_code==201
    result=await c.post(f"/api/waitlist/offers/{offer['id']}/confirm",headers=h)
    assert result.status_code==422 and "doctor" in result.json()["detail"]
    async with sessions() as s: assert len((await s.scalars(select(Appointment))).all())==0

@pytest.mark.asyncio
async def test_cancelling_offer_returns_entry_to_active(ctx):
    c,sessions=ctx;h=await register(c,"offer-cancel@example.com")
    patient=(await c.post("/api/patients",headers=h,json={"first_name":"Fake","last_name":"OfferCancel"})).json();room=(await c.post("/api/rooms",headers=h,json={"name":"Cancel offer room"})).json();doctor=(await c.get("/api/auth/me",headers=h)).json()["id"]
    day=date.today()+timedelta(days=25);entry=(await c.post("/api/waitlist/entries",headers=h,json={"patient_id":patient["id"],"desired_duration_minutes":30,"windows":[{"weekday":day.weekday(),"start":"10:00:00","end":"12:00:00"}],"valid_from":day.isoformat(),"valid_until":day.isoformat()})).json()
    offer=(await c.post(f"/api/waitlist/entries/{entry['id']}/offers",headers=h,json={"doctor_id":doctor,"room_id":room["id"],"starts_at":f"{day}T10:00:00+02:00","duration_minutes":30})).json()
    response=await c.post(f"/api/waitlist/offers/{offer['id']}/cancel",headers=h)
    assert response.status_code==200 and response.json()["status"]=="cancelled"
    async with sessions() as s: assert (await s.get(WaitlistEntry,entry["id"])).status=="active"

@pytest.mark.asyncio
async def test_waitlist_database_checks_and_restrict_foreign_keys(ctx):
    from sqlalchemy.exc import IntegrityError
    c,sessions=ctx;h=await register(c,"constraints@example.com")
    patient=(await c.post("/api/patients",headers=h,json={"first_name":"Fake","last_name":"Constraint"})).json()
    day=date.today()+timedelta(days=26);entry=(await c.post("/api/waitlist/entries",headers=h,json={"patient_id":patient["id"],"desired_duration_minutes":30,"windows":[{"weekday":day.weekday(),"start":"10:00:00","end":"12:00:00"}],"valid_from":day.isoformat(),"valid_until":day.isoformat()})).json()
    async with sessions() as s:
        connection=await s.connection();await connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        row=await s.get(WaitlistEntry,entry["id"]);row.status="nonsense"
        with pytest.raises(IntegrityError):await s.commit()
        await s.rollback()
        row=await s.get(WaitlistEntry,entry["id"]);patient_row=await s.get(Patient,patient["id"]);await s.delete(patient_row)
        with pytest.raises(IntegrityError):await s.commit()
        await s.rollback()
        assert (await s.get(WaitlistEntry,entry["id"])).status=="active"
