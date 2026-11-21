import { FormEvent, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiErrorMessage, getClinic, listRooms, User } from '../api/client';
import * as team from '../team/api';
import { clinicDate, formatDateTime } from '../team/TeamPage';
import * as collab from './api';
import './collab.css';

export const POLL_MS = 15000;
export const MEDICAL_NOTICE = 'Nu scrie date medicale în chat; folosește dosarul pacientului';
const MAX_MESSAGE = 4000;
const MANAGERS = ['clinic_manager', 'administrator'];
const KIND_LABELS: Record<collab.ConversationKind, string> = { direct: 'Direct', group: 'Grup', channel: 'Canal', room: 'Cabinet' };
export const STATUS_LABELS: Record<collab.TaskStatus, string> = { open: 'Deschisă', in_progress: 'În lucru', blocked: 'Blocată', resolved: 'Rezolvată', cancelled: 'Anulată' };
const CLOSED: collab.TaskStatus[] = ['resolved', 'cancelled'];
// TanStack Query skips the interval while the page is hidden when this flag is false.
export const POLL_OPTIONS = { refetchInterval: POLL_MS, refetchIntervalInBackground: false } as const;

function friendlyError(error: unknown): string {
  const message = apiErrorMessage(error);
  if (message.includes('ambiguous')) return 'Ora aleasă apare de două ori la trecerea la ora de iarnă. Alege mai jos prima sau a doua apariție.';
  if (message.includes('does not exist')) return 'Ora aleasă nu există în ziua trecerii la ora de vară. Alege altă oră.';
  return message;
}
const Failure = ({ error }: { error: unknown }) => error ? <p className="error" role="alert">{friendlyError(error)}</p> : null;

/** A ref blocks synchronously: `isPending` changes only after the next render. */
function useLockedMutate<T>(mutation: { mutateAsync: (variables: T) => Promise<unknown> }) {
  const busy = useRef(false);
  return (variables: T) => {
    if (busy.current) return;
    busy.current = true;
    void mutation.mutateAsync(variables).catch(() => undefined).finally(() => { busy.current = false; });
  };
}

function usePageVisible(): boolean {
  const [visible, setVisible] = useState(() => typeof document === 'undefined' || !document.hidden);
  useEffect(() => {
    if (typeof document === 'undefined') return undefined;
    const update = () => setVisible(!document.hidden);
    document.addEventListener('visibilitychange', update);
    return () => document.removeEventListener('visibilitychange', update);
  }, []);
  return visible;
}

export function CollabNavLink() {
  const query = useQuery({ queryKey: ['collab', 'summary'], queryFn: collab.getSummary, retry: 1, ...POLL_OPTIONS });
  const unread = query.data?.unread ?? 0;
  return <Link to="/messages">Mesaje și sarcini{unread > 0 && <span className="collab-badge" aria-label={`${unread} mesaje necitite`}>{unread > 99 ? '99+' : unread}</span>}</Link>;
}

type Target = { kind: 'conversation' | 'room'; id: string };

/** The draft and its request id survive a failed send: retrying the same text cannot duplicate it. */
export function Composer({ target, disabled, onSent }: { target: Target; disabled?: boolean; onSent: (message: collab.Message) => void }) {
  const [draft, setDraft] = useState(() => ({ text: '', requestId: collab.newRequestId() }));
  const send = useMutation({
    mutationFn: () => target.kind === 'room' ? collab.sendToRoom(target.id, draft.text, draft.requestId) : collab.sendMessage(target.id, draft.text, draft.requestId),
    onSuccess: message => { setDraft({ text: '', requestId: collab.newRequestId() }); onSent(message); },
  });
  const submitOnce = useLockedMutate<void>(send);
  // Another destination keeps the text, but it is a new send and needs its own request id.
  const destination = `${target.kind}:${target.id}`;
  const previous = useRef(destination);
  useEffect(() => {
    if (previous.current === destination) return;
    previous.current = destination;
    send.reset();
    setDraft(current => ({ ...current, requestId: collab.newRequestId() }));
  }, [destination, send]);
  function submit(event: FormEvent) {
    event.preventDefault();
    if (!draft.text.trim()) return;
    submitOnce();
  }
  return <form onSubmit={submit} aria-label="Scrie un mesaj">
    <p className="muted">{MEDICAL_NOTICE}</p>
    <label>Mesaj<textarea maxLength={MAX_MESSAGE} disabled={disabled} readOnly={send.isPending} value={draft.text} onChange={e => { send.reset(); setDraft({ text: e.target.value, requestId: collab.newRequestId() }); }} /></label>
    <Failure error={send.error} />
    <button disabled={disabled || send.isPending || !draft.text.trim()}>Trimite</button>
  </form>;
}

