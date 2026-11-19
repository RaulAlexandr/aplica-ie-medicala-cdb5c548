import { useRef, useState, type FormEvent, type ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { apiErrorMessage, listProcedures, type ProcedureCatalog } from '../api/client';
import { ADULT_TOOTH_IDS, type ToothId, type ToothSurface } from '../odontogram/types';
import { surfacesForTooth } from '../odontogram/surfaces';
import {
  acceptPlan, addItem, addStage, cancelItem, cancelPlan, completePlan, createPlan, deleteItem, deleteStage,
  getLinkCandidates, getPlan, linkAppointment, linkPerformed, listPatientPlans, unlinkPerformed, updateItem, updateStage,
  type ItemInput, type ItemProgress, type LinkCandidates, type PlanItem, type PlanStatus, type Priority, type TreatmentPlan,
} from './api';
import './treatment-plans.css';

export const STATUS_LABELS: Record<PlanStatus, string> = {
  draft: 'Ciornă', accepted: 'Acceptat', in_progress: 'În lucru', completed: 'Finalizat', cancelled: 'Anulat',
};
export const PROGRESS_LABELS: Record<ItemProgress, string> = {
  planned: 'Planificat', scheduled: 'Programat', in_progress: 'În execuție', done: 'Efectuat',
  attention: 'Manoperă anulată — necesită decizie', cancelled: 'Anulat explicit',
};
const PRIORITY_LABELS: Record<Priority, string> = { urgent: 'Urgent', high: 'Ridicată', normal: 'Normală', low: 'Scăzută' };
const SURFACE_NAMES: Partial<Record<ToothSurface, string>> = {
  mesial: 'mezial', distal: 'distal', occlusal: 'ocluzal', incisal: 'incizal', vestibular: 'vestibular',
  palatal: 'palatinal', lingual: 'lingual', buccal: 'bucal', labial: 'labial', approximal: 'aproximal', interproximal: 'interproximal',
};
const ACTION_LABELS: Record<string, string> = {
  created: 'Plan creat', plan_edited: 'Plan modificat', stage_added: 'Etapă adăugată', stage_edited: 'Etapă modificată',
  stage_deleted: 'Etapă ștearsă', item_added: 'Manoperă planificată adăugată', item_edited: 'Manoperă planificată modificată',
  item_deleted: 'Manoperă planificată ștearsă', item_cancelled: 'Manoperă planificată anulată', appointment_linked: 'Vizită asociată',
  appointment_unlinked: 'Vizită disociată', performed_linked: 'Manoperă efectuată asociată', performed_unlinked: 'Manoperă efectuată disociată',
  accepted: 'Acceptare consemnată', completed: 'Plan finalizat', cancelled: 'Plan anulat',
};

function newKey() { return crypto.randomUUID(); }
function formatDate(value: string | null | undefined, timezone: string) {
  if (!value) return '—';
  return new Intl.DateTimeFormat('ro-RO', { dateStyle: 'medium', timeStyle: 'short', timeZone: timezone }).format(new Date(value));
}
function location(tooth: string | null, surface: string | null) {
  if (!tooth) return 'Fără localizare dentară';
  const surfaces = surface ? surface.split(',').map(item => SURFACE_NAMES[item as ToothSurface] ?? item).join(', ') : '';
  return `Dinte ${tooth}${surfaces ? ` · ${surfaces}` : ''}`;
}
function sameSurfaces(a: string | null, b: string | null) {
  const set = (value: string | null) => (value ? value.split(',').map(item => item.trim()).filter(Boolean).sort().join(',') : '');
  return set(a) === set(b);
}

type ItemDraft = { key: string; procedure_catalog_id: string; tooth: ToothId | ''; surfaces: ToothSurface[]; priority: Priority; price: string; duration: string; notes: string };
type StageDraft = { key: string; title: string; items: ItemDraft[] };

const emptyItem = (): ItemDraft => ({ key: newKey(), procedure_catalog_id: '', tooth: '', surfaces: [], priority: 'normal', price: '', duration: '', notes: '' });
const emptyStage = (title = ''): StageDraft => ({ key: newKey(), title, items: [emptyItem()] });

function toInput(item: ItemDraft): ItemInput {
  return {
    procedure_catalog_id: item.procedure_catalog_id,
    tooth_fdi: item.tooth || null,
    tooth_surface: item.tooth && item.surfaces.length ? item.surfaces.join(',') : null,
    priority: item.priority,
    ...(item.price.trim() ? { estimated_price: item.price.trim() } : {}),
    ...(item.duration.trim() ? { estimated_duration_minutes: Number(item.duration) } : {}),
    notes: item.notes.trim() || null,
  };
}

function draftProblem(item: ItemDraft): string | null {
  if (!item.procedure_catalog_id) return 'Alege serviciul din catalog pentru fiecare manoperă planificată.';
  if (item.price.trim() && !/^\d{1,9}(\.\d{1,2})?$/.test(item.price.trim())) return 'Tariful estimat trebuie să fie o sumă cu cel mult două zecimale.';
  if (item.duration.trim() && !(Number.isInteger(Number(item.duration)) && Number(item.duration) > 0)) return 'Durata estimată trebuie să fie un număr întreg de minute.';
  return null;
}

function ItemEditor({ item, catalog, onChange, onRemove, label }: { item: ItemDraft; catalog: ProcedureCatalog[]; onChange: (item: ItemDraft) => void; onRemove?: () => void; label: string }) {
  const selected = catalog.find(row => row.id === item.procedure_catalog_id);
  const allowed = item.tooth ? surfacesForTooth(item.tooth) : [];
  return <fieldset className="tp-item-editor" aria-label={label}>
    <label>Serviciu<select aria-label={`${label} serviciu`} value={item.procedure_catalog_id} onChange={event => onChange({ ...item, procedure_catalog_id: event.target.value })}>
      <option value="">Alege serviciul activ</option>
      {catalog.map(row => <option key={row.id} value={row.id}>{row.code} · {row.name} ({row.base_price} {row.currency})</option>)}
    </select></label>
    <label>Dinte FDI<select aria-label={`${label} dinte`} value={item.tooth} onChange={event => onChange({ ...item, tooth: event.target.value as ToothId | '', surfaces: [] })}>
      <option value="">Fără dinte</option>
      {ADULT_TOOTH_IDS.map(tooth => <option key={tooth} value={tooth}>{tooth}</option>)}
    </select></label>
    <label>Prioritate<select aria-label={`${label} prioritate`} value={item.priority} onChange={event => onChange({ ...item, priority: event.target.value as Priority })}>
      {Object.entries(PRIORITY_LABELS).map(([value, text]) => <option key={value} value={value}>{text}</option>)}
    </select></label>
    <label>Tarif estimat<input aria-label={`${label} tarif estimat`} inputMode="decimal" placeholder={selected ? `${selected.base_price} ${selected.currency}` : 'din catalog'} value={item.price} onChange={event => onChange({ ...item, price: event.target.value })} /></label>
    <label>Durată estimată (min)<input aria-label={`${label} durată`} inputMode="numeric" placeholder={selected ? String(selected.default_duration_minutes) : 'din catalog'} value={item.duration} onChange={event => onChange({ ...item, duration: event.target.value })} /></label>
    {allowed.length > 0 && <div className="tp-surfaces" role="group" aria-label={`${label} suprafețe`}>
      {allowed.map(surface => <label key={surface.id} className="tp-check"><input type="checkbox" checked={item.surfaces.includes(surface.id)} onChange={() => onChange({ ...item, surfaces: item.surfaces.includes(surface.id) ? item.surfaces.filter(value => value !== surface.id) : [...item.surfaces, surface.id] })} />{surface.label}</label>)}
    </div>}
    <label className="tp-wide">Observații<input aria-label={`${label} observații`} value={item.notes} onChange={event => onChange({ ...item, notes: event.target.value })} /></label>
    {onRemove && <button type="button" className="secondary" onClick={onRemove}>Elimină manopera</button>}
  </fieldset>;
}

export function PlanForm({ patientId, catalog, onCreated, onCancel }: { patientId: string; catalog: ProcedureCatalog[]; onCreated: (plan: TreatmentPlan) => void; onCancel: () => void }) {
  const [title, setTitle] = useState('');
  const [notes, setNotes] = useState('');
  const [stages, setStages] = useState<StageDraft[]>([emptyStage('Etapa 1'), emptyStage('Etapa 2')]);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const lock = useRef(false);
  // One key per form instance: repeated clicks or a retry after a network error never create a second plan.
  const idempotencyKey = useRef(newKey());

  const updateStageDraft = (key: string, update: (stage: StageDraft) => StageDraft) => setStages(current => current.map(stage => stage.key === key ? update(stage) : stage));

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (lock.current) return;
    const problem = !title.trim() ? 'Titlul planului este obligatoriu.'
      : stages.some(stage => !stage.title.trim()) ? 'Fiecare etapă are nevoie de un titlu.'
        : stages.flatMap(stage => stage.items).map(draftProblem).find(Boolean) ?? null;
    if (problem) { setError(problem); return; }
    lock.current = true;
    setSaving(true);
    setError('');
    try {
      const plan = await createPlan(patientId, {
        title: title.trim(), notes: notes.trim() || null,
        stages: stages.map(stage => ({ title: stage.title.trim(), items: stage.items.map(toInput) })),
      }, idempotencyKey.current);
      onCreated(plan);
    } catch (caught) {
      setError(apiErrorMessage(caught));
    } finally {
      lock.current = false;
      setSaving(false);
    }
  }

  return <form className="tp-form" onSubmit={submit} aria-label="Plan de tratament nou">
    <label>Titlul planului<input aria-label="Titlul planului" value={title} onChange={event => setTitle(event.target.value)} /></label>
    <label>Observații plan<textarea aria-label="Observații plan" value={notes} onChange={event => setNotes(event.target.value)} /></label>
    {stages.map((stage, stageIndex) => <section key={stage.key} className="tp-stage-draft">
      <div className="detail-heading">
        <label>Titlu etapa {stageIndex + 1}<input aria-label={`Titlu etapa ${stageIndex + 1}`} value={stage.title} onChange={event => updateStageDraft(stage.key, current => ({ ...current, title: event.target.value }))} /></label>
        {stages.length > 1 && <button type="button" className="secondary" onClick={() => setStages(current => current.filter(row => row.key !== stage.key))}>Elimină etapa</button>}
      </div>
      {stage.items.map((item, itemIndex) => <ItemEditor key={item.key} item={item} catalog={catalog} label={`Etapa ${stageIndex + 1} manopera ${itemIndex + 1}`}
        onChange={next => updateStageDraft(stage.key, current => ({ ...current, items: current.items.map(row => row.key === item.key ? next : row) }))}
        onRemove={stage.items.length > 1 ? () => updateStageDraft(stage.key, current => ({ ...current, items: current.items.filter(row => row.key !== item.key) })) : undefined} />)}
      <button type="button" className="secondary" onClick={() => updateStageDraft(stage.key, current => ({ ...current, items: [...current.items, emptyItem()] }))}>Adaugă manoperă în etapa {stageIndex + 1}</button>
    </section>)}
    <button type="button" className="secondary" onClick={() => setStages(current => [...current, emptyStage(`Etapa ${current.length + 1}`)])}>Adaugă etapă</button>
    <p className="muted">Tarifele și duratele sunt estimări preluate din catalog; planul nu creează manopere efectuate, încasări sau cronometre.</p>
    {error && <p className="error" role="alert">{error}</p>}
    <div className="tp-actions">
      <button type="submit" disabled={saving}>{saving ? 'Se salvează…' : 'Salvează planul'}</button>
      <button type="button" className="secondary" disabled={saving} onClick={onCancel}>Renunță</button>
    </div>
  </form>;
}

