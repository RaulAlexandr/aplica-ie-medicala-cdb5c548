import { FormEvent, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiErrorMessage, getClinic } from '../api/client';
import { addInventoryItem, addInventoryLocation, addInventoryLot, addInventoryMovement, inventoryHistory, inventoryOverview, reverseInventoryMovement, updateInventoryItem, updateInventoryLocation } from './api';

const managers=['clinic_manager','administrator'];
const staff=['clinic_manager','administrator','doctor','assistant'];
const newId=()=>crypto.randomUUID();
export function InventoryPage({role}:{role:string}) {
  const client=useQueryClient(), view=useQuery({queryKey:['inventory'],queryFn:inventoryOverview}), history=useQuery({queryKey:['inventory-history'],queryFn:inventoryHistory}), clinic=useQuery({queryKey:['clinic'],queryFn:getClinic});
  const [message,setMessage]=useState(''),[itemName,setItemName]=useState(''),[unit,setUnit]=useState(''),[minimum,setMinimum]=useState('0'),[locationName,setLocationName]=useState(''),[lotItem,setLotItem]=useState(''),[lotCode,setLotCode]=useState(''),[expiry,setExpiry]=useState(''),[kind,setKind]=useState('consumption'),[itemId,setItemId]=useState(''),[lotId,setLotId]=useState(''),[locationId,setLocationId]=useState(''),[destination,setDestination]=useState(''),[quantity,setQuantity]=useState('1'),[reason,setReason]=useState(''),[direction,setDirection]=useState('increase'),[filter,setFilter]=useState('all');
  const changed=()=>{void client.invalidateQueries({queryKey:['inventory']});void client.invalidateQueries({queryKey:['inventory-history']});setMessage('Mișcarea a fost salvată.');};
  const itemMutation=useMutation({mutationFn:()=>addInventoryItem({name:itemName,unit,minimum_quantity:minimum}),onSuccess:()=>{changed();setItemName('');setUnit('');}});
  const locationMutation=useMutation({mutationFn:()=>addInventoryLocation({name:locationName}),onSuccess:()=>{changed();setLocationName('');}});
  const lotMutation=useMutation({mutationFn:()=>addInventoryLot({item_id:lotItem,code:lotCode,expires_on:expiry||null}),onSuccess:()=>{changed();setLotCode('');}});
  const toggleItem=useMutation({mutationFn:(x:{id:string;name:string;unit:string;minimum_quantity:string;is_active:boolean})=>updateInventoryItem(x.id,{name:x.name,unit:x.unit,minimum_quantity:x.minimum_quantity,is_active:!x.is_active}),onSuccess:changed});
  const toggleLocation=useMutation({mutationFn:(x:{id:string;name:string;is_active:boolean})=>updateInventoryLocation(x.id,{name:x.name,is_active:!x.is_active}),onSuccess:changed});
  const movement=useMutation({mutationFn:()=>addInventoryMovement(kind,{item_id:itemId,lot_id:lotId,location_id:locationId,destination_location_id:kind==='transfer'?destination:null,quantity,client_request_id:newId(),reason,direction}),onSuccess:changed,onError:e=>setMessage(apiErrorMessage(e))});
  const reverse=useMutation({mutationFn:({id,reason}:{id:string;reason:string})=>reverseInventoryMovement(id,{client_request_id:newId(),reason}),onSuccess:changed,onError:e=>setMessage(apiErrorMessage(e))});
  const isManager=managers.includes(role),canWork=staff.includes(role);
  const balances=(view.data?.balances??[]).filter(x=>filter==='all'||filter==='low'&&x.below_minimum||filter==='expired'&&x.expired||filter==='soon'&&x.expiring_soon);
  function submit(e:FormEvent,fn:()=>void){e.preventDefault();setMessage('');fn();}
  return <section className="inventory-page">
    <header><div><p className="eyebrow">GESTIONAREA MATERIALELOR</p><h1>Stocuri</h1></div><button className="secondary" onClick={()=>{void view.refetch();void history.refetch();}}>Reîmprospătează</button></header>
    {message&&<p role="status" className="notice-text">{message}</p>}{(view.isError||history.isError)&&<p className="error" role="alert">Nu s-au putut încărca stocurile.</p>}
    <section className="card"><h2>Solduri</h2><label>Filtru<select value={filter} onChange={e=>setFilter(e.target.value)}><option value="all">Toate</option><option value="low">Sub prag</option><option value="expired">Loturi expirate</option><option value="soon">Expiră în 30 de zile</option></select></label>
      {isManager&&view.data?.items.map(x=><article className="list-row" key={x.id}><span>{x.name} · {x.unit} · prag {x.minimum_quantity} · sold total {x.quantity_total} · {x.is_active?'Activ':'Inactiv'}</span><span></span><span></span><button className="secondary" onClick={()=>toggleItem.mutate(x)}>{x.is_active?'Dezactivează':'Activează'}</button></article>)}
      {isManager&&view.data?.locations.map(x=><article className="list-row" key={x.id}><span>Locație: {x.name} · {x.is_active?'Activă':'Inactivă'}</span><span></span><span></span><button className="secondary" onClick={()=>toggleLocation.mutate(x)}>{x.is_active?'Dezactivează':'Activează'}</button></article>)}
      <div className="list-row"><strong>Articol / lot / locație</strong><strong>Cantitate</strong><strong>Prag</strong><strong>Expirare</strong></div>
      {filter==='low'&&view.data?.items.filter(x=>x.below_minimum).map(x=><article className="list-row" key={`low-${x.id}`}><span>{x.name} · total pe toate loturile și locațiile</span><span>{x.quantity_total} {x.unit}</span><span>{x.minimum_quantity}</span><span>Sub prag</span></article>)}
      {balances.map((x,i)=><article className="list-row" key={`${x.lot_id}-${x.location_id}-${i}`}><span>{x.item} · {x.lot} · {x.location}{x.below_minimum&&<small> · Sub prag (total articol: {x.item_total} {x.unit})</small>}</span><span>{x.quantity} {x.unit}</span><span>{x.minimum_quantity}</span><span>{x.expires_on??'—'}{x.expired?' · Expirat':x.expiring_soon?' · Expiră curând':''}</span></article>)}
      {!balances.length&&<p>Nu există înregistrări pentru filtrul ales.</p>}
    </section>
    {isManager&&<section className="grid two">
      <form className="card" onSubmit={e=>submit(e,()=>itemMutation.mutate())}><h2>Articol nou</h2><label>Denumire<input required value={itemName} onChange={e=>setItemName(e.target.value)}/></label><label>Unitate<input required value={unit} onChange={e=>setUnit(e.target.value)}/></label><label>Prag minim<input type="number" min="0" step="0.001" required value={minimum} onChange={e=>setMinimum(e.target.value)}/></label><button>Adaugă articol</button></form>
      <form className="card" onSubmit={e=>submit(e,()=>locationMutation.mutate())}><h2>Locație nouă</h2><label>Denumire<input required value={locationName} onChange={e=>setLocationName(e.target.value)}/></label><button>Adaugă locație</button></form>
      <form className="card" onSubmit={e=>submit(e,()=>lotMutation.mutate())}><h2>Lot nou</h2><label>Articol<select required value={lotItem} onChange={e=>setLotItem(e.target.value)}><option value="">Alege articolul</option>{view.data?.items.map(x=><option key={x.id} value={x.id}>{x.name}</option>)}</select></label><label>Cod lot<input required value={lotCode} onChange={e=>setLotCode(e.target.value)}/></label><label>Data expirării<input type="date" value={expiry} onChange={e=>setExpiry(e.target.value)}/></label><button>Adaugă lot</button></form>
    </section>}
    {canWork&&<form className="card" onSubmit={e=>submit(e,()=>movement.mutate())}><h2>Înregistrează mișcare</h2><div className="grid two"><label>Operație<select value={kind} onChange={e=>setKind(e.target.value)}>{(isManager?['consumption','receipt','transfer','adjustment']:['consumption']).map(x=><option key={x} value={x}>{({consumption:'Consum',receipt:'Recepție',transfer:'Transfer',adjustment:'Ajustare'})[x]}</option>)}</select></label>
      <label>Articol<select required value={itemId} onChange={e=>setItemId(e.target.value)}><option value="">Alege articolul</option>{view.data?.items.map(x=><option key={x.id} value={x.id}>{x.name}</option>)}</select></label>
      <label>Lot<select required value={lotId} onChange={e=>setLotId(e.target.value)}><option value="">Alege lotul</option>{view.data?.balances.filter((x,i,a)=>x.item_id===itemId&&a.findIndex(y=>y.lot_id===x.lot_id)===i).map(x=><option key={x.lot_id} value={x.lot_id}>{x.lot}{x.expires_on?` · expiră ${x.expires_on}`:''}</option>)}</select></label>
      <label>Locație<select required value={locationId} onChange={e=>setLocationId(e.target.value)}><option value="">Alege locația</option>{view.data?.locations.filter(x=>x.is_active).map(x=><option key={x.id} value={x.id}>{x.name}</option>)}</select></label>
      {kind==='transfer'&&<label>Destinație<select required value={destination} onChange={e=>setDestination(e.target.value)}><option value="">Alege destinația</option>{view.data?.locations.filter(x=>x.id!==locationId).map(x=><option key={x.id} value={x.id}>{x.name}</option>)}</select></label>}
      {kind==='adjustment'&&<label>Sens<select value={direction} onChange={e=>setDirection(e.target.value)}><option value="increase">Adăugare</option><option value="decrease">Scădere</option></select></label>}
      <label>Cantitate<input type="number" min="0.001" step="0.001" required value={quantity} onChange={e=>setQuantity(e.target.value)}/></label>
      {kind==='adjustment'&&<label>Motiv<input required value={reason} onChange={e=>setReason(e.target.value)}/></label>}
      </div><button>Salvează mișcarea</button></form>}
    <section className="card"><h2>Istoricul mișcărilor</h2>{history.data?.map(x=><article className="list-row" key={x.id}><span>{x.kind} · {x.quantity} · {x.reason||'—'} · {new Date(x.created_at).toLocaleString('ro-RO',{timeZone:clinic.data?.timezone??'UTC'})}</span>{isManager&&<button className="secondary" onClick={()=>{const reason=window.prompt('Motivul corecției');if(reason?.trim())reverse.mutate({id:x.id,reason});}}>Corectează</button>}</article>)}{history.data?.length===0&&<p>Nu există mișcări înregistrate.</p>}</section>
  </section>;
}
