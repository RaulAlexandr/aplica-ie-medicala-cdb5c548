import json
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import require_roles
from app.database import Clinic, User, get_db, utcnow
from app.inventory.models import InventoryItem, InventoryLocation, InventoryLot, InventoryMovement

router = APIRouter(prefix="/inventory", tags=["inventory"])
READ = ("clinic_manager", "administrator", "doctor", "assistant", "reception")
CONSUME = ("clinic_manager", "administrator", "doctor", "assistant")
MANAGE = ("clinic_manager", "administrator")

class ItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    unit: str = Field(min_length=1, max_length=32)
    minimum_quantity: Decimal = Field(ge=0, max_digits=14, decimal_places=3)
    is_active: bool = True

    @field_validator("name", "unit", mode="before")
    @classmethod
    def trim_required_text(cls, value):
        if not isinstance(value,str): return value
        value=value.strip()
        if not value: raise ValueError("Câmpul nu poate fi gol.")
        return value

class LocationIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    is_active: bool = True

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value):
        if not isinstance(value,str): return value
        value=value.strip()
        if not value: raise ValueError("Denumirea locației este obligatorie.")
        return value

class LotIn(BaseModel):
    item_id: UUID
    code: str = Field(min_length=1, max_length=120)
    expires_on: date | None = None

    @field_validator("code", mode="before")
    @classmethod
    def trim_code(cls, value):
        if not isinstance(value,str): return value
        value=value.strip()
        if not value: raise ValueError("Codul lotului este obligatoriu.")
        return value

class MovementIn(BaseModel):
    item_id: UUID
    lot_id: UUID
    location_id: UUID
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=3)
    client_request_id: UUID
    reason: str = Field(default="", max_length=500)
    destination_location_id: UUID | None = None
    direction: str | None = None

class ReverseIn(BaseModel):
    client_request_id: UUID
    reason: str = Field(min_length=1, max_length=500)

def _signature(kind, p):
    return json.dumps({"kind":kind,"item_id":str(p.item_id),"lot_id":str(p.lot_id),"location_id":str(p.location_id),"quantity":str(p.quantity),"destination_location_id":str(p.destination_location_id) if p.destination_location_id else None,"reason":p.reason.strip(),"direction":p.direction},sort_keys=True,separators=(",",":"))

async def _balance(db, clinic_id, lot_id, location_id):
    incoming = await db.scalar(select(func.coalesce(func.sum(InventoryMovement.quantity), 0)).where(
        InventoryMovement.clinic_id==clinic_id, InventoryMovement.lot_id==lot_id, InventoryMovement.location_id==location_id,
        InventoryMovement.kind.in_(["receipt","transfer_in","adjustment_in","reversal_in"])))
    outgoing = await db.scalar(select(func.coalesce(func.sum(InventoryMovement.quantity), 0)).where(
        InventoryMovement.clinic_id==clinic_id, InventoryMovement.lot_id==lot_id, InventoryMovement.location_id==location_id,
        InventoryMovement.kind.in_(["consumption","transfer_out","adjustment_out","reversal_out"])))
    return Decimal(incoming or 0)-Decimal(outgoing or 0)

async def _clinic_today(db, clinic_id):
    timezone=await db.scalar(select(Clinic.timezone).where(Clinic.id==clinic_id))
    try:return utcnow().astimezone(ZoneInfo(timezone or "UTC")).date()
    except ZoneInfoNotFoundError:return utcnow().date()

async def _commit_unique(db, message):
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409,message) from exc

async def _item_location_lot(db, user, p):
    lot=await db.scalar(select(InventoryLot).where(InventoryLot.id==p.lot_id,InventoryLot.clinic_id==user.clinic_id,InventoryLot.item_id==p.item_id).with_for_update())
    item=await db.scalar(select(InventoryItem).where(InventoryItem.id==p.item_id,InventoryItem.clinic_id==user.clinic_id,InventoryItem.is_active.is_(True)))
    location=await db.scalar(select(InventoryLocation).where(InventoryLocation.id==p.location_id,InventoryLocation.clinic_id==user.clinic_id,InventoryLocation.is_active.is_(True)))
    if not lot or not item or not location: raise HTTPException(404,"Resursă inexistentă")
    return item,lot,location

def _movement(clinic_id, user, item, lot, location, kind, quantity, req, signature, reason, transfer_id=None, reverses=None):
    return InventoryMovement(clinic_id=clinic_id,item_id=item.id,lot_id=lot.id,location_id=location.id,kind=kind,quantity=quantity,
        client_request_id=str(req),signature=signature,reason=reason,transfer_id=transfer_id,reverses_movement_id=reverses,
        created_by_id=user.id,created_at=utcnow())