function Totals({ plan }: { plan: TreatmentPlan }) {
  return <div className="tp-totals" aria-label="Estimări plan">
    <p className="eyebrow">{plan.estimate_label}</p>
    {plan.totals.length === 0 && <p className="muted">Nicio manoperă activă.</p>}
    {plan.totals.map(total => <dl key={total.currency} className="tp-total-row">
      <div><dt>Total estimat</dt><dd data-testid={`estimated-${total.currency}`}>{total.estimated_total} {total.currency}</dd></div>
      <div><dt>Efectuat (estimat)</dt><dd>{total.completed_estimate} {total.currency}</dd></div>
      <div><dt>Rămas (estimat)</dt><dd>{total.remaining_estimate} {total.currency}</dd></div>
      <div><dt>Durată estimată</dt><dd>{total.estimated_minutes} min</dd></div>
    </dl>)}
  </div>;
}

function ProgressBar({ percent, label }: { percent: number; label: string }) {
  return <div className="tp-progress" role="progressbar" aria-label={label} aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}><span style={{ width: `${percent}%` }} /><em>{percent}%</em></div>;
}

type Run = (label: string, action: () => Promise<TreatmentPlan>) => Promise<boolean>;

function ItemCard({ plan, item, candidates, timezone, busy, run }: { plan: TreatmentPlan; item: PlanItem; candidates?: LinkCandidates; timezone: string; busy: boolean; run: Run }) {
  const editable = ['draft', 'accepted', 'in_progress'].includes(plan.status);
  const canFulfil = ['accepted', 'in_progress'].includes(plan.status);
  const [appointmentId, setAppointmentId] = useState('');
  const [performedId, setPerformedId] = useState('');
  const [reason, setReason] = useState('');
  const [priority, setPriority] = useState<Priority>(item.priority);
  const [price, setPrice] = useState(item.estimated_price);
  const [duration, setDuration] = useState(String(item.estimated_duration_minutes));
  const compatible = (candidates?.performed_procedures ?? []).filter(row => row.procedure_catalog_id === item.procedure_catalog_id
    && (row.tooth_fdi ?? null) === item.tooth_fdi && sameSurfaces(row.tooth_surface, item.tooth_surface) && !row.linked_item_id);
  const estimateChanged = priority !== item.priority || price !== item.estimated_price || duration !== String(item.estimated_duration_minutes);
  return <article className={`tp-item tp-progress-${item.progress}`} aria-label={`Manoperă planificată ${item.procedure_code} ${item.tooth_fdi ?? ''}`.trim()}>
    <div className="detail-heading">
      <div>
        <h4>{item.procedure_code} · {item.procedure_name}</h4>
        <p className="muted">{location(item.tooth_fdi, item.tooth_surface)} · Prioritate {PRIORITY_LABELS[item.priority].toLowerCase()} · Estimare {item.estimated_price} {item.currency} · {item.estimated_duration_minutes} min</p>
      </div>
      <span className={`tp-badge tp-badge-${item.progress}`} data-testid="item-progress">{PROGRESS_LABELS[item.progress]}</span>
    </div>
    {item.notes && <p className="clinical-note-text">{item.notes}</p>}
    {item.appointment && <p className="tp-link">Vizită: {formatDate(item.appointment.starts_at, timezone)} · {item.appointment.appointment_type} · {item.appointment.status} <span className="muted">(asociată de {item.appointment.linked_by ?? '—'}, {formatDate(item.appointment.linked_at, timezone)})</span></p>}
    {item.performed_procedure && <p className="tp-link">Manoperă efectuată: {item.performed_procedure.procedure_code} · {location(item.performed_procedure.tooth_fdi, item.performed_procedure.tooth_surface)} · status {item.performed_procedure.status} <span className="muted">(asociată de {item.performed_procedure.linked_by ?? '—'}, {formatDate(item.performed_procedure.linked_at, timezone)})</span></p>}
    {item.status === 'cancelled' && <p className="muted">Anulat de {item.cancelled_by ?? '—'} la {formatDate(item.cancelled_at, timezone)}: {item.cancel_reason}</p>}
    {editable && item.status === 'active' && <div className="tp-item-actions">
      <div className="tp-inline">
        <select aria-label={`Vizită pentru ${item.procedure_code}`} value={appointmentId} onChange={event => setAppointmentId(event.target.value)}>
          <option value="">Alege o vizită existentă a pacientului</option>
          {candidates?.appointments.map(row => <option key={row.id} value={row.id}>{formatDate(row.starts_at, timezone)} · {row.appointment_type} · {row.status}</option>)}
        </select>
        <button type="button" disabled={busy || !appointmentId} onClick={() => void run('Asociere vizită', () => linkAppointment(item.id, appointmentId)).then(ok => ok && setAppointmentId(''))}>Asociază vizita</button>
        {item.appointment && <button type="button" className="secondary" disabled={busy} onClick={() => void run('Disociere vizită', () => linkAppointment(item.id, null))}>Elimină vizita</button>}
      </div>
      {canFulfil && !item.performed_procedure && <div className="tp-inline">
        <select aria-label={`Manoperă efectuată pentru ${item.procedure_code}`} value={performedId} onChange={event => setPerformedId(event.target.value)}>
          <option value="">{compatible.length ? 'Alege manopera efectuată compatibilă' : 'Nicio manoperă compatibilă (serviciu, dinte, suprafețe)'}</option>
          {compatible.map(row => <option key={row.id} value={row.id}>{row.procedure_code} · {location(row.tooth_fdi, row.tooth_surface)} · {row.status} · {formatDate(row.created_at, timezone)}</option>)}
        </select>
        <button type="button" disabled={busy || !performedId} onClick={() => void run('Asociere manoperă efectuată', () => linkPerformed(item.id, performedId)).then(ok => ok && setPerformedId(''))}>Asociază manopera</button>
      </div>}
      <div className="tp-inline">
        <input aria-label={`Motiv pentru ${item.procedure_code}`} placeholder="Motiv (anulare sau disociere)" value={reason} onChange={event => setReason(event.target.value)} />
        {item.performed_procedure
          ? <button type="button" className="secondary" disabled={busy || !reason.trim()} onClick={() => void run('Disociere manoperă', () => unlinkPerformed(item.id, reason.trim())).then(ok => ok && setReason(''))}>Disociază manopera efectuată</button>
          : <button type="button" className="secondary" disabled={busy || !reason.trim()} onClick={() => void run('Anulare manoperă planificată', () => cancelItem(item.id, reason.trim())).then(ok => ok && setReason(''))}>Anulează manopera planificată</button>}
        {!item.performed_procedure && !item.appointment && <button type="button" className="secondary" disabled={busy} onClick={() => void run('Ștergere manoperă', () => deleteItem(item.id))}>Șterge</button>}
      </div>
      <div className="tp-inline">
        <select aria-label={`Prioritate ${item.procedure_code}`} value={priority} onChange={event => setPriority(event.target.value as Priority)}>{Object.entries(PRIORITY_LABELS).map(([value, text]) => <option key={value} value={value}>{text}</option>)}</select>
        <input aria-label={`Tarif estimat ${item.procedure_code}`} value={price} onChange={event => setPrice(event.target.value)} />
        <input aria-label={`Durată ${item.procedure_code}`} value={duration} onChange={event => setDuration(event.target.value)} />
        <button type="button" className="secondary" disabled={busy || !estimateChanged} onClick={() => void run('Modificare estimare', () => updateItem(item.id, { priority, estimated_price: price.trim(), estimated_duration_minutes: Number(duration) }))}>Salvează estimarea</button>
      </div>
    </div>}
  </article>;
}

