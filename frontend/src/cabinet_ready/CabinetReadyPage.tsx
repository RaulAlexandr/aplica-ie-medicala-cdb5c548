import { FormEvent, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AxiosError } from 'axios';
import { apiErrorMessage, getClinic, listProcedures, User } from '../api/client';
import {
  addBlock, CabinetCard, ChecklistKind, ChecklistLine, closeBlock, confirmItem, decideParticularity, getBoard, getChecklist,
  PreparationLine, proposeParticularity, revokeItem, saveChecklist, startPreparation,
} from './api';
import './cabinet-ready.css';

const MANAGERS = ['clinic_manager', 'administrator'];
const WORKERS = ['doctor', 'assistant', ...MANAGERS];
const PROPOSERS = ['doctor', 'assistant'];
const KIND_LABELS: Record<ChecklistKind, string> = { material: 'Material', instrument: 'Instrument', document: 'Document', action: 'Acțiune' };
const STERILIZATION_NOTE = 'Bifa de pregătire nu înseamnă trasabilitate de sterilizare.';

export function cabinetErrorText(error: unknown): string {
  if (error instanceof AxiosError && error.response) {
    const detail = error.response.data?.detail;
    if (typeof detail !== 'string' && !Array.isArray(detail)) return 'Serverul a întâmpinat o eroare. Valorile rămân; încercați din nou.';
    if (error.response.status === 422 && Array.isArray(detail)) return 'Verificați câmpurile completate.';
  }
  return apiErrorMessage(error);
}