function AddMember({ conversation, members }: { conversation: collab.Conversation; members: team.TeamMember[] }) {
  const client = useQueryClient();
  const [person, setPerson] = useState('');
  const present = new Set(conversation.members.map(item => item.id));
  const add = useMutation({ mutationFn: () => collab.addMember(conversation.id, person), onSuccess: () => { setPerson(''); void client.invalidateQueries({ queryKey: ['collab', 'conversations'] }); } });
  const addOnce = useLockedMutate<void>(add);
  return <form className="collab-task-actions" aria-label="Adaugă membru" onSubmit={e => { e.preventDefault(); addOnce(); }}>
    <label>Adaugă membru<select required value={person} onChange={e => setPerson(e.target.value)}><option value="">Alege persoana</option>{members.filter(item => item.is_active && !present.has(item.id)).map(item => <option key={item.id} value={item.id}>{item.full_name}</option>)}</select></label>
    <button className="secondary" disabled={add.isPending || !person}>Adaugă</button>
    <Failure error={add.error} />
  </form>;
}

function Thread({ conversation, user, members, timezone }: { conversation: collab.Conversation; user: User; members: team.TeamMember[]; timezone: string }) {
  const client = useQueryClient();
  const [limit, setLimit] = useState(50);
  const visible = usePageVisible();
  const query = useQuery({ queryKey: ['collab', 'messages', conversation.id, limit], queryFn: () => collab.listMessages(conversation.id, limit), ...POLL_OPTIONS });
  const refresh = () => { void client.invalidateQueries({ queryKey: ['collab'] }); };
  const messages = query.data?.messages ?? [];
  const lastSeq = messages.length ? messages[messages.length - 1].seq : 0;
  const marked = useRef(0);
  useEffect(() => {
    if (!visible || lastSeq === 0 || lastSeq <= conversation.last_read_seq || lastSeq <= marked.current) return;
    marked.current = lastSeq;
    void collab.markRead(conversation.id, lastSeq).then(() => client.invalidateQueries({ queryKey: ['collab', 'summary'] })).then(() => client.invalidateQueries({ queryKey: ['collab', 'conversations'] })).catch(() => { marked.current = 0; });
  }, [visible, lastSeq, conversation.id, conversation.last_read_seq, client]);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { end.current?.scrollIntoView?.({ block: 'nearest' }); }, [lastSeq]);
  const manager = MANAGERS.includes(user.role);
  const canAdd = conversation.kind === 'channel' ? manager : conversation.kind === 'group' && (manager || conversation.created_by_id === user.id);
  const target: Target = conversation.kind === 'room' && conversation.room_id ? { kind: 'room', id: conversation.room_id } : { kind: 'conversation', id: conversation.id };
  return <section className="card" aria-label="Conversație">
    <h2>{conversation.title}</h2>
    <p className="muted">{KIND_LABELS[conversation.kind]}{conversation.kind === 'room' ? ' · Răspunsul ajunge la cine e repartizat acum.' : conversation.kind === 'direct' ? '' : ` · ${conversation.members.map(m => m.is_active ? m.full_name : `${m.full_name} (inactiv)`).join(', ')}`}</p>
    {canAdd && <AddMember conversation={conversation} members={members} />}
    <Failure error={query.error} />
    {query.data?.has_more && <button className="secondary" onClick={() => setLimit(value => Math.min(value + 50, 500))} disabled={limit >= 500}>Mesaje mai vechi</button>}
    {query.data && messages.length === 0 && <p className="muted">Niciun mesaj încă.</p>}
    <div className="collab-thread">
      {messages.map(item => <article key={item.id} className={`collab-message${item.sender_id === user.id ? ' mine' : ''}`}>
        <small><strong>{item.sender}</strong> · {formatDateTime(item.created_at, timezone)}{item.recipients.length > 0 ? ` · către: ${item.recipients.join(', ')}` : ''}</small>
        <p>{item.body}</p>
      </article>)}
      <div ref={end} />
    </div>
    {conversation.can_write ? <Composer key={conversation.id} target={target} onSent={refresh} /> : <p className="notice-text" role="status">Persoana este inactivă. Poți citi istoricul, dar nu poți trimite mesaje noi.</p>}
  </section>;
}

