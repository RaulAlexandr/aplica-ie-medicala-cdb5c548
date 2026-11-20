"""PostgreSQL populated upgrade and serialized competing confirmations."""
import asyncio
import os
import subprocess
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Appointment, WaitlistOffer, get_db
from app.main import app
from tests.alembic_helpers import configured_alembic_head, revision_is_in_head_lineage

pytestmark=pytest.mark.postgres

def dsn(url,name):
 p=urlparse(url.replace("+asyncpg",""));return urlunparse(p._replace(scheme="postgresql",path=f"/{name}"))

async def alembic(url,target):
 env={**os.environ,"DATABASE_URL":url,"JWT_SECRET":"t16-ci","ENVIRONMENT":"test"}
 return await asyncio.to_thread(subprocess.run,["alembic","upgrade",target],cwd=os.path.dirname(__file__)+"/..",env=env,capture_output=True,text=True)

@pytest.fixture
async def tempdb():
 source=os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
 if not source or not source.startswith("postgresql+"):pytest.skip("PostgreSQL CI URL required")
 name="t16_"+uuid4().hex[:12];admin=await asyncpg.connect(dsn(source,"postgres"));await admin.execute(f'CREATE DATABASE "{name}"');await admin.close()
 try:yield source.rsplit("/",1)[0]+f"/{name}",name
 finally:
  admin=await asyncpg.connect(dsn(source,"postgres"));await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)');await admin.close()

@pytest.mark.asyncio
async def test_t16_migration_preserves_populated_t12_and_configured_head(tempdb):
 url,name=tempdb;base=await alembic(url,"t12_intake_forms");assert base.returncode==0,base.stdout+base.stderr
 clinic,user,patient=(str(uuid4()) for _ in range(3));c=await asyncpg.connect(dsn(url,name))
 try:
  await c.execute("INSERT INTO clinics(id,name,timezone,created_at,updated_at) VALUES($1,'T16 fictiv','Europe/Bucharest',now(),now())",clinic)
  await c.execute("INSERT INTO users(id,clinic_id,email,password_hash,full_name,role,is_active,created_at,updated_at) VALUES($1,$2,$3,'x','Manager fictiv','clinic_manager',true,now(),now())",user,clinic,f"{user}@example.com")
  await c.execute("INSERT INTO patients(id,clinic_id,first_name,last_name,created_at,updated_at) VALUES($1,$2,'Pacient','Fictiv',now(),now())",patient,clinic)
  await c.execute("INSERT INTO form_templates(id,clinic_id,name,created_by_id,created_at) VALUES($1,$2,'Șablon fictiv',$3,now())",str(uuid4()),clinic,user)
 finally:await c.close()
 result=await alembic(url,"head");assert result.returncode==0,result.stdout+result.stderr
 assert revision_is_in_head_lineage("t12_intake_forms") and revision_is_in_head_lineage("t16_waitlist_recalls")
 c=await asyncpg.connect(dsn(url,name))
 try:
  assert await c.fetchval("SELECT version_num FROM alembic_version")==configured_alembic_head()
  assert await c.fetchval("SELECT count(*) FROM form_templates")==1
  assert await c.fetchval("SELECT count(*) FROM waitlist_entries")==0
 finally:await c.close()

@pytest.mark.asyncio
async def test_competing_confirmations_create_exactly_one_appointment():
 url=os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
 if not url or not url.startswith("postgresql+"):pytest.skip("PostgreSQL CI URL required")
 engine=create_async_engine(url);sessions=async_sessionmaker(engine,expire_on_commit=False)
 async def override():
  async with sessions() as s:yield s
 app.dependency_overrides[get_db]=override
 try:
  async with AsyncClient(transport=ASGITransport(app=app),base_url="http://test") as c:
   reg=await c.post("/api/auth/register",json={"clinic_name":"T16 fake","full_name":"Manager fake","email":f"{uuid4().hex}@example.com","password":"correct horse battery staple"});assert reg.status_code==201
   h={"Authorization":f"Bearer {reg.json()['access_token']}"};p=(await c.post("/api/patients",headers=h,json={"first_name":"Fake","last_name":"Patient"})).json();r=(await c.post("/api/rooms",headers=h,json={"name":"Room fake"})).json();doctor=(await c.get("/api/auth/me",headers=h)).json()["id"]
   day=__import__('datetime').date.today()+__import__('datetime').timedelta(days=10)
   e=(await c.post("/api/waitlist/entries",headers=h,json={"patient_id":p["id"],"desired_duration_minutes":30,"windows":[{"weekday":day.weekday(),"start":"09:00:00","end":"12:00:00"}],"valid_from":day.isoformat(),"valid_until":day.isoformat()})).json()
   o=await c.post(f"/api/waitlist/entries/{e['id']}/offers",headers=h,json={"doctor_id":doctor,"room_id":r["id"],"starts_at":f"{day}T08:00:00+00:00","duration_minutes":30});assert o.status_code==201
   # The PostgreSQL partial unique index is tested with a direct ORM insert,
   # bypassing the application-level active-offer check.
   async with sessions() as s:
    current=await s.scalar(select(WaitlistOffer).where(WaitlistOffer.status=="active"))
    duplicate=WaitlistOffer(clinic_id=current.clinic_id,entry_id=current.entry_id,doctor_id=current.doctor_id,room_id=current.room_id,starts_at=current.starts_at,duration_minutes=current.duration_minutes,expires_at=current.expires_at,status="active",created_by_id=current.created_by_id)
    s.add(duplicate)
    with pytest.raises(IntegrityError):await s.commit()
    await s.rollback()
   ready=asyncio.Event();lock=asyncio.Lock();count=0;session_ids=[]
   async def barrier_override():
    nonlocal count
    async with sessions() as s:
     async with lock:
      count+=1;session_ids.append(id(s))
      if count==2:ready.set()
     await asyncio.wait_for(ready.wait(),10);yield s
   app.dependency_overrides[get_db]=barrier_override
   async with sessions() as s:before=await s.scalar(select(func.count()).select_from(Appointment))
   async def confirm():return await asyncio.wait_for(c.post(f"/api/waitlist/offers/{o.json()['id']}/confirm",headers=h),10)
   results=await asyncio.wait_for(asyncio.gather(confirm(),confirm()),15);assert sorted(x.status_code for x in results)==[201,409]
   assert count==2 and len(set(session_ids))==2, "requests must use distinct sessions and meet at the barrier"
   async with sessions() as s:assert await s.scalar(select(func.count()).select_from(Appointment))==before+1
 finally:app.dependency_overrides.clear();await engine.dispose()