function AddItemForm({ plan, stageId, catalog, busy, run }: { plan: TreatmentPlan; stageId: string; catalog: ProcedureCatalog[]; busy: boolean; run: Run }) {
  const [item, setItem] = useState<ItemDraft>(emptyItem);
  const key = useRef(newKey());
  const [problem, setProblem] = useState('');
  return <div className="tp-add-item">
    <ItemEditor item={item} catalog={catalog} label={`Manoperă nouă etapa ${plan.stages.find(stage => stage.id === stageId)?.position ?? ''}`} onChange={setItem} />
    {problem && <p className="error" role="alert">{problem}</p>}
    <button type="button" disabled={busy} onClick={() => {
      const issue = draftProblem(item);
      setProblem(issue ?? '');
      if (issue) return;
      void run('Adăugare manoperă', () => addItem(plan.id, stageId, toInput(item), key.current)).then(ok => { if (ok) { setItem(emptyItem()); key.current = newKey(); } });
    }}>Adaugă manopera planificată</button>
  </div>;
}

function Section({ title, eyebrow, children }: { title: string; eyebrow?: string; children: ReactNode }) {
  return <section className="tp-section">{eyebrow && <p className="eyebrow">{eyebrow}</p>}<h3>{title}</h3>{children}</section>;
}