function NewConversation({ user, members, onOpen }: { user: User; members: team.TeamMember[]; onOpen: (id: string) => void }) {
  const client = useQueryClient();
  const manager = MANAGERS.includes(user.role);
  const others = members.filter(item => item.is_active && item.id !== user.id);
  const [person, setPerson] = useState('');
  const [form, setForm] = useState<{ kind: 'group' | 'channel'; title: string; member_ids: string[] }>({ kind: 'group', title: '', member_ids: [] });
  const opened = (item: collab.Conversation) => { void client.invalidateQueries({ queryKey: ['collab', 'conversations'] }); onOpen(item.id); };
  const direct = useMutation({ mutationFn: () => collab.openDirect(person), onSuccess: item => { setPerson(''); opened(item); } });
  const create = useMutation({ mutationFn: () => collab.createConversation(form), onSuccess: item => { setForm({ kind: 'group', title: '', member_ids: [] }); opened(item); } });
  const directOnce = useLockedMutate<void>(direct);
  const createOnce = useLockedMutate<void>(create);
  const toggle = (id: string) => setForm(current => ({ ...current, member_ids: current.member_ids.includes(id) ? current.member_ids.filter(value => value !== id) : [...current.member_ids, id] }));
  return <section className="card" aria-label="Conversație nouă">
    <h2>Conversație nouă</h2>
    <form onSubmit={e => { e.preventDefault(); directOnce(); }} aria-label="Mesaj direct">
      <label>Mesaj direct către<select required value={person} onChange={e => setPerson(e.target.value)}><option value="">Alege persoana</option>{others.map(m => <option key={m.id} value={m.id}>{m.full_name}</option>)}</select></label>
      <Failure error={direct.error} />
      <button className="secondary" disabled={direct.isPending}>Deschide</button>
    </form>
    <form onSubmit={e => { e.preventDefault(); createOnce(); }} aria-label="Grup sau canal">
      <h3>{manager ? 'Grup sau canal' : 'Grup'}</h3>
      {manager && <label>Tip<select value={form.kind} onChange={e => setForm({ ...form, kind: e.target.value as 'group' | 'channel' })}><option value="group">Grup</option><option value="channel">Canal</option></select></label>}
      <label>Nume<input required maxLength={120} value={form.title} onChange={e => setForm({ ...form, title: e.target.value })} /></label>
      <div className="collab-members" role="group" aria-label="Membri">{others.map(m => <label key={m.id}><input type="checkbox" checked={form.member_ids.includes(m.id)} onChange={() => toggle(m.id)} />{m.full_name}</label>)}</div>
      <Failure error={create.error} />
      <button className="secondary" disabled={create.isPending}>Creează</button>
    </form>
  </section>;
}