function newRequestId(): string {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map(value => value.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

/** One write at a time per action. A failed request keeps its key, so a retry cannot apply twice. */
function useAction(onDone: () => Promise<unknown>) {
  const keys = useRef(new Map<string, string>());
  const running = useRef(false);
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<{ scope: string; text: string } | null>(null);
  const run = async (scope: string, fingerprint: string, action: (requestId: string) => Promise<unknown>) => {
    if (running.current) return false;
    running.current = true;
    setPending(scope);
    setError(null);
    const keyName = `${scope}:${fingerprint}`;
    const requestId = keys.current.get(keyName) ?? newRequestId();
    keys.current.set(keyName, requestId);
    try {
      await action(requestId);
      keys.current.delete(keyName);
      await onDone();
      return true;
    } catch (caught) {
      setError({ scope, text: cabinetErrorText(caught) });
      return false;
    } finally {
      running.current = false;
      setPending(null);
    }
  };
  const errorAt = (scope: string) => error?.scope === scope ? <p role="alert" className="cabinet-error">{error.text}</p> : null;
  return { run, pending, errorAt };
}

function ItemRow({ line, canWrite, action }: { line: PreparationLine; canWrite: boolean; action: ReturnType<typeof useAction> }) {
  const [reason, setReason] = useState('');
  const confirmScope = `confirm:${line.id}`, revokeScope = `revoke:${line.id}`;
  return <div className="cabinet-line">
    <p><strong>{line.label}</strong> <span className="cabinet-muted">· {KIND_LABELS[line.kind]} · {line.required ? 'obligatoriu' : 'opțional'} · {line.prep_minutes} min{line.procedure_name ? ` · ${line.procedure_name}` : ''}{line.from_particularity ? ' · particularitate aprobată' : ''}</span></p>
    {line.confirmed && <p className="cabinet-muted">Confirmat de {line.confirmed_by}</p>}
    {canWrite && line.id && !line.confirmed && <div className="cabinet-actions"><button type="button" disabled={action.pending !== null} onClick={() => void action.run(confirmScope, '', id => confirmItem(line.id as string, id))}>Confirm fizic: {line.label}</button></div>}
    {canWrite && line.id && line.confirmed && <form className="cabinet-actions" onSubmit={async event => { event.preventDefault(); if (await action.run(revokeScope, reason.trim(), id => revokeItem(line.id as string, reason.trim(), id))) setReason(''); }}>
      <label>Motivul anulării<input required maxLength={500} value={reason} onChange={event => setReason(event.target.value)} /></label>
      <button className="secondary" disabled={action.pending !== null || !reason.trim()}>Anulează confirmarea</button>
    </form>}
    {action.errorAt(confirmScope)}{action.errorAt(revokeScope)}
  </div>;
}

function BlockClose({ blockId, action }: { blockId: string; action: ReturnType<typeof useAction> }) {
  const [reason, setReason] = useState('');
  const scope = `close:${blockId}`;
  return <form className="cabinet-actions" onSubmit={async event => { event.preventDefault(); if (await action.run(scope, reason.trim(), id => closeBlock(blockId, reason.trim(), id))) setReason(''); }}>
    <label>Cum s-a rezolvat<input required maxLength={500} value={reason} onChange={event => setReason(event.target.value)} /></label>
    <button className="secondary" disabled={action.pending !== null || !reason.trim()}>Închide blocajul</button>
    {action.errorAt(scope)}
  </form>;
}

function ParticularityForm({ appointmentId, action }: { appointmentId: string; action: ReturnType<typeof useAction> }) {
  const [text, setText] = useState(''), [withItem, setWithItem] = useState(false), [kind, setKind] = useState<ChecklistKind>('action');
  const [label, setLabel] = useState(''), [required, setRequired] = useState(true), [minutes, setMinutes] = useState('5');
  const scope = `particularity:${appointmentId}`;
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const input = withItem ? { text: text.trim(), item_kind: kind, item_label: label.trim(), item_required: required, item_minutes: Number(minutes) } : { text: text.trim() };
    if (await action.run(scope, JSON.stringify(input), id => proposeParticularity(appointmentId, input, id))) { setText(''); setLabel(''); setWithItem(false); }
  };
  return <form className="cabinet-form" onSubmit={event => void submit(event)}>
    <label>Particularitate propusă<input required maxLength={500} value={text} onChange={event => setText(event.target.value)} /></label>
    <label className="cabinet-check"><input type="checkbox" checked={withItem} onChange={event => setWithItem(event.target.checked)} /> Adaugă un element suplimentar</label>
    {withItem && <>
      <label>Tip<select value={kind} onChange={event => setKind(event.target.value as ChecklistKind)}>{Object.entries(KIND_LABELS).map(([value, name]) => <option key={value} value={value}>{name}</option>)}</select></label>
      <label>Denumire element<input required maxLength={200} value={label} onChange={event => setLabel(event.target.value)} /></label>
      <label className="cabinet-check"><input type="checkbox" checked={required} onChange={event => setRequired(event.target.checked)} /> Obligatoriu</label>
      <label>Minute<input type="number" min="0" max="240" required value={minutes} onChange={event => setMinutes(event.target.value)} /></label>
    </>}
    <button className="secondary" disabled={action.pending !== null || !text.trim()}>Propune particularitatea</button>
    {action.errorAt(scope)}
  </form>;
}

function Card({ card, user, refresh }: { card: CabinetCard; user: User; refresh: () => Promise<unknown> }) {
  const action = useAction(refresh);
  const [blockReason, setBlockReason] = useState(''), [checkWith, setCheckWith] = useState('');
  const canWrite = WORKERS.includes(user.role);
  const startScope = 'start', blockScope = 'block';
  const canStart = canWrite && !card.started && (card.items?.length ?? 0) > 0 && !(card.missing ?? []).some(text => text.includes('nu are checklist'));
  const manualBlocks = card.blockers.filter(row => row.type === 'manual' && row.id);
  return <article className={`cabinet-card state-${card.state}`} aria-label={`Pregătire ${card.local_time} ${card.patient_name}`}>
    <div className="cabinet-card-head"><strong>{card.local_time} · {card.patient_name}</strong><span className={`cabinet-badge state-${card.state}`}>{card.state_label} · {card.counter}</span></div>
    {canWrite
      ? <div className="cabinet-missing"><h3>Ce lipsește</h3>{card.missing?.length ? <ul>{card.missing.map(text => <li key={text}>{text}</li>)}</ul> : <p>Nimic. Toate elementele obligatorii sunt confirmate.</p>}</div>
      : card.blockers.length > 0 && <div className="cabinet-missing"><h3>Blocaje</h3><ul>{card.blockers.map(row => <li key={row.type}>{row.type_label}</li>)}</ul></div>}
    <p className="cabinet-muted">{card.room_name} · {card.doctor_name}</p>
    <p className="cabinet-muted">Începe pregătirea până la {card.start_by_local}{card.late && <span className="cabinet-late"> · Întârziat</span>}</p>
    {card.warnings?.map(row => <p className="cabinet-warning" key={row.text}>{row.text} Verifică cu: {row.check_with}.</p>)}
    {canStart && <div className="cabinet-actions"><button type="button" disabled={action.pending !== null} onClick={() => void action.run(startScope, card.explanation ?? '', id => startPreparation(card.appointment_id, id))}>{card.explanation ? 'Reîncepe pregătirea' : 'Începe pregătirea'}</button>{action.errorAt(startScope)}</div>}
    {canWrite && card.items?.map((line, index) => <ItemRow key={line.id ?? `${line.label}-${index}`} line={line} canWrite={canWrite} action={action} />)}
    {canWrite && manualBlocks.map(row => <div className="cabinet-line" key={row.id}><p><strong>Blocaj manual:</strong> {row.text}</p><BlockClose blockId={row.id as string} action={action} /></div>)}
    {canWrite && !!card.particularities?.length && <div className="cabinet-line"><h4>Particularități</h4>{card.particularities.map(row => <div key={row.id} className="cabinet-form">
      <p>{row.text}{row.item_label ? ` · element: ${row.item_label}` : ''} <span className="cabinet-muted">· {row.status === 'pending' ? 'în așteptare' : row.status === 'approved' ? 'aprobată' : 'respinsă'}</span></p>
      {row.status === 'pending' && user.id === card.doctor_id && <div className="cabinet-actions">
        <button type="button" disabled={action.pending !== null} onClick={() => void action.run(`decision:${row.id}`, 'approved', id => decideParticularity(row.id, 'approved', id))}>Aprobă</button>
        <button type="button" className="secondary" disabled={action.pending !== null} onClick={() => void action.run(`decision:${row.id}`, 'rejected', id => decideParticularity(row.id, 'rejected', id))}>Respinge</button>
      </div>}
      {action.errorAt(`decision:${row.id}`)}
    </div>)}</div>}
    {canWrite && <details><summary>Adaugă blocaj manual</summary><form className="cabinet-form" onSubmit={async event => { event.preventDefault(); if (await action.run(blockScope, `${blockReason.trim()}|${checkWith.trim()}`, id => addBlock(card.appointment_id, blockReason.trim(), checkWith.trim(), id))) { setBlockReason(''); setCheckWith(''); } }}>
      <label>Problema<input required maxLength={500} value={blockReason} onChange={event => setBlockReason(event.target.value)} /></label>
      <label>Persoana sau locul de verificat<input required maxLength={200} value={checkWith} onChange={event => setCheckWith(event.target.value)} /></label>
      <button className="secondary" disabled={action.pending !== null || !blockReason.trim() || !checkWith.trim()}>Adaugă blocajul</button>
      {action.errorAt(blockScope)}
    </form></details>}
    {PROPOSERS.includes(user.role) && <details><summary>Propune o particularitate</summary><ParticularityForm appointmentId={card.appointment_id} action={action} /></details>}
  </article>;
}

function ChecklistAdmin() {
  const qc = useQueryClient();
  const procedures = useQuery({ queryKey: ['procedures', true], queryFn: () => listProcedures(undefined, true, 200) });
  const [procedureId, setProcedureId] = useState('');
  const [draft, setDraft] = useState<ChecklistLine[] | null>(null);
  const checklist = useQuery({ queryKey: ['cabinet-checklist', procedureId], queryFn: () => getChecklist(procedureId), enabled: Boolean(procedureId) });
  const lines = draft ?? checklist.data?.lines ?? [];
  const action = useAction(async () => { setDraft(null); await qc.invalidateQueries({ queryKey: ['cabinet-checklist', procedureId] }); await qc.invalidateQueries({ queryKey: ['cabinet-board'] }); });
  const update = (index: number, patch: Partial<ChecklistLine>) => setDraft(lines.map((line, position) => position === index ? { ...line, ...patch } : line));
  const save = useMutation({ mutationFn: async () => action.run('save', JSON.stringify({ procedureId, version: checklist.data?.version, lines }), id => saveChecklist(procedureId, checklist.data?.version ?? 0, lines, id)) });
  return <section className="card cabinet-form"><h2>Administrare checklisturi</h2>
    <label>Procedură<select value={procedureId} onChange={event => { setProcedureId(event.target.value); setDraft(null); }}><option value="">Alege procedura</option>{procedures.data?.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}</select></label>
    {procedureId && <><p className="cabinet-muted">Versiunea curentă: {checklist.data?.version ?? 0}. Salvarea creează o versiune nouă; pregătirile începute nu se schimbă.</p>
      {lines.map((line, index) => <div className="cabinet-editor-line" key={index}>
        <label>Tip<select value={line.kind} onChange={event => update(index, { kind: event.target.value as ChecklistKind })}>{Object.entries(KIND_LABELS).map(([value, name]) => <option key={value} value={value}>{name}</option>)}</select></label>
        <label>Denumire<input maxLength={200} value={line.label} onChange={event => update(index, { label: event.target.value })} /></label>
        <label className="cabinet-check"><input type="checkbox" checked={line.required} onChange={event => update(index, { required: event.target.checked })} /> Obligatoriu</label>
        <label>Minute<input type="number" min="0" max="240" value={line.prep_minutes} onChange={event => update(index, { prep_minutes: Number(event.target.value) })} /></label>
        <button type="button" className="secondary" onClick={() => setDraft(lines.filter((_, position) => position !== index))}>Elimină</button>
      </div>)}
      <div className="cabinet-actions"><button type="button" className="secondary" onClick={() => setDraft([...lines, { kind: 'action', label: '', required: true, prep_minutes: 5 }])}>Adaugă element</button>
        <button type="button" disabled={action.pending !== null || !lines.length || lines.some(line => !line.label.trim())} onClick={() => save.mutate()}>Salvează versiunea</button></div>
      {action.errorAt('save')}</>}
  </section>;
}

function clinicToday(timezone: string) {
  return new Intl.DateTimeFormat('en-CA', { timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
}

export function CabinetReadyPage({ user }: { user: User }) {
  const qc = useQueryClient();
  const clinic = useQuery({ queryKey: ['clinic'], queryFn: getClinic });
  const [chosenDay, setChosenDay] = useState(''), [roomId, setRoomId] = useState(''), [view, setView] = useState<'day' | 'room' | 'admin'>('day');
  const day = chosenDay || (clinic.data ? clinicToday(clinic.data.timezone) : '');
  const board = useQuery({ queryKey: ['cabinet-board', day], queryFn: () => getBoard(day), enabled: Boolean(day), refetchInterval: 30000, refetchIntervalInBackground: false });
  const refresh = () => qc.invalidateQueries({ queryKey: ['cabinet-board', day] });
  const cards = useMemo(() => (board.data?.cards ?? []).filter(card => !roomId || card.room_id === roomId), [board.data, roomId]);
  const groups = useMemo(() => {
    if (view !== 'room') return [{ key: 'day', title: null as string | null, cards: [...cards].sort((a, b) => a.starts_at.localeCompare(b.starts_at)) }];
    const byRoom = new Map<string, CabinetCard[]>();
    for (const card of cards) byRoom.set(card.room_name, [...(byRoom.get(card.room_name) ?? []), card]);
    return [...byRoom.entries()].map(([title, rows]) => ({ key: title, title: title as string | null, cards: rows }));
  }, [cards, view]);
  return <div className="cabinet-page">
    <h1>Cabinet Ready</h1>
    <p className="cabinet-note">{STERILIZATION_NOTE} Stocul suficient nu confirmă nimic: numai o persoană confirmă pregătirea.</p>
    <div className="cabinet-tabs">
      <button type="button" className="secondary" aria-pressed={view === 'day'} onClick={() => setView('day')}>Pe zi</button>
      <button type="button" className="secondary" aria-pressed={view === 'room'} onClick={() => setView('room')}>Pe cabinet</button>
      {MANAGERS.includes(user.role) && <button type="button" className="secondary" aria-pressed={view === 'admin'} onClick={() => setView('admin')}>Checklisturi</button>}
    </div>
    {view === 'admin' ? <ChecklistAdmin /> : <>
      <div className="cabinet-toolbar">
        <label>Ziua<input type="date" value={day} onChange={event => setChosenDay(event.target.value)} /></label>
        <label>Cabinet<select value={roomId} onChange={event => setRoomId(event.target.value)}><option value="">Toate cabinetele</option>{board.data?.rooms.map(room => <option key={room.id} value={room.id}>{room.name}</option>)}</select></label>
      </div>
      {board.isLoading && <p>Se încarcă…</p>}
      {board.isError && <p role="alert" className="cabinet-error">{cabinetErrorText(board.error)}</p>}
      {board.isSuccess && !cards.length && <p>Nu există programări active în această zi.</p>}
      {groups.map(group => <section className="cabinet-group" key={group.key}>{group.title && <h2>{group.title}</h2>}<div className="cabinet-cards">{group.cards.map(card => <Card key={card.appointment_id} card={card} user={user} refresh={refresh} />)}</div></section>)}
    </>}
  </div>;
}