export function PlanDetail({ planId, patientId, catalog, timezone, onBack }: { planId: string; patientId: string; catalog: ProcedureCatalog[]; timezone: string; onBack: () => void }) {
  const client = useQueryClient();
  const plan = useQuery({ queryKey: ['treatment-plan', planId], queryFn: () => getPlan(planId) });
  const candidates = useQuery({ queryKey: ['treatment-plan-candidates', patientId], queryFn: () => getLinkCandidates(patientId) });
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [acceptNote, setAcceptNote] = useState('');
  const [cancelReason, setCancelReason] = useState('');
  const [stageTitle, setStageTitle] = useState('');
  const [addingTo, setAddingTo] = useState<string | null>(null);

  const run: Run = async (label, action) => {
    if (lock.current) return false;
    lock.current = true;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const updated = await action();
      if (updated.patient_id !== patientId) throw new Error('Răspunsul aparține altui pacient; reîncarcă pagina.');
      client.setQueryData(['treatment-plan', planId], updated);
      void client.invalidateQueries({ queryKey: ['treatment-plans', patientId] });
      void client.invalidateQueries({ queryKey: ['treatment-plan-candidates', patientId] });
      setNotice(`${label}: salvat.`);
      return true;
    } catch (caught) {
      setError(`${label}: ${apiErrorMessage(caught)}`);
      return false;
    } finally {
      lock.current = false;
      setBusy(false);
    }
  };

  if (plan.isLoading) return <p className="muted">Se încarcă planul…</p>;
  if (plan.isError || !plan.data) return <div><p className="error" role="alert">{apiErrorMessage(plan.error)}</p><button className="secondary" onClick={onBack}>Înapoi la planuri</button></div>;
  const data = plan.data;
  const editable = ['draft', 'accepted', 'in_progress'].includes(data.status);
  const current = data.acceptances.find(row => row.is_current);
  return <div className="tp-detail" aria-label={`Plan ${data.title}`}>
    <div className="detail-heading">
      <div>
        <p className="eyebrow">PLAN DE TRATAMENT · VERSIUNEA {data.version}</p>
        <h3>{data.title}</h3>
        <p className="muted">Creat de {data.created_by ?? '—'} la {formatDate(data.created_at, timezone)}</p>
      </div>
      <div className="tp-actions">
        <span className={`tp-badge tp-status-${data.status}`} data-testid="plan-status">{STATUS_LABELS[data.status]}</span>
        <button className="secondary" onClick={onBack}>Înapoi la planuri</button>
      </div>
    </div>
    {data.notes && <p className="clinical-note-text">{data.notes}</p>}
    {current && <p className="tp-accepted" data-testid="accepted-version">Versiunea {current.version} acceptată · consemnat de {current.accepted_by} la {formatDate(current.accepted_at, timezone)} · „{current.note}”</p>}
    {data.needs_acceptance && data.accepted_version && <p className="notice-text" role="status">Planul a fost modificat după acceptarea versiunii {data.accepted_version}. Versiunea {data.version} necesită o nouă acceptare; istoricul acceptării anterioare este păstrat.</p>}
    {data.status === 'cancelled' && <p className="notice-text">Anulat de {data.cancelled_by} la {formatDate(data.cancelled_at, timezone)}: {data.cancel_reason}. Vizitele și manoperele reale nu au fost modificate.</p>}
    {data.status === 'completed' && <p className="notice-text">Finalizat de {data.completed_by} la {formatDate(data.completed_at, timezone)}.</p>}
    <Totals plan={data} />
    <ProgressBar percent={data.progress.percent} label="Progres plan" />
    <p className="muted" data-testid="plan-progress">{data.progress.done} efectuate · {data.progress.cancelled} anulate · {data.progress.remaining} rămase din {data.progress.total}</p>
    {error && <p className="error" role="alert">{error}</p>}
    {notice && <p className="muted" role="status">{notice}</p>}
    {candidates.isError && <p className="error" role="alert">Vizitele și manoperele pacientului nu au putut fi încărcate: {apiErrorMessage(candidates.error)}</p>}

    {data.stages.map((stage, index) => <section key={stage.id} className="tp-stage" aria-label={`Etapa ${stage.position}`}>
      <div className="detail-heading">
        <div><h4>Etapa {stage.position}: {stage.title}</h4><p className="muted">{stage.progress.done}/{stage.progress.total - stage.progress.cancelled} efectuate</p></div>
        {editable && <div className="tp-actions">
          <button type="button" className="secondary" disabled={busy || index === 0} onClick={() => void run('Reordonare etapă', () => updateStage(data.id, stage.id, { move: 'up' }))}>Sus</button>
          <button type="button" className="secondary" disabled={busy || index === data.stages.length - 1} onClick={() => void run('Reordonare etapă', () => updateStage(data.id, stage.id, { move: 'down' }))}>Jos</button>
          {stage.items.length === 0 && <button type="button" className="secondary" disabled={busy} onClick={() => void run('Ștergere etapă', () => deleteStage(data.id, stage.id))}>Șterge etapa</button>}
          <button type="button" className="secondary" onClick={() => setAddingTo(addingTo === stage.id ? null : stage.id)}>{addingTo === stage.id ? 'Închide' : 'Adaugă manoperă'}</button>
        </div>}
      </div>
      <ProgressBar percent={stage.progress.percent} label={`Progres etapa ${stage.position}`} />
      {stage.items.length === 0 && <p className="muted">Etapa nu are manopere planificate.</p>}
      {stage.items.map(item => <ItemCard key={`${item.id}:${item.estimated_price}:${item.priority}:${item.estimated_duration_minutes}`} plan={data} item={item} candidates={candidates.data} timezone={timezone} busy={busy} run={run} />)}
      {editable && addingTo === stage.id && <AddItemForm plan={data} stageId={stage.id} catalog={catalog} busy={busy} run={run} />}
    </section>)}

    {editable && <div className="tp-inline">
      <input aria-label="Titlu etapă nouă" placeholder="Titlu etapă nouă" value={stageTitle} onChange={event => setStageTitle(event.target.value)} />
      <button type="button" className="secondary" disabled={busy || !stageTitle.trim()} onClick={() => void run('Adăugare etapă', () => addStage(data.id, stageTitle.trim())).then(ok => ok && setStageTitle(''))}>Adaugă etapă</button>
    </div>}

    {editable && data.needs_acceptance && <Section title={`Consemnează acceptarea versiunii ${data.version}`} eyebrow="ACCEPTARE INTERNĂ">
      <p className="muted">Consemnare internă făcută de personal (cine, când, observație). Nu este semnătură electronică și nu dovedește consimțământul medical al pacientului.</p>
      <textarea aria-label="Observație acceptare" placeholder="Ex.: acord exprimat verbal în cabinet, după explicarea etapelor și estimărilor" value={acceptNote} onChange={event => setAcceptNote(event.target.value)} />
      <button type="button" disabled={busy || !acceptNote.trim()} onClick={() => void run('Acceptare', () => acceptPlan(data.id, acceptNote.trim(), data.version)).then(ok => ok && setAcceptNote(''))}>Consemnează acceptarea versiunii {data.version}</button>
    </Section>}

    {editable && <Section title="Finalizare sau anulare" eyebrow="STATUS">
      <button type="button" disabled={busy || !data.can_complete} onClick={() => void run('Finalizare plan', () => completePlan(data.id))}>Finalizează planul</button>
      {!data.can_complete && data.completion_blocker && <p className="muted" data-testid="completion-blocker">Finalizarea nu este încă permisă: {data.completion_blocker}</p>}
      <div className="tp-inline">
        <input aria-label="Motiv anulare plan" placeholder="Motivul anulării planului" value={cancelReason} onChange={event => setCancelReason(event.target.value)} />
        <button type="button" className="secondary" disabled={busy || !cancelReason.trim()} onClick={() => void run('Anulare plan', () => cancelPlan(data.id, cancelReason.trim()))}>Anulează planul</button>
      </div>
      <p className="muted">Anularea planului nu anulează vizitele sau manoperele reale și nu șterge istoricul.</p>
    </Section>}

    <Section title="Acceptări consemnate" eyebrow="ISTORIC VERSIUNI">
      {data.acceptances.length === 0 && <p className="muted">Nicio versiune acceptată încă.</p>}
      {data.acceptances.map(row => <article key={row.id} className="tp-acceptance" data-testid="acceptance-row">
        <strong>Versiunea {row.version}</strong> {row.is_current ? '(curentă)' : '(înlocuită de o versiune ulterioară)'} · {row.accepted_by} · {formatDate(row.accepted_at, timezone)} · „{row.note}”
        <p className="muted">{row.snapshot.totals.map(total => `${total.estimated_total} ${total.currency}`).join(' · ') || '—'} · {row.snapshot.stages.reduce((sum, stage) => sum + stage.items.length, 0)} manopere</p>
      </article>)}
    </Section>
    <Section title="Istoric plan" eyebrow="AUDIT">
      <ul className="tp-history">{data.history.map(event => <li key={event.id}>{formatDate(event.occurred_at, timezone)} · {ACTION_LABELS[event.action] ?? event.action} · v{event.plan_version} · {event.actor ?? '—'}{typeof event.details.reason === 'string' ? ` · motiv: ${event.details.reason}` : ''}</li>)}</ul>
    </Section>
  </div>;
}