@router.get("")
async def overview(user: User=Depends(require_roles(*READ)),db: AsyncSession=Depends(get_db)):
    items=(await db.scalars(select(InventoryItem).where(InventoryItem.clinic_id==user.clinic_id).order_by(InventoryItem.name))).all()
    locations=(await db.scalars(select(InventoryLocation).where(InventoryLocation.clinic_id==user.clinic_id).order_by(InventoryLocation.name))).all()
    result=[]; as_of=await _clinic_today(db,user.clinic_id);summaries=[]
    for item in items:
        lots=(await db.scalars(select(InventoryLot).where(InventoryLot.clinic_id==user.clinic_id,InventoryLot.item_id==item.id))).all()
        item_total=Decimal(0)
        lot_rows=[]
        for lot in lots:
            total=Decimal(0)
            for loc in locations:
                qty=await _balance(db,user.clinic_id,lot.id,loc.id); total+=qty
                lot_rows.append({"item_id":str(item.id),"item":item.name,"unit":item.unit,"minimum_quantity":str(item.minimum_quantity),"lot_id":str(lot.id),"lot":lot.code,"expires_on":lot.expires_on.isoformat() if lot.expires_on else None,"location_id":str(loc.id),"location":loc.name,"quantity":str(qty),"expired":bool(lot.expires_on and lot.expires_on<as_of),"expiring_soon":bool(lot.expires_on and as_of<=lot.expires_on<=as_of+timedelta(days=30))})
            item_total+=total
        result.extend(lot_rows)
        summaries.append({"id":str(item.id),"name":item.name,"unit":item.unit,"minimum_quantity":str(item.minimum_quantity),"quantity_total":str(item_total),"below_minimum":item_total<item.minimum_quantity,"is_active":item.is_active})
        for row in lot_rows:
            row["item_total"] = str(item_total)
            row["below_minimum"] = item_total < item.minimum_quantity
    return {"items":summaries,
        "locations":[{"id":str(x.id),"name":x.name,"is_active":x.is_active} for x in locations],"balances":result}

@router.get("/movements")
async def history(item_id: UUID|None=None,lot_id: UUID|None=None,location_id: UUID|None=None,kind: str|None=None,
                   limit: int=Query(200,ge=1,le=500),user: User=Depends(require_roles(*READ)),db: AsyncSession=Depends(get_db)):
    q=select(InventoryMovement).where(InventoryMovement.clinic_id==user.clinic_id)
    for col,val in ((InventoryMovement.item_id,item_id),(InventoryMovement.lot_id,lot_id),(InventoryMovement.location_id,location_id),(InventoryMovement.kind,kind)):
        if val is not None:q=q.where(col==val)
    rows=(await db.scalars(q.order_by(InventoryMovement.created_at.desc(),InventoryMovement.id).limit(limit))).all()
    return [{"id":str(x.id),"item_id":str(x.item_id),"lot_id":str(x.lot_id),"location_id":str(x.location_id),"kind":x.kind,"quantity":str(x.quantity),"reason":x.reason,"transfer_id":str(x.transfer_id) if x.transfer_id else None,"reverses_movement_id":str(x.reverses_movement_id) if x.reverses_movement_id else None,"created_at":x.created_at.isoformat()} for x in rows]

@router.post("/items",status_code=201)
async def create_item(p: ItemIn,user: User=Depends(require_roles(*MANAGE)),db: AsyncSession=Depends(get_db)):
    x=InventoryItem(clinic_id=user.clinic_id,**p.model_dump());db.add(x);await _commit_unique(db,"Un articol cu acest nume există deja.");return {"id":str(x.id)}

@router.patch("/items/{item_id}")
async def update_item(item_id: UUID,p: ItemIn,user: User=Depends(require_roles(*MANAGE)),db: AsyncSession=Depends(get_db)):
    x=await db.scalar(select(InventoryItem).where(InventoryItem.id==item_id,InventoryItem.clinic_id==user.clinic_id).with_for_update())
    if not x:raise HTTPException(404,"Resursă inexistentă")
    for key,value in p.model_dump().items():setattr(x,key,value)
    await _commit_unique(db,"Un articol cu acest nume există deja.");return {"id":str(x.id)}

@router.post("/locations",status_code=201)
async def create_location(p: LocationIn,user: User=Depends(require_roles(*MANAGE)),db: AsyncSession=Depends(get_db)):
    x=InventoryLocation(clinic_id=user.clinic_id,**p.model_dump());db.add(x);await _commit_unique(db,"O locație cu acest nume există deja.");return {"id":str(x.id)}

