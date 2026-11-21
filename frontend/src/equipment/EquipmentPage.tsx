import { FormEvent, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiErrorMessage, getClinic, listRooms, listStaff, listProcedures, User } from '../api/client';
import { addCheck, addDependency, addDocument, closeOutage, completeCheck, createEquipment, equipmentHistory, Equipment, listDueChecks, listEquipment, reportOutage, updateEquipment } from './api';
import { clinicLocalToUtc } from '../timezone';
import './equipment.css';

const managers = ['clinic_manager', 'administrator'];
const canReport = (role: string) => [...managers, 'doctor', 'assistant'].includes(role);
const equipmentStateLabel: Record<string, string> = { available: 'Disponibil', maintenance: 'Mentenanță', broken: 'Defect', retired: 'Retras' };
const format = (value: string, timezone: string) => new Intl.DateTimeFormat('ro-RO', { dateStyle: 'medium', timeStyle: 'short', timeZone: timezone }).format(new Date(value));

function CheckHistoryItem({ check, timezone, canComplete, pending, onComplete }: {
  check: Record<string, unknown>; timezone: string; canComplete: boolean; pending: boolean; onComplete: (result: string) => void;
}) {
  const [result, setResult] = useState('');
  return <article className="equipment-check"><p>{check.check_type === 'service' ? 'Service' : 'Verificare'} · scadentă {format(String(check.due_at), timezone)} · {check.completed_at ? `efectuată ${format(String(check.completed_at), timezone)} de ${check.completed_by_id ?? check.author_id}` : 'neefectuată'}</p>
    {Boolean(check.completed_at && check.result) && <p>Rezultat: {String(check.result)}</p>}
    {canComplete && !check.completed_at && <form className="equipment-inline" aria-label={`Completează verificarea ${String(check.id)}`} onSubmit={event => { event.preventDefault(); if (result.trim()) onComplete(result.trim()); }}><label>Rezultatul verificării<input required value={result} onChange={event => setResult(event.target.value)} /></label><button disabled={pending || !result.trim()}>Marchează efectuată</button></form>}
  </article>;
}