function RoomMessage({ onOpen }: { onOpen: (id: string) => void }) {
  const client = useQueryClient();
  const rooms = useQuery({ queryKey: ['rooms'], queryFn: listRooms });
  const [room, setRoom] = useState('');
  const [notice, setNotice] = useState('');
  return <section className="card" aria-label="Mesaj către cabinet">
    <h2>Mesaj către cabinet</h2>
    <p className="muted">Ajunge la echipa repartizată acum în cabinet.</p>
    <label>Cabinet<select value={room} onChange={e => { setRoom(e.target.value); setNotice(''); }}><option value="">Alege cabinetul</option>{rooms.data?.filter(item => item.is_active).map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
    <Failure error={rooms.error} />
    {notice && <p className="notice-text" role="status">{notice}</p>}
    {room && <Composer target={{ kind: 'room', id: room }} onSent={message => { setNotice(`Trimis către: ${message.recipients.join(', ')}.`); void client.invalidateQueries({ queryKey: ['collab'] }); onOpen(message.conversation_id); }} />}
  </section>;
}

function Messages({ user, members, timezone }: { user: User; members: team.TeamMember[]; timezone: string }) {
  const query = useQuery({ queryKey: ['collab', 'conversations'], queryFn: collab.listConversations, ...POLL_OPTIONS });
  const [selected, setSelected] = useState('');
  const current = query.data?.find(item => item.id === selected);
  return <div className="collab-layout">
    <div className="collab-column">
      <section className="card collab-first" aria-label="Conversații">
        <h2>Conversații</h2>
        <Failure error={query.error} />
        {query.data?.length === 0 && <p className="muted">Nicio conversație încă.</p>}
        {query.data?.map(item => <button key={item.id} className="collab-conversation" aria-current={item.id === selected ? 'true' : undefined} onClick={() => setSelected(item.id)}>
          <strong>{item.title}</strong>{item.unread > 0 && <span className="collab-badge" aria-label={`${item.unread} necitite`}>{item.unread}</span>}
          <small>{KIND_LABELS[item.kind]}{item.last_message ? ` · ${item.last_message.sender}: ${item.last_message.preview}` : ''}</small>
        </button>)}
      </section>
      <div className="collab-third"><NewConversation user={user} members={members} onOpen={setSelected} /></div>
    </div>
    <div className="collab-column">
      <div className="collab-second">{current ? <Thread key={current.id} conversation={current} user={user} members={members} timezone={timezone} /> : <section className="card"><p className="muted">Alege o conversație.</p></section>}</div>
      <div className="collab-fourth"><RoomMessage onOpen={setSelected} /></div>
    </div>
  </div>;
}

export function TaskRow({ task, user, members, timezone }: { task: collab.Task; user: User; members: team.TeamMember[]; timezone: string }) {
  const client = useQueryClient();
  const manager = MANAGERS.includes(user.role);
  const closed = CLOSED.includes(task.status);
  const canChange = manager || task.assignee_id === user.id || task.created_by_id === user.id; // a handover recipient reads until taking it
  const [form, setForm] = useState({ status: '' as '' | collab.TaskStatus, assignee_id: '', reason: '' });
  const reopening = closed && form.status === 'open';
  const reasonNeeded = form.status === 'blocked' || form.status === 'cancelled' || reopening;
  const change = useMutation({
    mutationFn: () => collab.changeTask(task.id, { expected_version: task.version, ...(form.status ? { status: form.status } : {}), ...(form.assignee_id ? { assignee_id: form.assignee_id } : {}), ...(form.reason.trim() ? { reason: form.reason.trim() } : {}) }),
    onSuccess: () => setForm({ status: '', assignee_id: '', reason: '' }),
    onSettled: () => { void client.invalidateQueries({ queryKey: ['collab', 'tasks'] }); },
  });
  const changeOnce = useLockedMutate<void>(change);
  const options = (Object.keys(STATUS_LABELS) as collab.TaskStatus[]).filter(value => value !== task.status);
  const ready = (form.status || form.assignee_id) && (!reasonNeeded || form.reason.trim());
  return <article className="collab-task" aria-label={`Sarcină: ${task.title}`}>
    <strong>{task.title}</strong>
    <p className="muted">{STATUS_LABELS[task.status]} · Responsabil: {task.assignee} · De la: {task.created_by}{task.due_at ? ' · Termen: ' : ''}{task.due_at && <span className={task.overdue ? 'collab-overdue' : undefined}>{formatDateTime(task.due_at, timezone)}{task.overdue ? ' (întârziată)' : ''}</span>}</p>
    {task.description && <p>{task.description}</p>}
    <details><summary className="muted">Istoric ({task.events.length})</summary>
      {task.events.map(event => <p key={event.version} className="muted">{formatDateTime(event.created_at, timezone)} · {event.actor} · {event.from_status === null ? `creată pentru ${event.to_assignee}` : [event.from_status !== event.to_status ? `${STATUS_LABELS[event.from_status]} → ${STATUS_LABELS[event.to_status]}` : '', event.from_assignee !== event.to_assignee ? `${event.from_assignee} → ${event.to_assignee}` : ''].filter(Boolean).join(' · ')}{event.reason ? ` · Motiv: ${event.reason}` : ''}</p>)}
    </details>
    {canChange && (!closed || manager) && <form className="collab-task-actions" onSubmit={e => { e.preventDefault(); changeOnce(); }}>
      {closed ? <label>Status<select value={form.status} onChange={e => setForm({ ...form, status: e.target.value as '' | collab.TaskStatus })}><option value="">Fără schimbare</option><option value="open">Redeschide</option></select></label>
        : <label>Status<select value={form.status} onChange={e => setForm({ ...form, status: e.target.value as '' | collab.TaskStatus })}><option value="">Fără schimbare</option>{options.map(value => <option key={value} value={value}>{STATUS_LABELS[value]}</option>)}</select></label>}
      {!closed && <label>Responsabil nou<select value={form.assignee_id} onChange={e => setForm({ ...form, assignee_id: e.target.value })}><option value="">Fără schimbare</option>{members.filter(m => m.is_active && m.id !== task.assignee_id).map(m => <option key={m.id} value={m.id}>{m.full_name}</option>)}</select></label>}
      {(reasonNeeded || form.assignee_id) && <label>{reasonNeeded ? 'Motiv (obligatoriu)' : 'Motiv (opțional)'}<input required={reasonNeeded} maxLength={500} value={form.reason} onChange={e => setForm({ ...form, reason: e.target.value })} /></label>}
      <button className="secondary" disabled={!ready || change.isPending}>Aplică</button>
    </form>}
    <Failure error={change.error} />
  </article>;
}

function Tasks({ user, members, timezone }: { user: User; members: team.TeamMember[]; timezone: string }) {
  const client = useQueryClient();
  const [filters, setFilters] = useState<collab.TaskFilters>({ mine: true });
  const query = useQuery({ queryKey: ['collab', 'tasks', filters], queryFn: () => collab.listTasks(filters), ...POLL_OPTIONS });
  const blank = { title: '', description: '', assignee_id: '', local_due: '' };
  const [form, setForm] = useState(blank);
  const [fold, setFold] = useState<'' | '0' | '1'>('');
  const create = useMutation({
    mutationFn: () => collab.createTask({ title: form.title, assignee_id: form.assignee_id, ...(form.description.trim() ? { description: form.description } : {}), ...(form.local_due ? { local_due: form.local_due } : {}), ...(fold ? { due_fold: Number(fold) as 0 | 1 } : {}) }),
    onSuccess: () => { setForm(blank); setFold(''); void client.invalidateQueries({ queryKey: ['collab', 'tasks'] }); },
  });
  const createOnce = useLockedMutate<void>(create);
  const ambiguous = create.error ? apiErrorMessage(create.error).includes('ambiguous') : false;
  return <>
    <section className="card" aria-label="Sarcină nouă">
      <h2>Sarcină nouă</h2>
      <form onSubmit={e => { e.preventDefault(); createOnce(); }}>
        <label>Titlu<input required maxLength={200} value={form.title} onChange={e => setForm({ ...form, title: e.target.value })} /></label>
        <label>Descriere (opțional)<textarea maxLength={MAX_MESSAGE} value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} /></label>
        <p className="muted">{MEDICAL_NOTICE}</p>
        <label>Responsabil<select required value={form.assignee_id} onChange={e => setForm({ ...form, assignee_id: e.target.value })}><option value="">Alege persoana</option>{members.filter(m => m.is_active).map(m => <option key={m.id} value={m.id}>{m.full_name}</option>)}</select></label>
        <label>Termen, în fusul clinicii ({timezone})<input type="datetime-local" value={form.local_due} onChange={e => setForm({ ...form, local_due: e.target.value })} /></label>
        {(ambiguous || fold !== '') && <label>Apariția orei<select value={fold} onChange={e => setFold(e.target.value as '' | '0' | '1')}><option value="">Alege</option><option value="0">Prima</option><option value="1">A doua</option></select></label>}
        <Failure error={create.error} />
        <button disabled={create.isPending}>Adaugă sarcina</button>
      </form>
    </section>
    <section className="card" aria-label="Sarcini">
      <h2>Sarcini</h2>
      <div className="collab-members">
        <label><input type="checkbox" checked={Boolean(filters.mine)} onChange={e => setFilters({ ...filters, mine: e.target.checked })} />Ale mele</label>
        <label><input type="checkbox" checked={Boolean(filters.overdue)} onChange={e => setFilters({ ...filters, overdue: e.target.checked })} />Întârziate</label>
        <label>Status<select value={filters.status ?? ''} onChange={e => setFilters({ ...filters, status: (e.target.value || undefined) as collab.TaskStatus | undefined })}><option value="">Toate</option>{(Object.keys(STATUS_LABELS) as collab.TaskStatus[]).map(value => <option key={value} value={value}>{STATUS_LABELS[value]}</option>)}</select></label>
      </div>
      <Failure error={query.error} />
      {query.data?.length === 0 && <p className="muted">Nicio sarcină pentru acest filtru.</p>}
      {query.data?.map(task => <TaskRow key={task.id} task={task} user={user} members={members} timezone={timezone} />)}
    </section>
  </>;
}