@router.patch("/locations/{location_id}")
async def update_location(location_id: UUID,p: LocationIn,user: User=Depends(require_roles(*MANAGE)),db: AsyncSession=Depends(get_db)):
    x=await db.scalar(select(InventoryLocation).where(InventoryLocation.id==location_id,InventoryLocation.clinic_id==user.clinic_id).with_for_update())
    if not x:raise HTTPException(404,"Resursă inexistentă")
    x.name=p.name;x.is_active=p.is_active;await _commit_unique(db,"O locație cu acest nume există deja.");return {"id":str(x.id)}

@router.post("/lots",status_code=201)
async def create_lot(p: LotIn,user: User=Depends(require_roles(*MANAGE)),db: AsyncSession=Depends(get_db)):
    if not await db.scalar(select(InventoryItem.id).where(InventoryItem.id==p.item_id,InventoryItem.clinic_id==user.clinic_id)):raise HTTPException(404,"Resursă inexistentă")
    x=InventoryLot(clinic_id=user.clinic_id,**p.model_dump());db.add(x);await _commit_unique(db,"Codul lotului există deja pentru acest articol.");return {"id":str(x.id)}

async def _write(p,kind,user,db):
    sig=_signature(kind,p);req=str(p.client_request_id)
    prior=await db.scalar(select(InventoryMovement).where(InventoryMovement.clinic_id==user.clinic_id,InventoryMovement.client_request_id==req))
    if prior:
        if prior.signature!=sig:raise HTTPException(409,"client_request_id deja folosit cu alt conținut")
        response={"id":str(prior.id),"transfer_id":str(prior.transfer_id) if prior.transfer_id else None,"retry":True}
        await db.rollback();return response
    item,lot,loc=await _item_location_lot(db,user,p)
    # Another request may have completed while this one waited for the lot lock.
    prior=await db.scalar(select(InventoryMovement).where(InventoryMovement.clinic_id==user.clinic_id,InventoryMovement.client_request_id==req))
    if prior:
        if prior.signature!=sig:raise HTTPException(409,"client_request_id deja folosit cu alt conținut")
        response={"id":str(prior.id),"transfer_id":str(prior.transfer_id) if prior.transfer_id else None,"retry":True}
        await db.rollback();return response
    if kind=="consumption" and lot.expires_on and lot.expires_on<await _clinic_today(db,user.clinic_id):raise HTTPException(409,"Lot expirat; consumul este interzis")
    if kind in ("consumption","transfer_out","adjustment_out") and await _balance(db,user.clinic_id,lot.id,loc.id)<p.quantity:raise HTTPException(409,"Stoc insuficient")
    if kind.startswith("adjustment") and not p.reason.strip():raise HTTPException(422,"Motivul ajustării este obligatoriu")
    if kind=="transfer_out":
        dest=await db.scalar(select(InventoryLocation).where(InventoryLocation.id==p.destination_location_id,InventoryLocation.clinic_id==user.clinic_id,InventoryLocation.is_active.is_(True)))
        if not dest:raise HTTPException(404,"Resursă inexistentă")
        if dest.id==loc.id:raise HTTPException(422,"Locația destinație trebuie să fie diferită")
        transfer_reason=p.reason.strip() or "Transfer"
        tid=uuid4();out=_movement(user.clinic_id,user,item,lot,loc,kind,p.quantity,req,sig,transfer_reason,tid);inc=_movement(user.clinic_id,user,item,lot,dest,"transfer_in",p.quantity,str(uuid4()),sig,transfer_reason,tid)
        db.add_all([out,inc])
        try:await db.commit()
        except IntegrityError:
            await db.rollback();prior=await db.scalar(select(InventoryMovement).where(InventoryMovement.clinic_id==user.clinic_id,InventoryMovement.client_request_id==req))
            if prior and prior.signature==sig:return {"id":str(prior.id),"transfer_id":str(prior.transfer_id),"retry":True}
            raise HTTPException(409,"Retry sau conflict de transfer; reîncearcă aceeași cheie")
        return {"id":str(out.id),"transfer_id":str(tid)}
    row=_movement(user.clinic_id,user,item,lot,loc,kind,p.quantity,req,sig,p.reason.strip())
    db.add(row)
    try:await db.commit()
    except IntegrityError:
        await db.rollback();prior=await db.scalar(select(InventoryMovement).where(InventoryMovement.clinic_id==user.clinic_id,InventoryMovement.client_request_id==req))
        if prior and prior.signature==sig:return {"id":str(prior.id),"retry":True}
        raise HTTPException(409,"Mișcare conflictuală")
    return {"id":str(row.id)}