export function TreatmentPlansPanel({ patientId, timezone }: { patientId: string; timezone: string }) {
  const client = useQueryClient();
  const plans = useQuery({ queryKey: ['treatment-plans', patientId], queryFn: () => listPatientPlans(patientId) });
  const catalog = useQuery({ queryKey: ['procedures', 'treatment-plan-active'], queryFn: () => listProcedures(undefined, true, 200) });
  const [mode, setMode] = useState<'list' | 'new' | string>('list');
  const activeCatalog = (catalog.data ?? []).filter(row => row.is_active);
  return <section className="card clinical-record-section tp-panel" aria-label="Planuri de tratament">
    <div className="detail-heading">
      <div><p className="eyebrow">T08 · PLANIFICARE (NU EXECUȚIE)</p><h2>Planuri de tratament</h2></div>
      {mode === 'list' && <button onClick={() => setMode('new')} disabled={catalog.isLoading}>Plan nou</button>}
    </div>
    {catalog.isError && <p className="error" role="alert">Catalogul nu a putut fi încărcat: {apiErrorMessage(catalog.error)}</p>}
    {mode === 'new' && <PlanForm patientId={patientId} catalog={activeCatalog} onCancel={() => setMode('list')} onCreated={plan => {
      client.setQueryData(['treatment-plan', plan.id], plan);
      void client.invalidateQueries({ queryKey: ['treatment-plans', patientId] });
      setMode(plan.id);
    }} />}
    {mode !== 'list' && mode !== 'new' && <PlanDetail key={mode} planId={mode} patientId={patientId} catalog={activeCatalog} timezone={timezone} onBack={() => setMode('list')} />}
    {mode === 'list' && <>
      {plans.isLoading && <p className="muted">Se încarcă planurile…</p>}
      {plans.isError && <p className="error" role="alert">{apiErrorMessage(plans.error)}</p>}
      {plans.data?.length === 0 && <p className="muted">Pacientul nu are planuri de tratament.</p>}
      <div className="clinical-list">{plans.data?.map(row => <article key={row.id} className="clinical-list-item detail-heading" aria-label={`Rezumat ${row.title}`}>
        <div>
          <h3>{row.title}</h3>
          <p className="muted">{STATUS_LABELS[row.status]} · v{row.version}{row.needs_acceptance ? ' · necesită acceptare' : ''} · {row.progress.done}/{row.progress.total - row.progress.cancelled} efectuate · {row.totals.map(total => `${total.estimated_total} ${total.currency} estimat`).join(' · ') || 'fără estimare'}</p>
        </div>
        <button className="secondary" onClick={() => setMode(row.id)}>Deschide planul</button>
      </article>)}</div>
    </>}
  </section>;
}