function HandoverTask({ handoverId, task, timezone }: { handoverId: string; task: collab.Handover['tasks'][number]; timezone: string }) {
  const client = useQueryClient();
  const take = useMutation({ mutationFn: () => collab.takeHandoverTask(handoverId, task.task_id, task.version as number), onSettled: () => { void client.invalidateQueries({ queryKey: ['collab'] }); } });
  const takeOnce = useLockedMutate<void>(take);
  return <div>
    <p>{task.title} · {STATUS_LABELS[task.status]}{task.due_at ? ` · Termen: ${formatDateTime(task.due_at, timezone)}` : ''}{task.with_me ? ' · Preluată de tine' : ''}</p>
    {task.can_take && <button className="secondary" disabled={take.isPending} onClick={() => takeOnce()}>Preia sarcina</button>}
    <Failure error={take.error} />
  </div>;
}

export function HandoverCard({ item, user, timezone }: { item: collab.Handover; user: User; timezone: string }) {
  const client = useQueryClient();
  const confirm = useMutation({ mutationFn: () => collab.acknowledgeHandover(item.id), onSettled: () => { void client.invalidateQueries({ queryKey: ['collab'] }); } });
  const confirmOnce = useLockedMutate<void>(confirm);
  return <article className="collab-task" aria-label={`Predare de la ${item.from_user}`}>
    <strong>{item.from_user} → {item.to_user}</strong>
    <p className="muted">Tura {formatDateTime(item.shift_starts_at, timezone)} – {formatDateTime(item.shift_ends_at, timezone)} · scrisă de {item.created_by}, {formatDateTime(item.created_at, timezone)}</p>
    <p>{item.notes}</p>
    <p className="muted">{item.tasks.length ? 'Sarcini nerezolvate la predare:' : 'Nicio sarcină nerezolvată la predare.'}</p>
    {item.tasks.map(task => <HandoverTask key={task.task_id} handoverId={item.id} task={task} timezone={timezone} />)}
    {item.acknowledged_at ? <p className="notice-text">Primire confirmată: {formatDateTime(item.acknowledged_at, timezone)}</p>
      : item.to_user_id === user.id ? <button className="secondary" disabled={confirm.isPending} onClick={() => confirmOnce()}>Confirm primirea</button>
        : <p className="muted">Așteaptă confirmarea destinatarului.</p>}
    <Failure error={confirm.error} />
  </article>;
}

