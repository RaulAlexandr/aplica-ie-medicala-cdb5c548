import { FormEvent, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiErrorMessage, getClinic, listRooms, User } from '../api/client';
import { clinicLocalToUtc } from '../timezone';
import * as team from './api';

const MANAGERS = ['clinic_manager', 'administrator'];
const ROLE_LABELS: Record<string, string> = { clinic_manager: 'Manager', administrator: 'Administrator', doctor: 'Medic', assistant: 'Asistent', reception: 'Recepție' };
const POST_LABELS: Record<team.Shift['post'], string> = { doctor: 'Medic', assistant: 'Asistent', front_desk: 'Recepție' };
const LEAVE_LABELS: Record<team.LeaveType, string> = { annual: 'Odihnă', medical: 'Medical', unpaid: 'Fără plată', other: 'Altul' };
const LEAVE_STATUS: Record<team.Leave['status'], string> = { pending: 'În așteptare', approved: 'Aprobat', rejected: 'Respins', cancelled: 'Anulat' };
const DOCUMENT_LABELS: Record<team.DocumentCategory, string> = { contract: 'Contract', certificate: 'Certificat', medical: 'Medical', other: 'Altul' };

export function clinicDate(timezone: string, instant: number = Date.now()): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(instant));
}
export function addDays(day: string, count: number): string {
  const value = new Date(`${day}T12:00:00Z`);
  value.setUTCDate(value.getUTCDate() + count);
  return value.toISOString().slice(0, 10);
}
export function mondayOf(day: string): string {
  return addDays(day, -((new Date(`${day}T12:00:00Z`).getUTCDay() + 6) % 7));
}
export const formatTime = (iso: string, timezone: string) => new Intl.DateTimeFormat('ro-RO', { timeZone: timezone, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(new Date(iso));
export const formatDateTime = (iso: string, timezone: string) => new Intl.DateTimeFormat('ro-RO', { timeZone: timezone, day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(new Date(iso));
export const formatDay = (day: string) => new Intl.DateTimeFormat('ro-RO', { timeZone: 'UTC', weekday: 'long', day: '2-digit', month: '2-digit' }).format(new Date(`${day}T12:00:00Z`));
export function workedLabel(seconds: number | null): string {
  if (seconds === null) return 'în curs';
  const minutes = Math.floor(seconds / 60);
  return `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, '0')} min`;
}
export const attendanceHours = (row: team.Attendance) => row.needs_review || row.open_over_limit ? 'De verificat' : workedLabel(row.worked_seconds);
export function shiftsForDay(shifts: team.Shift[], day: team.ScheduleDay): team.Shift[] {
  const start = Date.parse(day.start); const end = Date.parse(day.end);
  return shifts.filter(item => Date.parse(item.starts_at) < end && Date.parse(item.ends_at) > start);
}
function friendlyError(error: unknown): string {
  const message = apiErrorMessage(error);
  if (message.includes('ambiguous')) return 'Ora aleasă apare de două ori la trecerea la ora de iarnă. Alege mai jos prima sau a doua apariție.';
  if (message.includes('does not exist')) return 'Ora aleasă nu există în ziua trecerii la ora de vară. Alege altă oră.';
  return message;
}
const Failure = ({ error }: { error: unknown }) => error ? <p className="error" role="alert">{friendlyError(error)}</p> : null;

function MyAttendance({ timezone }: { timezone: string }) {
  const client = useQueryClient();
  const query = useQuery({ queryKey: ['team', 'attendance', 'me'], queryFn: team.myAttendance });
  const refresh = () => { void client.invalidateQueries({ queryKey: ['team', 'attendance'] }); };
  const enter = useMutation({ mutationFn: team.clockIn, onSettled: refresh });
  const leave = useMutation({ mutationFn: team.clockOut, onSettled: refresh });
  const open = query.data?.open ?? null;
  const busy = enter.isPending || leave.isPending || query.isLoading;
  return <section className="card" aria-label="Prezența mea">
    <h2>Prezența mea</h2>
    <p className="muted">Prezența arată când ești la clinică. Este separată de timpul manoperelor.</p>
    <p><strong>{open ? `Prezent de la ${formatTime(open.effective_clock_in_at, timezone)}` : 'Nu ești pontat acum'}</strong></p>
    {open?.open_over_limit && <p className="error" role="alert">Pontajul este deschis de peste 24 de ore, din {formatDateTime(open.effective_clock_in_at, timezone)}. Închide-l, apoi cere managerului corectarea orei de ieșire.</p>}
    {open ? <button disabled={busy} onClick={() => leave.mutate()}>Înregistrează ieșirea</button> : <button disabled={busy} onClick={() => enter.mutate()}>Înregistrează intrarea</button>}
    <Failure error={query.error ?? enter.error ?? leave.error} />
    <div className="list">{query.data?.sessions.slice(0, 5).map(row => <article key={row.id}><span>{formatDateTime(row.effective_clock_in_at, timezone)} – {row.effective_clock_out_at ? formatTime(row.effective_clock_out_at, timezone) : 'în curs'}{row.corrections.length > 0 ? ' · corectat de manager' : ''}</span><span>{attendanceHours(row)}</span></article>)}</div>
  </section>;
}

function ScheduleView({ timezone, userId }: { timezone: string; userId: string }) {
  const today = clinicDate(timezone);
  const [mode, setMode] = useState<'day' | 'week'>('week');
  const [date, setDate] = useState(today);
  const anchor = mode === 'week' ? mondayOf(date) : date;
  const query = useQuery({ queryKey: ['team', 'schedule', mode, anchor], queryFn: () => team.getSchedule(mode, anchor) });
  const step = mode === 'week' ? 7 : 1;
  return <section className="card" aria-label="Cine lucrează unde și când">
    <h2>Cine lucrează unde și când</h2>
    <div className="calendar-actions">
      <button className="secondary" onClick={() => setMode('day')} aria-pressed={mode === 'day'}>Zi</button>
      <button className="secondary" onClick={() => setMode('week')} aria-pressed={mode === 'week'}>Săptămână</button>
      <button className="secondary" onClick={() => setDate(addDays(anchor, -step))}>Înapoi</button>
      <input aria-label="Data" type="date" value={date} onChange={e => e.target.value && setDate(e.target.value)} />
      <button className="secondary" onClick={() => setDate(addDays(anchor, step))}>Înainte</button>
      <button className="secondary" onClick={() => setDate(today)}>Azi</button>
    </div>
    <p className="muted">Ore în fusul clinicii: {timezone}</p>
    <Failure error={query.error} />
    {query.data?.days.map(day => {
      const shifts = shiftsForDay(query.data.shifts, day);
      const leaves = query.data.leaves.filter(item => item.starts_on <= day.date && item.ends_on >= day.date);
      return <div key={day.date} data-day={day.date}>
        <h3>{formatDay(day.date)}{day.hours !== 24 ? ` · zi de ${day.hours} ore (schimbare de oră)` : ''}</h3>
        {shifts.length === 0 && leaves.length === 0 && <p className="muted">Nicio tură.</p>}
        {shifts.map(item => <p key={item.id}><strong>{formatTime(item.starts_at, timezone)}–{formatTime(item.ends_at, timezone)}</strong> · {item.user_name}{item.user_is_active ? '' : ' · inactiv'}{item.user_id === userId ? ' (tu)' : ''} · {POST_LABELS[item.post]} · {item.room_name ?? 'Fără cabinet'}</p>)}
        {leaves.map(item => <p key={item.id} className="muted">{item.user_name}{item.user_is_active ? '' : ' · inactiv'}{item.user_id === userId ? ' (tu)' : ''} · în concediu</p>)}
      </div>;
    })}
  </section>;
}

function ShiftManager({ members, timezone }: { members: team.TeamMember[]; timezone: string }) {
  const client = useQueryClient();
  const rooms = useQuery({ queryKey: ['rooms'], queryFn: listRooms });
  const [form, setForm] = useState({ user_id: '', room_id: '', local_start: '', local_end: '' });
  const [folds, setFolds] = useState<{ start: '' | '0' | '1'; end: '' | '0' | '1' }>({ start: '', end: '' });
  const [cancelId, setCancelId] = useState('');
  const [reason, setReason] = useState('');
  const person = members.find(item => item.id === form.user_id);
  const noRoom = person?.role === 'reception';
  const refresh = () => { void client.invalidateQueries({ queryKey: ['team', 'schedule'] }); };
  const create = useMutation({
    mutationFn: () => team.createShift({ user_id: form.user_id, room_id: noRoom || !form.room_id ? null : form.room_id, local_start: form.local_start, local_end: form.local_end, ...(folds.start ? { start_fold: Number(folds.start) as 0 | 1 } : {}), ...(folds.end ? { end_fold: Number(folds.end) as 0 | 1 } : {}) }),
    onSuccess: () => { setForm({ user_id: '', room_id: '', local_start: '', local_end: '' }); setFolds({ start: '', end: '' }); refresh(); },
  });
  const cancel = useMutation({ mutationFn: () => team.cancelShift(cancelId, reason), onSuccess: () => { setCancelId(''); setReason(''); refresh(); } });
  const today = clinicDate(timezone);
  const upcoming = useQuery({ queryKey: ['team', 'schedule', 'week', mondayOf(today)], queryFn: () => team.getSchedule('week', mondayOf(today)) });
  const ambiguous = create.error ? apiErrorMessage(create.error).includes('ambiguous') : false;
  const showFolds = ambiguous || folds.start !== '' || folds.end !== '';
  function submit(event: FormEvent) { event.preventDefault(); create.mutate(); }
  return <section className="card" aria-label="Repartizare ture">
    <h2>Repartizare ture</h2>
    <form onSubmit={submit}>
      <label>Persoană<select required value={form.user_id} onChange={e => setForm({ ...form, user_id: e.target.value })}><option value="">Alege persoana</option>{members.filter(m => m.is_active).map(m => <option key={m.id} value={m.id}>{m.full_name} ({ROLE_LABELS[m.role] ?? m.role})</option>)}</select></label>
      {!noRoom && <label>Cabinet<select value={form.room_id} onChange={e => setForm({ ...form, room_id: e.target.value })}><option value="">Fără cabinet</option>{rooms.data?.filter(r => r.is_active).map(r => <option key={r.id} value={r.id}>{r.name}</option>)}</select></label>}
      <label>Început<input required type="datetime-local" value={form.local_start} onChange={e => setForm({ ...form, local_start: e.target.value })} /></label>
      <label>Sfârșit<input required type="datetime-local" value={form.local_end} onChange={e => setForm({ ...form, local_end: e.target.value })} /></label>
      {showFolds && <>
        <label>Apariția orei de început<select value={folds.start} onChange={e => setFolds({ ...folds, start: e.target.value as '' | '0' | '1' })}><option value="">Nu e cazul</option><option value="0">Prima</option><option value="1">A doua</option></select></label>
        <label>Apariția orei de sfârșit<select value={folds.end} onChange={e => setFolds({ ...folds, end: e.target.value as '' | '0' | '1' })}><option value="">Nu e cazul</option><option value="0">Prima</option><option value="1">A doua</option></select></label>
      </>}
      <Failure error={create.error} />
      <button disabled={create.isPending}>Adaugă tura</button>
    </form>
    <h3>Anulează o tură din această săptămână</h3>
    <form onSubmit={e => { e.preventDefault(); cancel.mutate(); }}>
      <label>Tură<select required value={cancelId} onChange={e => setCancelId(e.target.value)}><option value="">Alege tura</option>{upcoming.data?.shifts.map(s => <option key={s.id} value={s.id}>{formatDateTime(s.starts_at, timezone)} · {s.user_name}</option>)}</select></label>
      <label>Motivul anulării<input required value={reason} onChange={e => setReason(e.target.value)} /></label>
      <Failure error={cancel.error} />
      <button className="secondary" disabled={cancel.isPending}>Anulează tura</button>
    </form>
  </section>;
}

function LeavePanel({ user, members }: { user: User; members: team.TeamMember[] }) {
  const client = useQueryClient();
  const manager = MANAGERS.includes(user.role);
  const query = useQuery({ queryKey: ['team', 'leave'], queryFn: team.listLeave });
  const [form, setForm] = useState({ user_id: '', leave_type: 'annual' as team.LeaveType, starts_on: '', ends_on: '', reason: '' });
  const [decisionReason, setDecisionReason] = useState('');
  const [notice, setNotice] = useState('');
  const refresh = () => { void client.invalidateQueries({ queryKey: ['team'] }); };
  const create = useMutation({
    mutationFn: () => team.createLeave({ ...(form.user_id ? { user_id: form.user_id } : {}), leave_type: form.leave_type, starts_on: form.starts_on, ends_on: form.ends_on, ...(form.reason.trim() ? { reason: form.reason.trim() } : {}) }),
    onSuccess: () => { setForm({ user_id: '', leave_type: 'annual', starts_on: '', ends_on: '', reason: '' }); setNotice('Cererea a fost trimisă.'); refresh(); },
  });
  const decide = useMutation({
    mutationFn: async ({ id, action }: { id: string; action: 'approve' | 'reject' | 'cancel' }) => {
      if (action === 'approve') return team.approveLeave(id);
      if (!decisionReason.trim()) throw new Error('Scrie motivul înainte de a respinge sau anula.');
      return action === 'reject' ? team.rejectLeave(id, decisionReason.trim()) : team.cancelLeave(id, decisionReason.trim());
    },
    onSuccess: data => {
      setDecisionReason('');
      const appointments = data.affected_appointments?.length ?? 0; const shifts = data.affected_shifts?.length ?? 0;
      setNotice(data.status === 'approved' ? `Concediu aprobat. De verificat: ${appointments} programări și ${shifts} ture din interval. Nu au fost modificate automat.` : `Cerere: ${LEAVE_STATUS[data.status]}.`);
      refresh();
    },
  });
  const withdraw = useMutation({ mutationFn: (id: string) => team.cancelLeave(id, 'Renunț la cerere'), onSuccess: () => { setNotice('Cerere anulată.'); refresh(); } });
  return <section className="card" aria-label="Concedii">
    <h2>Concedii</h2>
    <form onSubmit={e => { e.preventDefault(); create.mutate(); }}>
      {manager && <label>Pentru<select value={form.user_id} onChange={e => setForm({ ...form, user_id: e.target.value })}><option value="">Mine</option>{members.filter(m => m.is_active && m.id !== user.id).map(m => <option key={m.id} value={m.id}>{m.full_name}</option>)}</select></label>}
      <label>Tip concediu<select value={form.leave_type} onChange={e => setForm({ ...form, leave_type: e.target.value as team.LeaveType })}>{Object.entries(LEAVE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label>Prima zi<input required type="date" value={form.starts_on} onChange={e => setForm({ ...form, starts_on: e.target.value })} /></label>
      <label>Ultima zi<input required type="date" value={form.ends_on} onChange={e => setForm({ ...form, ends_on: e.target.value })} /></label>
      <label>Motiv (opțional)<input maxLength={500} value={form.reason} onChange={e => setForm({ ...form, reason: e.target.value })} /></label>
      <Failure error={create.error} />
      <button disabled={create.isPending}>Trimite cererea</button>
    </form>
    {notice && <p className="notice-text" role="status">{notice}</p>}
    <Failure error={query.error ?? decide.error ?? withdraw.error} />
    {manager && <label>Motiv pentru respingere sau anulare<input value={decisionReason} onChange={e => setDecisionReason(e.target.value)} /></label>}
    <div className="list">{query.data?.map(item => <article key={item.id}>
      <span><strong>{item.user_name}</strong> · {item.starts_on} – {item.ends_on} · {item.leave_type ? LEAVE_LABELS[item.leave_type] : ''} · {LEAVE_STATUS[item.status]}{item.status === 'approved' && !item.agenda_blocked && item.user_id ? ' · fără blocare în agendă' : ''}{item.decision_reason ? ` · ${item.decision_reason}` : ''}</span>
      <span>
        {manager && item.status === 'pending' && <><button className="secondary" disabled={decide.isPending} onClick={() => decide.mutate({ id: item.id, action: 'approve' })}>Aprobă</button> <button className="secondary" disabled={decide.isPending} onClick={() => decide.mutate({ id: item.id, action: 'reject' })}>Respinge</button> </>}
        {manager && item.status === 'approved' && <button className="secondary" disabled={decide.isPending} onClick={() => decide.mutate({ id: item.id, action: 'cancel' })}>Anulează</button>}
        {!manager && item.status === 'pending' && item.user_id === user.id && <button className="secondary" disabled={withdraw.isPending} onClick={() => withdraw.mutate(item.id)}>Renunță</button>}
      </span>
    </article>)}</div>
  </section>;
}

function DocumentsPanel({ user, members }: { user: User; members: team.TeamMember[] }) {
  const client = useQueryClient();
  const manager = MANAGERS.includes(user.role);
  const [selected, setSelected] = useState(user.id);
  const [file, setFile] = useState<File | null>(null);
  const [category, setCategory] = useState<team.DocumentCategory>('contract');
  const [expiresOn, setExpiresOn] = useState('');
  const [archiveReason, setArchiveReason] = useState('');
  const [uploadKey, setUploadKey] = useState(() => crypto.randomUUID());
  const query = useQuery({ queryKey: ['team', 'documents', selected], queryFn: () => team.listDocuments(selected) });
  const refresh = () => { void client.invalidateQueries({ queryKey: ['team', 'documents', selected] }); };
  const upload = useMutation({
    mutationFn: () => { if (!file) throw new Error('Alege un fișier.'); return team.uploadDocument(selected, file, category, expiresOn, uploadKey); },
    // uploadKey also remounts the file input, so it cannot keep showing a file already uploaded.
    onSuccess: () => { setFile(null); setExpiresOn(''); setUploadKey(crypto.randomUUID()); refresh(); },
  });
  const archive = useMutation({ mutationFn: (id: string) => { if (!archiveReason.trim()) throw new Error('Scrie motivul arhivării.'); return team.archiveDocument(id, archiveReason.trim()); }, onSuccess: () => { setArchiveReason(''); refresh(); } });
  const download = useMutation({
    mutationFn: async (item: team.TeamDocument) => {
      const url = URL.createObjectURL(await team.fetchDocument(item.id));
      const anchor = document.createElement('a'); anchor.href = url; anchor.download = item.original_filename; anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    },
  });
  return <section className="card" aria-label="Documente administrative">
    <h2>Documente administrative</h2>
    <p className="muted">Sunt vizibile numai managerului și persoanei în cauză.</p>
    {manager && <label>Persoană<select value={selected} onChange={e => setSelected(e.target.value)}>{members.map(m => <option key={m.id} value={m.id}>{m.full_name}</option>)}</select></label>}
    <Failure error={query.error ?? archive.error ?? download.error} />
    {query.data?.length === 0 && <p className="muted">Niciun document.</p>}
    <div className="list">{query.data?.map(item => <article key={item.id}>
      <span><strong>{item.original_filename}</strong> · {DOCUMENT_LABELS[item.category]}{item.expires_on ? ` · expiră ${item.expires_on}` : ''}</span>
      <span><button className="secondary" disabled={download.isPending} onClick={() => download.mutate(item)}>Descarcă</button>{manager && <> <button className="secondary" disabled={archive.isPending} onClick={() => archive.mutate(item.id)}>Arhivează</button></>}</span>
    </article>)}</div>
    {manager && <>
      <label>Motiv arhivare<input value={archiveReason} onChange={e => setArchiveReason(e.target.value)} /></label>
      <form onSubmit={e => { e.preventDefault(); upload.mutate(); }}>
        <label>Fișier (PDF, JPEG, PNG)<input key={uploadKey} required type="file" accept=".pdf,.jpg,.jpeg,.png" onChange={e => setFile(e.target.files?.[0] ?? null)} /></label>
        <label>Categorie<select value={category} onChange={e => setCategory(e.target.value as team.DocumentCategory)}>{Object.entries(DOCUMENT_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label>Expiră la (opțional)<input type="date" value={expiresOn} onChange={e => setExpiresOn(e.target.value)} /></label>
        <Failure error={upload.error} />
        <button disabled={upload.isPending}>Încarcă documentul</button>
      </form>
    </>}
  </section>;
}

function AttendanceManager({ members, timezone }: { members: team.TeamMember[]; timezone: string }) {
  const client = useQueryClient();
  const [day, setDay] = useState(clinicDate(timezone));
  // The server computes the clinic day, so 23- and 25-hour days have the right bounds.
  const bounds = useQuery({ queryKey: ['team', 'schedule', 'day', day], queryFn: () => team.getSchedule('day', day) });
  const range = bounds.data?.days[0];
  const query = useQuery({ queryKey: ['team', 'attendance', 'day', day, range?.start], queryFn: () => team.listAttendance(range!.start, range!.end), enabled: Boolean(range) });
  const alerts = useQuery({ queryKey: ['team', 'attendance', 'alerts'], queryFn: team.attendanceAlerts });
  const [form, setForm] = useState({ id: '', clock_in: '', clock_out: '', reason: '' });
  const [manual, setManual] = useState({ user_id: '', clock_in: '', clock_out: '', reason: '' });
  const [fold, setFold] = useState<'' | '0' | '1'>('');
  const toUtc = (value: string) => clinicLocalToUtc(value, timezone, fold === '' ? undefined : Number(fold) as 0 | 1);
  const refresh = () => { void client.invalidateQueries({ queryKey: ['team', 'attendance'] }); };
  const correct = useMutation({
    mutationFn: async () => team.correctAttendance(form.id, { ...(form.clock_in ? { clock_in_at: toUtc(form.clock_in) } : {}), ...(form.clock_out ? { clock_out_at: toUtc(form.clock_out) } : {}), reason: form.reason }),
    onSuccess: () => { setForm({ id: '', clock_in: '', clock_out: '', reason: '' }); setFold(''); refresh(); },
  });
  const add = useMutation({
    mutationFn: async () => team.addManualAttendance({ user_id: manual.user_id, clock_in_at: toUtc(manual.clock_in), clock_out_at: toUtc(manual.clock_out), reason: manual.reason }),
    onSuccess: () => { setManual({ user_id: '', clock_in: '', clock_out: '', reason: '' }); setFold(''); refresh(); },
  });
  const foldField = <label>Ora repetată la schimbarea orei<select value={fold} onChange={e => setFold(e.target.value as '' | '0' | '1')}><option value="">Nu e cazul</option><option value="0">Prima apariție</option><option value="1">A doua apariție</option></select></label>;
  return <section className="card" aria-label="Prezența echipei">
    <h2>Prezența echipei</h2>
    <Failure error={alerts.error} />
    {Boolean(alerts.data?.inactive.length) && <section aria-label="Persoane inactive">
      <h3>Persoane inactive cu activitate rămasă</h3>
      <p className="muted">Nimic nu se anulează automat. Verifică turele și pontajele de mai jos.</p>
      <div className="list">{alerts.data?.inactive.map(item => <article key={item.user_id}><span><strong>{item.user_name}</strong>{item.future_shifts > 0 ? ` · ${item.future_shifts} ${item.future_shifts === 1 ? 'tură viitoare activă' : 'ture viitoare active'}` : ''}{item.open_attendance ? ' · pontaj deschis' : ''}</span></article>)}</div>
    </section>}
    {Boolean(alerts.data?.review.length) && <section aria-label="Pontaje de verificat">
      <h3>Pontaje de verificat</h3>
      <div className="list">{alerts.data?.review.map(row => <article key={row.id}>
        <span><strong>{row.user_name}</strong>{row.user_is_active ? '' : ' · inactiv'} · {formatDateTime(row.effective_clock_in_at, timezone)} – {row.effective_clock_out_at ? formatDateTime(row.effective_clock_out_at, timezone) : 'deschis de peste 24 de ore'} · {attendanceHours(row)}</span>
        <button className="secondary" onClick={() => setForm({ id: row.id, clock_in: '', clock_out: '', reason: '' })}>Corectează</button>
      </article>)}</div>
    </section>}
    <label>Ziua<input type="date" value={day} onChange={e => e.target.value && setDay(e.target.value)} /></label>
    <Failure error={bounds.error ?? query.error} />
    {query.data?.length === 0 && <p className="muted">Nicio prezență în această zi.</p>}
    <div className="list">{query.data?.map(row => <article key={row.id}>
      <span><strong>{row.user_name}</strong>{row.user_is_active ? '' : ' · inactiv'} · {formatDateTime(row.effective_clock_in_at, timezone)} – {row.effective_clock_out_at ? formatDateTime(row.effective_clock_out_at, timezone) : 'în curs'} · {attendanceHours(row)}{row.corrections.map(c => <small key={c.revision}><br />Corecție {c.revision}: {c.reason} ({c.created_by})</small>)}</span>
      <button className="secondary" onClick={() => setForm({ id: row.id, clock_in: '', clock_out: '', reason: '' })}>Corectează</button>
    </article>)}</div>
    {form.id && <form onSubmit={e => { e.preventDefault(); correct.mutate(); }} aria-label="Corecție prezență">
      <h3>Corecție (se păstrează înregistrarea inițială)</h3>
      <label>Intrare corectă<input type="datetime-local" value={form.clock_in} onChange={e => setForm({ ...form, clock_in: e.target.value })} /></label>
      <label>Ieșire corectă<input type="datetime-local" value={form.clock_out} onChange={e => setForm({ ...form, clock_out: e.target.value })} /></label>
      {foldField}
      <label>Motivul corecției<input required value={form.reason} onChange={e => setForm({ ...form, reason: e.target.value })} /></label>
      <Failure error={correct.error} />
      <button disabled={correct.isPending}>Salvează corecția</button>
    </form>}
    <form onSubmit={e => { e.preventDefault(); add.mutate(); }} aria-label="Prezență uitată">
      <h3>Adaugă o prezență uitată</h3>
      <label>Persoană<select required value={manual.user_id} onChange={e => setManual({ ...manual, user_id: e.target.value })}><option value="">Alege persoana</option>{members.map(m => <option key={m.id} value={m.id}>{m.full_name}</option>)}</select></label>
      <label>Intrare<input required type="datetime-local" value={manual.clock_in} onChange={e => setManual({ ...manual, clock_in: e.target.value })} /></label>
      <label>Ieșire<input required type="datetime-local" value={manual.clock_out} onChange={e => setManual({ ...manual, clock_out: e.target.value })} /></label>
      {!form.id && foldField}
      <label>Motiv<input required value={manual.reason} onChange={e => setManual({ ...manual, reason: e.target.value })} /></label>
      <Failure error={add.error} />
      <button className="secondary" disabled={add.isPending}>Adaugă prezența</button>
    </form>
  </section>;
}

export function TeamPage({ user }: { user: User }) {
  const manager = MANAGERS.includes(user.role);
  const clinic = useQuery({ queryKey: ['clinic'], queryFn: getClinic });
  const members = useQuery({ queryKey: ['team', 'members'], queryFn: team.listMembers });
  if (clinic.isLoading || members.isLoading) return <p className="muted">Se încarcă echipa…</p>;
  if (clinic.isError || members.isError || !clinic.data || !members.data) return <Failure error={clinic.error ?? members.error ?? new Error('Echipa nu a putut fi încărcată.')} />;
  const timezone = clinic.data.timezone;
  return <>
    <header><div><p className="eyebrow">ECHIPĂ</p><h1>Echipă</h1></div></header>
    <MyAttendance timezone={timezone} />
    <ScheduleView timezone={timezone} userId={user.id} />
    {manager && <ShiftManager members={members.data} timezone={timezone} />}
    <LeavePanel user={user} members={members.data} />
    <DocumentsPanel user={user} members={members.data} />
    {manager && <AttendanceManager members={members.data} timezone={timezone} />}
    <section className="card" aria-label="Membrii echipei">
      <h2>Membrii echipei</h2>
      <div className="list">{members.data.map(m => <article key={m.id}><span><strong>{m.full_name}</strong> · {ROLE_LABELS[m.role] ?? m.role}{m.job_title ? ` · ${m.job_title}` : ''}{m.specialization ? ` · ${m.specialization}` : ''}{m.is_active ? '' : ' · inactiv'}</span>{m.phone ? <span>{m.phone}</span> : null}</article>)}</div>
    </section>
  </>;
}