@router.post("/movements/{kind}",status_code=201)
async def movement(kind: str,p: MovementIn,user: User=Depends(require_roles(*READ)),db: AsyncSession=Depends(get_db)):
    if kind=="consumption":
        if user.role not in CONSUME:raise HTTPException(403,"Rol fără drept de consum")
        return await _write(p,kind,user,db)
    if kind not in ("receipt","transfer","adjustment"):raise HTTPException(404,"Tip de mișcare invalid")
    if user.role not in MANAGE:raise HTTPException(403,"Doar managerul administrează mișcările")
    if kind=="receipt":return await _write(p,kind,user,db)
    if kind=="transfer":
        if p.destination_location_id is None:raise HTTPException(422,"Locația destinație este obligatorie")
        return await _write(p,"transfer_out",user,db)
    if p.direction not in ("increase","decrease"):raise HTTPException(422,"Sensul ajustării este obligatoriu")
    return await _write(p,"adjustment_in" if p.direction=="increase" else "adjustment_out",user,db)

@router.post("/movements/{movement_id}/reverse")
async def reverse(movement_id: UUID,p: ReverseIn,user: User=Depends(require_roles(*MANAGE)),db: AsyncSession=Depends(get_db)):
    original=await db.scalar(select(InventoryMovement).where(InventoryMovement.id==movement_id,InventoryMovement.clinic_id==user.clinic_id))
    if not original:raise HTTPException(404,"Mișcare inexistentă")
    if not p.reason.strip():raise HTTPException(422,"Motivul corecției este obligatoriu")
    req=str(p.client_request_id);sig=json.dumps({"reverse":str(movement_id),"reason":p.reason.strip()},sort_keys=True)
    prior_req=await db.scalar(select(InventoryMovement).where(InventoryMovement.clinic_id==user.clinic_id,InventoryMovement.client_request_id==req))
    if prior_req:
        if prior_req.signature!=sig:raise HTTPException(409,"client_request_id deja folosit cu alt conținut")
        response={"id":str(prior_req.id),"transfer_id":str(prior_req.transfer_id) if prior_req.transfer_id else None,"retry":True}
        await db.rollback();return response
    lot=await db.scalar(select(InventoryLot).where(InventoryLot.id==original.lot_id,InventoryLot.clinic_id==user.clinic_id).with_for_update())
    prior_req=await db.scalar(select(InventoryMovement).where(InventoryMovement.clinic_id==user.clinic_id,InventoryMovement.client_request_id==req))
    if prior_req:
        if prior_req.signature!=sig:raise HTTPException(409,"client_request_id deja folosit cu alt conținut")
        response={"id":str(prior_req.id),"transfer_id":str(prior_req.transfer_id) if prior_req.transfer_id else None,"retry":True}
        await db.rollback();return response
    originals=[original]
    if original.transfer_id: originals=(await db.scalars(select(InventoryMovement).where(InventoryMovement.clinic_id==user.clinic_id,InventoryMovement.transfer_id==original.transfer_id).order_by(InventoryMovement.location_id).with_for_update())).all()
    if any(x.reverses_movement_id for x in originals):raise HTTPException(409,"Mișcarea a fost deja corectată")
    item=await db.get(InventoryItem,original.item_id)
    reversal_transfer=uuid4() if original.transfer_id else None;rows=[]
    for idx,x in enumerate(originals):
        loc=await db.scalar(select(InventoryLocation).where(InventoryLocation.id==x.location_id,InventoryLocation.clinic_id==user.clinic_id))
        inbound=x.kind in ("receipt","transfer_in","adjustment_in","reversal_in")
        inverse="reversal_out" if inbound else "reversal_in"
        if inverse=="reversal_out" and await _balance(db,user.clinic_id,lot.id,loc.id)<x.quantity:raise HTTPException(409,"Corecția ar duce stocul sub zero")
        rows.append(_movement(user.clinic_id,user,item,lot,loc,inverse,x.quantity,req if idx==0 else str(uuid4()),sig,p.reason.strip(),reversal_transfer,x.id))
    db.add_all(rows)
    try:await db.commit()
    except IntegrityError:await db.rollback();raise HTTPException(409,"Mișcarea a fost deja corectată")
    return {"id":str(rows[0].id),"transfer_id":str(reversal_transfer) if reversal_transfer else None}