function Handovers({ user, members, timezone }: { user: User; members: team.TeamMember[]; timezone: string }) {
  const client = useQueryClient();
  const manager = MANAGERS.includes(user.role);
  const query = useQuery({ queryKey: ['collab', 'handovers'], queryFn: collab.listHandovers, ...POLL_OPTIONS });
  const day = clinicDate(timezone);
  const schedule = useQuery({ queryKey: ['team', 'schedule', 'day', day], queryFn: () => team.getSchedule('day', day) });
  const [form, setForm] = useState({ shift_id: '', to_user_id: '', notes: '' });
  const create = useMutation({ mutationFn: () => collab.createHandover(form), onSuccess: () => { setForm({ shift_id: '', to_user_id: '', notes: '' }); void client.invalidateQueries({ queryKey: ['collab'] }); } });
  const createOnce = useLockedMutate<void>(create);
  const done = new Set(query.data?.map(item => item.shift_id));
  const now = Date.now();
  const shifts = schedule.data?.shifts.filter(item => (manager || item.user_id === user.id) && Date.parse(item.starts_at) <= now && !done.has(item.id)) ?? [];
  const from = schedule.data?.shifts.find(item => item.id === form.shift_id)?.user_id;
  return <>
    <section className="card" aria-label="Predare nouă">
      <h2>Predare de tură</h2>
      <p className="muted">Lista sarcinilor nerezolvate se salvează în momentul predării. Predarea nu se mai modifică.</p>
      <form onSubmit={e => { e.preventDefault(); createOnce(); }}>
        <label>Tura<select required value={form.shift_id} onChange={e => setForm({ ...form, shift_id: e.target.value, to_user_id: '' })}><option value="">Alege tura de azi</option>{shifts.map(item => <option key={item.id} value={item.id}>{formatDateTime(item.starts_at, timezone)} – {formatDateTime(item.ends_at, timezone)} · {item.user_name} · {item.room_name ?? 'Recepție'}</option>)}</select></label>
        <label>Predau către<select required value={form.to_user_id} onChange={e => setForm({ ...form, to_user_id: e.target.value })}><option value="">Alege persoana</option>{members.filter(m => m.is_active && m.id !== from).map(m => <option key={m.id} value={m.id}>{m.full_name}</option>)}</select></label>
        <label>Note<textarea required maxLength={MAX_MESSAGE} value={form.notes} onChange={e => setForm({ ...form, notes: e.target.value })} /></label>
        <p className="muted">{MEDICAL_NOTICE}</p>
        <Failure error={schedule.error ?? create.error} />
        <button disabled={create.isPending}>Salvează predarea</button>
      </form>
    </section>
    <section className="card" aria-label="Predări">
      <h2>Predări</h2>
      <Failure error={query.error} />
      {query.data?.length === 0 && <p className="muted">Nicio predare.</p>}
      {query.data?.map(item => <HandoverCard key={item.id} item={item} user={user} timezone={timezone} />)}
    </section>
  </>;
}