export function EquipmentPage({ user }: { user: User }) {
  const manager = managers.includes(user.role);
  const client = useQueryClient();
  const equipment = useQuery({ queryKey: ['equipment'], queryFn: listEquipment });
  const dueChecks = useQuery({ queryKey: ['equipment-due-checks'], queryFn: listDueChecks });
  const clinic = useQuery({ queryKey: ['clinic'], queryFn: getClinic });
  const rooms = useQuery({ queryKey: ['rooms'], queryFn: listRooms, enabled: manager });
  const staff = useQuery({ queryKey: ['staff'], queryFn: listStaff, enabled: manager });
  const procedures = useQuery({ queryKey: ['procedures'], queryFn: () => listProcedures(undefined, true, 100), enabled: manager });
  const [selected, setSelected] = useState('');
  const [notice, setNotice] = useState('');
  const [mutationError, setMutationError] = useState('');
  const [outageReason, setOutageReason] = useState('');
  const [dependencyTarget, setDependencyTarget] = useState<'' | 'room' | 'procedure'>('');
  const outageFormRef = useRef<HTMLFormElement>(null);
  const pendingForms = useRef(new WeakSet<HTMLFormElement>());
  const history = useQuery({ queryKey: ['equipment-history', selected], queryFn: () => equipmentHistory(selected), enabled: Boolean(selected) });
  const refresh = () => { void client.invalidateQueries({ queryKey: ['equipment'] }); void client.invalidateQueries({ queryKey: ['equipment-history'] }); };
  const mutationOptions = { onMutate: () => setMutationError(''), onError: (error: unknown) => setMutationError(apiErrorMessage(error)), onSuccess: () => setMutationError('') };
  const create = useMutation({ mutationFn: createEquipment, ...mutationOptions, onSuccess: result => { setMutationError(''); setSelected(result.id); refresh(); } });
  const update = useMutation({ mutationFn: updateEquipment, ...mutationOptions, onSuccess: result => {
    setMutationError('');
    refresh();
    if (result.unavailability_required) {
      setSelected(result.id);
      setNotice(`Echipamentul este ${result.state === 'broken' ? 'defect' : 'în mentenanță'} și nu are o indisponibilitate deschisă. Completează intervalul pentru a alerta programările.`);
      setOutageReason(`${result.state === 'broken' ? 'Defecțiune' : 'Mentenanță'} raportată pentru ${result.name}`);
      requestAnimationFrame(() => outageFormRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' }));
    }
  }, onError: mutationOptions.onError });
  const outage = useMutation({ mutationFn: ({ id, data }: { id: string; data: { reason: string; starts_at: string; ends_at: string } }) => reportOutage(id, data), ...mutationOptions, onSuccess: result => { setMutationError(''); setNotice(result.affected_appointments.length ? `Avertisment: ${result.affected_appointments.length} programări afectate.` : 'Indisponibilitatea a fost înregistrată; nu sunt programări afectate.'); refresh(); } });
  const check = useMutation({ mutationFn: ({ id, data }: { id: string; data: { check_type: string; due_at: string; completed_at?: string; result?: string } }) => addCheck(id, data), ...mutationOptions, onSuccess: () => { setMutationError(''); refresh(); } });
  const completion = useMutation({ mutationFn: ({ equipmentId, checkId, result }: { equipmentId: string; checkId: string; result: string }) => completeCheck(equipmentId, checkId, result), ...mutationOptions, onSuccess: () => { setMutationError(''); refresh(); } });
  const doc = useMutation({ mutationFn: ({ id, data }: { id: string; data: { title: string; document_type: string; reference: string } }) => addDocument(id, data), ...mutationOptions, onSuccess: () => { setMutationError(''); refresh(); } });
  const dependency = useMutation({ mutationFn: ({ id, data }: { id: string; data: { room_id?: string; procedure_id?: string } }) => addDependency(id, data), ...mutationOptions, onSuccess: () => { setMutationError(''); refresh(); } });
  const close = useMutation({ mutationFn: ({ equipmentId, outageId }: { equipmentId: string; outageId: string }) => closeOutage(equipmentId, outageId), ...mutationOptions, onSuccess: () => { setMutationError(''); refresh(); } });
  const current = equipment.data?.find(item => item.id === selected);
  const timezone = clinic.data?.timezone ?? 'Europe/Bucharest';
  function acquireForm(form: HTMLFormElement) { if (pendingForms.current.has(form)) return false; pendingForms.current.add(form); return true; }
  function releaseForm(form: HTMLFormElement) { pendingForms.current.delete(form); }
  function submitEquipment(event: FormEvent<HTMLFormElement>) { event.preventDefault(); const form = event.currentTarget; if (!acquireForm(form)) return; const data = new FormData(form); create.mutate({ name: String(data.get('name')), equipment_type: String(data.get('equipment_type')), serial_number: String(data.get('serial_number')) || null, room_id: String(data.get('room_id')) || null, responsible_id: String(data.get('responsible_id')), state: 'available', is_active: true }, { onSuccess: () => form.reset(), onSettled: () => releaseForm(form) }); }
  function submitOutage(event: FormEvent<HTMLFormElement>) { event.preventDefault(); const form = event.currentTarget; if (!selected || !acquireForm(form)) return; try { const data = new FormData(form); outage.mutate({ id: selected, data: { reason: String(data.get('reason')), starts_at: clinicLocalToUtc(String(data.get('starts_at')), timezone), ends_at: clinicLocalToUtc(String(data.get('ends_at')), timezone) } }, { onSuccess: () => { form.reset(); setOutageReason(''); }, onSettled: () => releaseForm(form) }); } catch (error) { releaseForm(form); setNotice(error instanceof Error ? error.message : 'Ora introdusă nu este validă.'); } }

  return <main className="equipment-page">
    <header><div><p className="eyebrow">ADMINISTRARE CLINICĂ</p><h1>Echipamente</h1><p className="muted">Registru, verificări și indisponibilități. Programările sunt doar citite.</p></div></header>
    {mutationError && <p className="error" role="alert">{mutationError}</p>}
    {dueChecks.isError && <p className="error" role="alert">Lista scadențelor nu s-a putut încărca.</p>}
    {manager && <details className="card"><summary>Înregistrează echipament</summary><form className="equipment-grid" onSubmit={submitEquipment}>
      <label>Nume<input name="name" required maxLength={160} /></label><label>Tip<input name="equipment_type" required maxLength={100} /></label><label>Serie<input name="serial_number" maxLength={120} /></label>
      <label>Cabinet<select name="room_id"><option value="">Fără cabinet implicit</option>{rooms.data?.map(room => <option key={room.id} value={room.id}>{room.name}</option>)}</select></label>
      <label>Responsabil<select name="responsible_id" required><option value="">Selectează</option>{staff.data?.map(person => <option key={person.id} value={person.id}>{person.full_name}</option>)}</select></label><button disabled={create.isPending}>Adaugă</button>
    </form></details>}
    {dueChecks.data && dueChecks.data.length > 0 && <section className="equipment-warning" aria-label="Scadențe service și verificări"><strong>Service și verificări scadente în următoarele 30 de zile</strong>{dueChecks.data.map(row => <p key={String(row.id)}>{row.equipment_name} · {String(row.check_type)} · {format(String(row.due_at), timezone)} · {row.overdue ? 'DEPĂȘITĂ' : 'scadentă'}</p>)}</section>}
    <section className="equipment-layout"><div className="card"><h2>Registru</h2>{equipment.isLoading ? <p>Se încarcă…</p> : equipment.isError ? <p className="error">Nu s-a putut încărca registrul.</p> : equipment.data?.length ? <div className="equipment-list">{equipment.data.map(item => <button className={selected === item.id ? 'equipment-row selected' : 'equipment-row'} key={item.id} onClick={() => { setSelected(item.id); setNotice(''); }}><strong>{item.name}</strong><span>{item.equipment_type} · {equipmentStateLabel[item.state] ?? item.state} · {item.is_active ? 'Activ' : 'Inactiv'}</span>{item.missing_unavailability && <strong className="equipment-state-warning">Defect / mentenanță fără indisponibilitate</strong>}</button>)}</div> : <p className="muted">Nu există echipamente înregistrate.</p>}</div>
      <div className="card equipment-detail">{!current ? <p className="muted">Selectează un echipament pentru detalii.</p> : <><h2>{current.name}</h2><p>{current.equipment_type} · Serie: {current.serial_number || '—'} · {current.is_active ? 'Activ' : 'Inactiv'}</p>
        {manager && <div className="equipment-inline"><label>Stare<select value={current.state} onChange={event => update.mutate({ ...current, state: event.target.value })}><option value="available">Disponibil</option><option value="maintenance">Mentenanță</option><option value="broken">Defect</option><option value="retired">Retras</option></select></label><button className="secondary" onClick={() => update.mutate({ ...current, is_active: !current.is_active })}>{current.is_active ? 'Dezactivează' : 'Reactivează'}</button></div>}
        {canReport(user.role) && <form ref={outageFormRef} className="equipment-grid" onSubmit={submitOutage}><h3>Raportează indisponibilitate</h3><label>Motiv<input name="reason" required maxLength={500} value={outageReason} onChange={event => setOutageReason(event.target.value)} /></label><label>Început ({timezone})<input name="starts_at" type="datetime-local" required /></label><label>Sfârșit ({timezone})<input name="ends_at" type="datetime-local" required /></label><button disabled={outage.isPending}>Înregistrează</button></form>}
        {manager && <>
          <form className="equipment-grid" onSubmit={event => { event.preventDefault(); const form = event.currentTarget; if (!selected || !acquireForm(form)) return; try { const data = new FormData(form); check.mutate({ id: selected, data: { check_type: String(data.get('check_type')), due_at: clinicLocalToUtc(String(data.get('due_at')), timezone), completed_at: String(data.get('completed_at')) ? clinicLocalToUtc(String(data.get('completed_at')), timezone) : undefined, result: String(data.get('result')) || undefined } }, { onSuccess: () => form.reset(), onSettled: () => releaseForm(form) }); } catch (error) { releaseForm(form); setNotice(error instanceof Error ? error.message : 'Ora introdusă nu este validă.'); } }}><h3>Service / verificare periodică</h3><label>Tip<select name="check_type"><option value="service">Service</option><option value="inspection">Verificare</option></select></label><label>Scadență ({timezone})<input name="due_at" type="datetime-local" required /></label><label>Efectuat la ({timezone})<input name="completed_at" type="datetime-local" /></label><label>Rezultat<input name="result" maxLength={2000} /></label><button disabled={check.isPending}>Adaugă în istoric</button></form>
          <form className="equipment-grid" onSubmit={event => { event.preventDefault(); const form = event.currentTarget; if (!selected || !acquireForm(form)) return; const data = new FormData(form); doc.mutate({ id: selected, data: { title: String(data.get('title')), document_type: String(data.get('document_type')), reference: String(data.get('reference')) } }, { onSuccess: () => form.reset(), onSettled: () => releaseForm(form) }); }}><h3>Document (referință)</h3><label>Titlu<input name="title" required /></label><label>Tip<input name="document_type" required /></label><label>Referință URI/ID<input name="reference" required /></label><button disabled={doc.isPending}>Adaugă</button></form>
          <form className="equipment-grid" onSubmit={event => { event.preventDefault(); const form = event.currentTarget; if (!selected || !dependencyTarget || !acquireForm(form)) return; const data = new FormData(form); const targetId = String(data.get(dependencyTarget === 'room' ? 'room_id' : 'procedure_id')); if (!targetId) { releaseForm(form); return; } dependency.mutate({ id: selected, data: dependencyTarget === 'room' ? { room_id: targetId } : { procedure_id: targetId } }, { onSuccess: () => { form.reset(); setDependencyTarget(''); }, onSettled: () => releaseForm(form) }); }}><h3>Dependență</h3><p className="muted">Alege un singur tip: cabinet sau serviciu. Pentru ambele, adaugă înregistrări separate.</p><label>Tipul dependenței<select aria-label="Tipul dependenței" value={dependencyTarget} onChange={event => setDependencyTarget(event.target.value as '' | 'room' | 'procedure')}><option value="">Selectează</option><option value="room">Cabinet</option><option value="procedure">Serviciu</option></select></label>{dependencyTarget === 'room' && <label>Cabinet<select name="room_id" required><option value="">Selectează cabinetul</option>{rooms.data?.map(room => <option key={room.id} value={room.id}>{room.name}</option>)}</select></label>}{dependencyTarget === 'procedure' && <label>Serviciu<select name="procedure_id" required><option value="">Selectează serviciul</option>{procedures.data?.map(proc => <option key={proc.id} value={proc.id}>{proc.name}</option>)}</select></label>}<button disabled={!dependencyTarget || dependency.isPending}>Adaugă dependență</button></form></>}
        {notice && <p className="equipment-warning" role="alert">{notice}</p>}
        {history.isLoading && <p>Se încarcă istoricul…</p>}{history.isError && <p className="error">Istoricul nu s-a putut încărca.</p>}
        {history.data?.unavailabilities.map(item => <article className="equipment-outage" key={item.id}><h3>Indisponibilitate: {item.reason}</h3><p>{format(item.starts_at, timezone)} – {format(item.ends_at, timezone)}{item.closed_at ? ' · închisă' : ''}</p>{!item.closed_at && manager && <button onClick={() => close.mutate({ equipmentId: current.id, outageId: item.id })}>Închide indisponibilitatea</button>}{item.affected_appointments.length > 0 && <div className="equipment-warning"><strong>Atenție: {item.affected_appointments.length} programări afectate</strong>{item.affected_appointments.map(appointment => <p key={appointment.appointment_id}><strong>Potrivire {appointment.match}</strong> · {appointment.patient} · {format(appointment.starts_at, timezone)} · {appointment.appointment_type} · {appointment.room} · Dr. {appointment.doctor} · Responsabil: {appointment.responsible}</p>)}</div>}</article>)}
        {history.data?.checks.map(row => <CheckHistoryItem key={String(row.id)} check={row} timezone={timezone} canComplete={manager} pending={completion.isPending} onComplete={result => completion.mutate({ equipmentId: current.id, checkId: String(row.id), result })} />)}
        {history.data?.documents.map(row => <p key={String(row.id)}>Document referențiat: {String(row.title)} · {String(row.reference)}</p>)}
      </>}</div></section>
  </main>;
}