export function CollabPage({ user }: { user: User }) {
  const [tab, setTab] = useState<'messages' | 'tasks' | 'handovers'>('messages');
  const clinic = useQuery({ queryKey: ['clinic'], queryFn: getClinic });
  const members = useQuery({ queryKey: ['team', 'members'], queryFn: team.listMembers });
  const summary = useQuery({ queryKey: ['collab', 'summary'], queryFn: collab.getSummary, ...POLL_OPTIONS });
  if (clinic.isLoading || members.isLoading) return <p className="muted">Se încarcă…</p>;
  if (clinic.isError || members.isError || !clinic.data || !members.data) return <Failure error={clinic.error ?? members.error ?? new Error('Pagina nu a putut fi încărcată.')} />;
  const timezone = clinic.data.timezone;
  const pending = summary.data?.pending_handovers ?? 0;
  return <>
    <header><div><p className="eyebrow">COLABORARE</p><h1>Mesaje și sarcini</h1></div></header>
    <div className="collab-tabs" role="group" aria-label="Secțiuni">
      <button className="secondary" aria-pressed={tab === 'messages'} onClick={() => setTab('messages')}>Mesaje{summary.data?.unread ? ` (${summary.data.unread})` : ''}</button>
      <button className="secondary" aria-pressed={tab === 'tasks'} onClick={() => setTab('tasks')}>Sarcini</button>
      <button className="secondary" aria-pressed={tab === 'handovers'} onClick={() => setTab('handovers')}>Predări{pending ? ` (${pending})` : ''}</button>
    </div>
    {tab === 'messages' && <Messages user={user} members={members.data} timezone={timezone} />}
    {tab === 'tasks' && <Tasks user={user} members={members.data} timezone={timezone} />}
    {tab === 'handovers' && <Handovers user={user} members={members.data} timezone={timezone} />}
  </>;
}
