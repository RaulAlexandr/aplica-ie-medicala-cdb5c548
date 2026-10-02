import { FormEvent, ReactNode, useEffect, useState } from 'react';
import { Link, Navigate, Route, Routes, useLocation, useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { acceptInvitation, activateProcedure, apiErrorMessage, Clinic, createAppointment, createInvitation, createPatient, createProcedure, createRoom, countPatients, deactivateProcedure, deactivateStaff, Doctor, getClinic, getPatient, getProcedure, getStaff, Invitation, listAppointments, listDoctors, listInvitations, listPatients, listProcedures, listRooms, listStaff, login, logout, me, Patient, ProcedureCatalog, register, reissueInvitation, revokeInvitation, Room, Staff, updateClinic, updatePatient, updateProcedure, updateRoom, User } from './api/client';

const managementRoles = ['clinic_manager', 'administrator'];
const staffRoles = ['clinic_manager', 'doctor', 'assistant', 'reception', 'administrator'];
const canEditPatients = (role: string) => managementRoles.includes(role) || role === 'doctor';
function ErrorState({ error }: { error: unknown }) { return <p className="error" role="alert">{apiErrorMessage(error)}</p>; }
function QueryState({ loading, empty, error }: { loading: boolean; empty: boolean; error: unknown }) { if (loading) return <p className="muted">Loading\u2026</p>; if (error) return <ErrorState error={error} />; if (empty) return <p className="muted">Nothing to display yet.</p>; return null; }

function Auth({ onDone }: { onDone: () => void }) {
  const [registerMode, setRegisterMode] = useState(false);
  const [form, setForm] = useState({ clinic_name: '', full_name: '', email: '', password: '' });
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) { e.preventDefault(); setBusy(true); setError(null); try { if (registerMode) await register(form); else await login(form.email, form.password); onDone(); } catch (err) { setError(err); } finally { setBusy(false); } }
  return <main className="auth"><form className="card" onSubmit={submit}><p className="eyebrow">DENTACARE</p><h1>{registerMode ? 'Create clinic' : 'Welcome back'}</h1>{registerMode && <><label>Clinic name<input required value={form.clinic_name} onChange={e => setForm({ ...form, clinic_name: e.target.value })} /></label><label>Your name<input required value={form.full_name} onChange={e => setForm({ ...form, full_name: e.target.value })} /></label></>}<label>Email<input type="email" required value={form.email} onChange={e => setForm({ ...form, email: e.target.value })} /></label><label>Password<input type="password" minLength={12} required value={form.password} onChange={e => setForm({ ...form, password: e.target.value })} /></label>{error !== null && <ErrorState error={error} />}<button disabled={busy}>{busy ? 'Working\u2026' : registerMode ? 'Create clinic' : 'Sign in'}</button><button type="button" className="link" onClick={() => { setRegisterMode(!registerMode); setError(null); }}>{registerMode ? 'Already have an account?' : 'Register a new clinic'}</button></form></main>;
}

function Layout({ user, children, onLogout }: { user: User; children: ReactNode; onLogout: () => Promise<void> }) {
  return <div className="shell">
    <aside>
      <p className="eyebrow">DENTACARE</p>
      <h2>{user.full_name}</h2>
      <p className="role">{user.role.replace('_', ' ')}</p>
      <nav>
        <Link to="/">Overview</Link>
        {staffRoles.includes(user.role) && <Link to="/patients">Patients</Link>}
        {staffRoles.includes(user.role) && <Link to="/appointments">Appointments</Link>}
        {staffRoles.includes(user.role) && <Link to="/procedures">Procedure catalog</Link>}
        {managementRoles.includes(user.role) && <><Link to="/setup">Clinic setup</Link><Link to="/staff">Staff</Link></>}
      </nav>
      <button className="link logout" onClick={() => void onLogout()}>Sign out</button>
    </aside>
    <main className="content">{children}</main>
  </div>;
}

function PatientForm({ initial, onSaved }: { initial?: Patient; onSaved: (patient: Patient) => void }) {
  const [form, setForm] = useState<Partial<Patient>>({ first_name: initial?.first_name ?? '', last_name: initial?.last_name ?? '', phone: initial?.phone ?? '', email: initial?.email ?? '', allergies: initial?.allergies ?? '', medications: initial?.medications ?? '', medical_alerts: initial?.medical_alerts ?? '', notes: initial?.notes ?? '' });
  const mutation = useMutation({ mutationFn: async () => { const payload = { ...form }; if (!payload.email?.trim()) { if (initial) payload.email = null; else delete payload.email; } return initial ? updatePatient(initial.id, payload) : createPatient(payload as Pick<Patient, 'first_name' | 'last_name'>); }, onSuccess: onSaved });
  const set = (key: keyof Patient, value: string) => setForm(current => ({ ...current, [key]: value }));
  return <form className="card" onSubmit={e => { e.preventDefault(); mutation.mutate(); }}>
    <h2>{initial ? 'Edit patient' : 'New patient'}</h2>
    <div className="grid two">
      <label>First name<input required value={form.first_name ?? ''} onChange={e => set('first_name', e.target.value)} /></label>
      <label>Last name<input required value={form.last_name ?? ''} onChange={e => set('last_name', e.target.value)} /></label>
      <label>Phone<input value={form.phone ?? ''} onChange={e => set('phone', e.target.value)} /></label>
      <label>Email<input type="email" value={form.email ?? ''} onChange={e => set('email', e.target.value)} /></label>
    </div>
    <label>Allergies<textarea value={form.allergies ?? ''} onChange={e => set('allergies', e.target.value)} /></label>
    <label>Medications<textarea value={form.medications ?? ''} onChange={e => set('medications', e.target.value)} /></label>
    <label>Medical alerts<textarea value={form.medical_alerts ?? ''} onChange={e => set('medical_alerts', e.target.value)} /></label>
    <label>Notes<textarea value={form.notes ?? ''} onChange={e => set('notes', e.target.value)} /></label>
    {mutation.isError && <ErrorState error={mutation.error} />}
    <button disabled={mutation.isPending}>{mutation.isPending ? 'Saving\u2026' : 'Save patient'}</button>
  </form>;
}

function Patients({ user }: { user: User }) {
  const [search, setSearch] = useState('');
  const [showForm, setShowForm] = useState(false);
  const query = useQuery({ queryKey: ['patients', search], queryFn: () => listPatients(search, 100) });
  const client = useQueryClient();
  return <>
    <header><div><p className="eyebrow">CLINICAL RECORDS</p><h1>Patients</h1></div>{staffRoles.includes(user.role) && <button onClick={() => setShowForm(!showForm)}>{showForm ? 'Close' : 'New patient'}</button>}</header>
    {showForm && <PatientForm onSaved={() => { setShowForm(false); client.invalidateQueries({ queryKey: ['patients'] }); }} />}
    <section className="card">
      <label>Search patients<input placeholder="Name, phone or email" value={search} onChange={e => setSearch(e.target.value)} /></label>
      <QueryState loading={query.isLoading} empty={!query.isLoading && !query.isError && query.data?.length === 0} error={query.error} />
      {!query.isLoading && !query.isError && <div className="list">{query.data?.map(p => <article key={p.id}><Link to={`/patients/${p.id}`}><strong>{p.first_name} {p.last_name}</strong></Link><span>{p.phone || p.email || 'No contact details'}</span></article>)}</div>}
    </section>
  </>;
}

function PatientDetail({ user }: { user: User }) {
  const { id = '' } = useParams();
  const [editing, setEditing] = useState(false);
  const query = useQuery({ queryKey: ['patient', id], queryFn: () => getPatient(id) });
  const client = useQueryClient();
  if (query.isLoading) return <p className="muted">Loading patient\u2026</p>;
  if (query.isError || !query.data) return <ErrorState error={query.error} />;
  if (editing) return <><Link to="/patients">\u2190 Patients</Link><PatientForm initial={query.data} onSaved={() => { setEditing(false); client.invalidateQueries({ queryKey: ['patient', id] }); }} /></>;
  const p = query.data;
  return <>
    <header><div><p className="eyebrow">PATIENT DETAIL</p><h1>{p.first_name} {p.last_name}</h1></div>{canEditPatients(user.role) && <button onClick={() => setEditing(true)}>Edit patient</button>}</header>
    <section className="card">
      <h2>Contact</h2>
      <p>{p.phone || 'No phone'} \u00b7 {p.email || 'No email'}</p>
      <h2>Clinical alerts</h2>
      <p>{p.allergies || 'No allergies recorded.'}</p>
      <p>{p.medications || 'No medications recorded.'}</p>
      <p>{p.medical_alerts || 'No medical alerts recorded.'}</p>
      <h2>Notes</h2>
      <p>{p.notes || 'No notes recorded.'}</p>
    </section>
  </>;
}

function AppointmentForm({ onSaved }: { onSaved: () => void }) {
  const patients = useQuery({ queryKey: ['patients', 'appointment'], queryFn: () => listPatients(undefined, 100) });
  const doctors = useQuery({ queryKey: ['doctors'], queryFn: listDoctors });
  const rooms = useQuery({ queryKey: ['rooms'], queryFn: listRooms });
  const mutation = useMutation({ mutationFn: createAppointment, onSuccess: onSaved });
  const [patientSearch, setPatientSearch] = useState('');
  const searchedPatients = useQuery({ queryKey: ['patients', 'appointment-search', patientSearch], queryFn: () => listPatients(patientSearch, 100), enabled: patientSearch.length > 0 });
  const [form, setForm] = useState({ patient_id: '', doctor_id: '', room_id: '', starts_at: '', duration_minutes: 30, appointment_type: 'Consultation', status: 'scheduled', notes: '' });
  const patientOptions = patientSearch ? searchedPatients.data : patients.data;
  function submit(e: FormEvent) { e.preventDefault(); if (!form.starts_at) return; mutation.mutate({ ...form, starts_at: new Date(form.starts_at).toISOString(), duration_minutes: Number(form.duration_minutes), assistant_id: null }); }
  return <form className="card" onSubmit={submit}>
    <h2>New appointment</h2>
    <label>Find patient<input placeholder="Type a name, phone or email" value={patientSearch} onChange={e => setPatientSearch(e.target.value)} /></label>
    <label>Patient<select required value={form.patient_id} onChange={e => setForm({ ...form, patient_id: e.target.value })}><option value="">Select patient</option>{patientOptions?.map(p => <option key={p.id} value={p.id}>{p.first_name} {p.last_name}</option>)}</select></label>
    <label>Doctor<select required value={form.doctor_id} onChange={e => setForm({ ...form, doctor_id: e.target.value })}><option value="">Select authorized doctor</option>{doctors.data?.map((doctor: Doctor) => <option key={doctor.id} value={doctor.id}>{doctor.full_name} ({doctor.role.replace('_', ' ')})</option>)}</select></label>
    <label>Room<select required value={form.room_id} onChange={e => setForm({ ...form, room_id: e.target.value })}><option value="">Select active room</option>{rooms.data?.filter(r => r.is_active).map(r => <option key={r.id} value={r.id}>{r.name}</option>)}</select>{rooms.data?.every(r => !r.is_active) && <span className="muted">No active rooms. Ask a clinic manager to configure a room.</span>}</label>
    <label>Start<input type="datetime-local" required value={form.starts_at} onChange={e => setForm({ ...form, starts_at: e.target.value })} /></label>
    <label>Duration (minutes)<input type="number" min={5} max={1440} required value={form.duration_minutes} onChange={e => setForm({ ...form, duration_minutes: Number(e.target.value) })} /></label>
    {[patients, doctors, rooms, searchedPatients].some(q => q.isError) && <ErrorState error={[patients, doctors, rooms, searchedPatients].find(q => q.isError)?.error} />}
    {mutation.isError && <ErrorState error={mutation.error} />}
    <button disabled={mutation.isPending}>{mutation.isPending ? 'Booking\u2026' : 'Create appointment'}</button>
  </form>;
}

function Appointments({ user }: { user: User }) {
  const [showForm, setShowForm] = useState(false);
  const canCreate = ['clinic_manager', 'doctor', 'reception', 'administrator'].includes(user.role);
  const query = useQuery({ queryKey: ['appointments'], queryFn: listAppointments });
  const client = useQueryClient();
  return <>
    <header><div><p className="eyebrow">SCHEDULE</p><h1>Appointments</h1></div>{canCreate && <button onClick={() => setShowForm(!showForm)}>{showForm ? 'Close' : 'New appointment'}</button>}</header>
    {showForm && <AppointmentForm onSaved={() => { setShowForm(false); client.invalidateQueries({ queryKey: ['appointments'] }); }} />}
    <section className="card">
      <QueryState loading={query.isLoading} empty={!query.isLoading && !query.isError && query.data?.length === 0} error={query.error} />
      {!query.isLoading && !query.isError && query.data?.map(a => <article className="appointment" key={a.id}><strong>{new Date(a.starts_at).toLocaleString()}</strong><span>{a.appointment_type} \u00b7 {a.status}</span></article>)}
    </section>
  </>;
}

function ProcedureForm({ initial, onSaved, onCancel }: { initial?: ProcedureCatalog; onSaved: (procedure: ProcedureCatalog) => void; onCancel: () => void }) {
  const [form, setForm] = useState<{ code: string; name: string; description: string; category: string; default_duration_minutes: number; base_price: string; currency: string }>({
    code: initial?.code ?? '',
    name: initial?.name ?? '',
    description: initial?.description ?? '',
    category: initial?.category ?? '',
    default_duration_minutes: initial?.default_duration_minutes ?? 30,
    base_price: initial?.base_price ?? '',
    currency: initial?.currency ?? 'RON'
  });
  useEffect(() => {
    if (initial) {
      setForm({
        code: initial.code ?? '',
        name: initial.name ?? '',
        description: initial.description ?? '',
        category: initial.category ?? '',
        default_duration_minutes: initial.default_duration_minutes ?? 30,
        base_price: initial.base_price ?? '',
        currency: initial.currency ?? 'RON'
      });
    } else {
      setForm({
        code: '',
        name: '',
        description: '',
        category: '',
        default_duration_minutes: 30,
        base_price: '',
        currency: 'RON'
      });
    }
  }, [initial]);
  const mutation = useMutation({ mutationFn: async () => { const payload = { ...form, base_price: form.base_price }; return initial ? updateProcedure(initial.id, payload) : createProcedure(payload); }, onSuccess: onSaved });
  const set = (key: keyof typeof form, value: string | number) => setForm(current => ({ ...current, [key]: value }));
  return <form className="card" onSubmit={e => { e.preventDefault(); mutation.mutate(); }}>
    <h2>{initial ? 'Edit procedure' : 'New procedure'}</h2>
    <div className="grid two">
      <label>Code<input required value={form.code} onChange={e => set('code', e.target.value)} /></label>
      <label>Name<input required value={form.name} onChange={e => set('name', e.target.value)} /></label>
      <label>Category<input value={form.category} onChange={e => set('category', e.target.value)} /></label>
      <label>Currency<input required value={form.currency} onChange={e => set('currency', e.target.value)} /></label>
      <label>Duration (minutes)<input type="number" min={1} max={1440} required value={form.default_duration_minutes} onChange={e => set('default_duration_minutes', Number(e.target.value))} /></label>
      <label>Base price<input type="text" inputMode="decimal" pattern="[0-9]*\.?[0-9]*" required value={form.base_price} onChange={e => set('base_price', e.target.value)} /></label>
    </div>
    <label>Description<textarea value={form.description} onChange={e => set('description', e.target.value)} /></label>
    {mutation.isError && <ErrorState error={mutation.error} />}
    <div className="form-actions">
      <button type="button" className="secondary" onClick={onCancel} disabled={mutation.isPending}>Cancel</button>
      <button disabled={mutation.isPending}>{mutation.isPending ? 'Saving\u2026' : 'Save procedure'}</button>
    </div>
  </form>;
}

function Procedures({ user }: { user: User }) {
  const [search, setSearch] = useState('');
  const [activeFilter, setActiveFilter] = useState<'all' | 'active' | 'inactive'>('all');
  const [page, setPage] = useState(0);
  const [limit, setLimit] = useState(50);
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<ProcedureCatalog | undefined>(undefined);
  const client = useQueryClient();
  const query = useQuery({
    queryKey: ['procedures', search, activeFilter, page, limit],
    queryFn: () => listProcedures(search || undefined, activeFilter === 'active', limit, page * limit)
  });
  const countQuery = useQuery({
    queryKey: ['procedures-count', search, activeFilter],
    queryFn: () => countProcedures(search || undefined, activeFilter === 'active')
  });
  const activateMutation = useMutation({
    mutationFn: activateProcedure,
    onSuccess: () => { client.invalidateQueries({ queryKey: ['procedures'] }); },
    onError: (error) => { client.invalidateQueries({ queryKey: ['procedures'] }); }
  });
  const deactivateMutation = useMutation({
    mutationFn: deactivateProcedure,
    onSuccess: () => { client.invalidateQueries({ queryKey: ['procedures'] }); },
    onError: (error) => { client.invalidateQueries({ queryKey: ['procedures'] }); }
  });
  const canEdit = managementRoles.includes(user.role);

  useEffect(() => {
    setPage(0);
  }, [search, activeFilter]);

  const handleActivate = (id: string) => {
    activateMutation.mutate(id, {
      onSuccess: () => {},
      onError: (error) => {}
    });
  };

  const handleDeactivate = (id: string) => {
    deactivateMutation.mutate(id, {
      onSuccess: () => {},
      onError: (error) => {}
    });
  };

  const totalPages = query.data ? Math.ceil(query.data.length / limit) : 0;

  return <>
    <header><div><p className="eyebrow">PROCEDURE CATALOG</p><h1>Procedure catalog</h1></div>{canEdit && <button onClick={() => { setShowForm(!showForm); setEditing(undefined); }}>{showForm ? 'Close' : 'New procedure'}</button>}</header>
    {showForm && <ProcedureForm key={editing?.id ?? 'new'} initial={editing} onSaved={() => { setShowForm(false); setEditing(undefined); client.invalidateQueries({ queryKey: ['procedures'] }); }} onCancel={() => { setShowForm(false); setEditing(undefined); }} />}
    <section className="card">
      <label>Search procedures<input placeholder="Code, name, category or description" value={search} onChange={e => setSearch(e.target.value)} /></label>
      <label>Filter:
        <select value={activeFilter} onChange={e => setActiveFilter(e.target.value as 'all' | 'active' | 'inactive')}>
          <option value="all">All</option>
          <option value="active">Active only</option>
          <option value="inactive">Inactive only</option>
        </select>
      </label>
      <QueryState loading={query.isLoading} empty={!query.isLoading && !query.isError && query.data?.length === 0} error={query.error} />
      {!query.isLoading && !query.isError && query.data && <>
        <div className="list">
          {query.data.map(p => <article key={p.id} className="list-row">
            <span><Link to={`/procedures/${p.id}`}><strong>{p.code}</strong> - {p.name}</Link><br /><span className="muted">{p.category || 'No category'} \u00b7 {p.default_duration_minutes} min \u00b7 {p.base_price} {p.currency} \u00b7 {p.is_active ? 'Active' : 'Inactive'}</span></span>
            {canEdit && <>
              <button className="secondary" onClick={() => { setEditing(p); setShowForm(true); }}>Edit</button>
              {p.is_active ? <button className="secondary" onClick={() => handleDeactivate(p.id)} disabled={deactivateMutation.isPending}>Deactivate</button> : <button className="secondary" onClick={() => handleActivate(p.id)} disabled={activateMutation.isPending}>Activate</button>}
            </>}
          </article>)}
        </div>
        {countQuery.data !== undefined && <div className="pagination">
          <button className="secondary" disabled={page === 0} onClick={() => setPage(p => Math.max(0, p - 1))}>Previous</button>
          <span>Page {page + 1} of {Math.ceil(countQuery.data / limit)}</span>
          <button className="secondary" disabled={(page + 1) * limit >= countQuery.data} onClick={() => setPage(p => p + 1)}>Next</button>
        </div>}
      </>}
      {activateMutation.isError && <ErrorState error={activateMutation.error} />}
      {deactivateMutation.isError && <ErrorState error={deactivateMutation.error} />}
      {activateMutation.isPending && <p className="muted">Activating...</p>}
      {deactivateMutation.isPending && <p className="muted">Deactivating...</p>}
    </section>
  </>;
}

function ProcedureDetail({ user }: { user: User }) {
  const { id = '' } = useParams();
  const query = useQuery({ queryKey: ['procedure', id], queryFn: () => getProcedure(id) });
  const client = useQueryClient();
  if (query.isLoading) return <p className="muted">Loading procedure\u2026</p>;
  if (query.isError || !query.data) return <ErrorState error={query.error} />;
  const p = query.data;
  const canEdit = managementRoles.includes(user.role);
  return <>
    <Link to="/procedures">\u2190 Procedures</Link>
    <section className="card">
      <p className="eyebrow">PROCEDURE DETAIL</p>
      <h1>{p.code} - {p.name}</h1>
      <p><strong>Category:</strong> {p.category || 'No category'}</p>
      <p><strong>Duration:</strong> {p.default_duration_minutes} minutes</p>
      <p><strong>Price:</strong> {p.base_price} {p.currency}</p>
      <p><strong>Status:</strong> {p.is_active ? 'Active' : 'Inactive'}</p>
      <p><strong>Description:</strong></p>
      <p>{p.description || 'No description'}</p>
      {canEdit && <button onClick={() => client.invalidateQueries({ queryKey: ['procedure', id] })}>Refresh</button>}
    </section>
  </>;
}

function ClinicSettings() {
  const query = useQuery({ queryKey: ['clinic'], queryFn: getClinic });
  const mutation = useMutation({ mutationFn: updateClinic, onSuccess: () => query.refetch() });
  const [form, setForm] = useState<Pick<Clinic, 'name' | 'timezone'> | null>(null);
  useEffect(() => { if (query.data) setForm({ name: query.data.name, timezone: query.data.timezone }); }, [query.data]);
  if (query.isLoading || !form) return <p className="muted">Loading clinic settings\u2026</p>;
  return <section className="card">
    <h2>Clinic settings</h2>
    <p className="muted">Changing the timezone changes display context only; stored appointment instants are preserved.</p>
    <form onSubmit={e => { e.preventDefault(); mutation.mutate(form); }}>
      <label>Display name<input required value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} /></label>
      <label>Timezone<input required value={form.timezone} onChange={e => setForm({ ...form, timezone: e.target.value })} /></label>
      {mutation.isError && <ErrorState error={mutation.error} />}
      <button disabled={mutation.isPending}>Save settings</button>
    </form>
  </section>;
}

function Rooms() {
  const client = useQueryClient();
  const query = useQuery({ queryKey: ['rooms'], queryFn: listRooms });
  const [name, setName] = useState('');
  const [editingId, setEditingId] = useState('');
  const [editingName, setEditingName] = useState('');
  const [message, setMessage] = useState('');
  const create = useMutation({ mutationFn: () => createRoom(name), onSuccess: () => { setName(''); setMessage('Room created.'); client.invalidateQueries({ queryKey: ['rooms'] }); }, onError: error => setMessage(apiErrorMessage(error)) });
  const update = useMutation({ mutationFn: ({ id, input }: { id: string; input: { name?: string; is_active?: boolean } }) => updateRoom(id, input), onSuccess: data => { if (data.affected_appointments.length) setMessage(`Cannot deactivate this room. Future appointments: ${data.affected_appointments.map(item => `${new Date(item.starts_at).toLocaleString()} (${item.id.slice(0, 8)})`).join(', ')}`); else { setEditingId(''); setMessage('Room updated.'); client.invalidateQueries({ queryKey: ['rooms'] }); } }, onError: error => setMessage(apiErrorMessage(error)) });
  return <section className="card">
    <h2>Treatment rooms</h2>
    <form className="inline-form" onSubmit={e => { e.preventDefault(); create.mutate(); }}>
      <input required placeholder="New room name" value={name} onChange={e => setName(e.target.value)} />
      <button disabled={create.isPending}>Create room</button>
    </form>
    {message && <p className="notice-text" role="alert">{message}</p>}
    {query.isError && <ErrorState error={query.error} />}
    <div className="list">
      {query.data?.map(room => <article key={room.id}>
        {editingId === room.id ?
          <>
            <input value={editingName} onChange={e => setEditingName(e.target.value)} />
            <button className="secondary" onClick={() => update.mutate({ id: room.id, input: { name: editingName } })}>Save name</button>
            <button className="secondary" onClick={() => setEditingId('')}>Cancel</button>
          </> :
          <>
            <span><strong>{room.name}</strong> \u00b7 {room.is_active ? 'Active' : 'Inactive'}</span>
            <span>
              <button className="secondary" onClick={() => { setEditingId(room.id); setEditingName(room.name); }}>Rename</button>
              <button className="secondary" onClick={() => update.mutate({ id: room.id, input: { is_active: !room.is_active } })}>{room.is_active ? 'Deactivate' : 'Activate'}</button>
            </span>
          </>}
        </article>)
      }
    </div>
  </section>;
}

function Setup({ user }: { user: User }) {
  const rooms = useQuery({ queryKey: ['rooms'], queryFn: listRooms });
  return <>
    <header><div><p className="eyebrow">FIRST-RUN SETUP</p><h1>Clinic setup</h1></div></header>
    <ClinicSettings />
    <Rooms />
    <section className="card notice">
      <h2>Next step</h2>
      <p>{rooms.data?.length ? 'Invite your doctor, assistant, and receptionist from Staff.' : 'Create at least one active treatment room before booking appointments.'}</p>
      <Link className="button" to="/staff">Open staff onboarding</Link>
    </section>
  </>;
}

export function InvitationLinkNotice({ link, onCopy }: { link: string; onCopy: () => void }) {
  return <div className="invite-link">
    <label>New invitation link \u2014 copy now<input readOnly value={link} onFocus={e => e.currentTarget.select()} /></label>
    <button onClick={onCopy}>Copy link</button>
  </div>;
}

export function StaffOnboarding() {
  const client = useQueryClient();
  const staff = useQuery({ queryKey: ['staff'], queryFn: listStaff });
  const invitations = useQuery({ queryKey: ['invitations'], queryFn: listInvitations });
  const [form, setForm] = useState({ email: '', full_name: '', role: 'doctor' });
  const [latestInvitation, setLatestInvitation] = useState<Invitation | null>(null);
  const [message, setMessage] = useState('');
  const invite = useMutation({ mutationFn: () => createInvitation(form), onSuccess: data => { setLatestInvitation(data); setForm({ email: '', full_name: '', role: 'doctor' }); setMessage('Invitation created. The secret is shown only now; copy it before leaving this page.'); client.invalidateQueries({ queryKey: ['invitations'] }); }, onError: error => setMessage(apiErrorMessage(error)) });
  const reissue = useMutation({ mutationFn: reissueInvitation, onSuccess: data => { setLatestInvitation(data); setMessage('Invitation reissued. The previous link is revoked; copy this new link now.'); client.invalidateQueries({ queryKey: ['invitations'] }); }, onError: error => setMessage(apiErrorMessage(error)) });
  const revoke = useMutation({ mutationFn: revokeInvitation, onSuccess: () => { setMessage('Invitation revoked.'); client.invalidateQueries({ queryKey: ['invitations'] }); }, onError: error => setMessage(apiErrorMessage(error)) });
  const deactivate = useMutation({ mutationFn: deactivateStaff, onSuccess: data => { if (data.affected_appointments.length) setMessage(`Cannot deactivate ${data.staff.full_name}. Future appointments: ${data.affected_appointments.map(item => `${new Date(item.starts_at).toLocaleString()} (${item.id.slice(0, 8)})`).join(', ')}`); else { setMessage('Staff member deactivated.'); client.invalidateQueries({ queryKey: ['staff'] }); } }, onError: error => setMessage(apiErrorMessage(error)) });
  async function copyLink(invitation: Invitation) {
    if (!invitation.invitation_url) { setMessage('The original link is no longer available. Reissue the invitation to create a new link.'); return; }
    const link = `${window.location.origin}${invitation.invitation_url}`;
    try { await navigator.clipboard.writeText(link); setMessage('Invitation link copied. No email was sent.'); } catch { setMessage(`Clipboard unavailable. Select and copy this link manually: ${link}`); }
  }
  return <>
    <header><div><p className="eyebrow">PEOPLE & ACCESS</p><h1>Staff onboarding</h1></div></header>
    {message && <p className="notice-text" role="alert">{message}</p>}
    <section className="card">
      <h2>Invite staff</h2>
      <p className="muted">The link is single-use and expires after three days. The recipient chooses their own password.</p>
      <form className="grid two" onSubmit={e => { e.preventDefault(); invite.mutate(); }}>
        <label>Full name<input required value={form.full_name} onChange={e => setForm({ ...form, full_name: e.target.value })} /></label>
        <label>Email<input required type="email" value={form.email} onChange={e => setForm({ ...form, email: e.target.value })} /></label>
        <label>Role<select value={form.role} onChange={e => setForm({ ...form, role: e.target.value })}>
          <option value="doctor">Doctor</option>
          <option value="assistant">Assistant</option>
          <option value="reception">Receptionist</option>
        </select></label>
        <div><button disabled={invite.isPending}>Create invitation link</button></div>
      </form>
      {latestInvitation?.invitation_url && <InvitationLinkNotice link={`${window.location.origin}${latestInvitation.invitation_url}`} onCopy={() => void copyLink(latestInvitation)} />}
      <p className="muted">After reload, original invitation links cannot be retrieved. Use Reissue to generate a new link.</p>
    </section>
    <section className="card">
      <h2>Active and inactive staff</h2>
      {staff.isError && <ErrorState error={staff.error} />}
      {staff.data?.map(person => <article className="list-row" key={person.id}><span><Link to={`/staff/${person.id}`}><strong>{person.full_name}</strong></Link><br />{person.email} \u00b7 {person.role} \u00b7 {person.is_active ? 'Active' : 'Inactive'}</span>{person.is_active && !managementRoles.includes(person.role) && <button className="secondary" onClick={() => deactivate.mutate(person.id)}>Deactivate</button>}</article>)}
    </section>
    <section className="card">
      <h2>Invitation history</h2>
      {invitations.data?.map(inv => <article className="list-row" key={inv.id}><span><strong>{inv.full_name}</strong> \u00b7 {inv.role}<br /><span className="muted">{inv.accepted_at ? 'Accepted' : inv.revoked_at ? 'Revoked' : `Expires ${new Date(inv.expires_at).toLocaleString()}`}</span></span>{!inv.accepted_at && !inv.revoked_at && <><button className="secondary" onClick={() => reissue.mutate(inv.id)}>Reissue</button><button className="secondary" onClick={() => revoke.mutate(inv.id)}>Revoke</button></>}</article>)}
    </section>
  </>;
}

function StaffDetail() {
  const { id = '' } = useParams();
  const query = useQuery({ queryKey: ['staff', id], queryFn: () => getStaff(id) });
  if (query.isLoading) return <p className="muted">Loading staff member\u2026</p>;
  if (query.isError || !query.data) return <ErrorState error={query.error} />;
  const person = query.data;
  return <>
    <Link to="/staff">\u2190 Staff</Link>
    <section className="card">
      <p className="eyebrow">STAFF DETAIL</p>
      <h1>{person.full_name}</h1>
      <p>{person.email} \u00b7 {person.role} \u00b7 {person.is_active ? 'Active' : 'Inactive'}</p>
      <p>{person.phone || 'No phone recorded'} \u00b7 {person.specialization || 'No specialization recorded'}</p>
      <p>Upcoming active appointments: {person.upcoming_appointment_count}</p>
    </section>
  </>;
}

function InvitationAccept() {
  const params = new URLSearchParams(useLocation().search);
  const token = params.get('token') ?? '';
  const [password, setPassword] = useState('');
  const [done, setDone] = useState(false);
  const mutation = useMutation({ mutationFn: () => acceptInvitation(token, password), onSuccess: () => setDone(true) });
  return <main className="auth">
    <form className="card" onSubmit={e => { e.preventDefault(); mutation.mutate(); }}>
      <p className="eyebrow">DENTACARE INVITATION</p>
      <h1>Activate staff account</h1>
      {done ? <><p>Your account is active. You can now sign in.</p><Link className="button" to="/">Go to sign in</Link></> : <>
        <label>Password<input required minLength={12} type="password" value={password} onChange={e => setPassword(e.target.value)} /></label>
        {mutation.isError && <ErrorState error={mutation.error} />}
        <button disabled={mutation.isPending || !token}>Set password and activate</button>
      </>}
    </form>
  </main>;
}

function Home({ user }: { user: User }) {
  const count = useQuery({ queryKey: ['patient-count'], queryFn: countPatients });
  const appointments = useQuery({ queryKey: ['appointments'], queryFn: listAppointments });
  return <>
    <header><div><p className="eyebrow">CLINIC OVERVIEW</p><h1>Good morning, {user.full_name.split(' ')[0]}</h1></div></header>
    <section className="stats">
      <div className="card"><span>Patients</span><strong>{count.isLoading ? '\u2014' : count.data}</strong></div>
      <div className="card"><span>Appointments</span><strong>{appointments.isLoading ? '\u2014' : appointments.data?.length}</strong></div>
      <div className="card"><span>Clinic</span><strong className="small">{user.clinic_id.slice(0, 8)}</strong></div>
    </section>
    {managementRoles.includes(user.role) && <section className="card notice">
      <h2>Setup actions</h2>
      <p>Configure the clinic timezone, add active rooms, and onboard staff before booking.</p>
      <Link className="button" to="/setup">Open clinic setup</Link>
    </section>}
  </>;
}

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();
  const location = useLocation();
  useEffect(() => { const handler = () => { setUser(null); navigate('/'); }; window.addEventListener('dentacare:session-cleared', handler); return () => window.removeEventListener('dentacare:session-cleared', handler); }, [navigate]);
  useEffect(() => { if (localStorage.getItem('access_token')) me().then(setUser).catch(() => setUser(null)).finally(() => setLoading(false)); else setLoading(false); }, []);
  if (location.pathname === '/staff/invitations/accept') return <InvitationAccept />;
  if (loading) return <main className="auth"><p>Loading session\u2026</p></main>;
  if (!user) return <Auth onDone={() => me().then(setUser).catch(() => setUser(null))} />;
  const signOut = async () => { try { await logout(); } catch (error) { console.error('Server-side logout failed; local session was cleared.', error); } finally { setUser(null); navigate('/'); } };
  return <Layout user={user} onLogout={signOut}>
    <Routes>
      <Route path="/" element={<Home user={user} />} />
      <Route path="/patients" element={<Patients user={user} />} />
      <Route path="/patients/:id" element={<PatientDetail user={user} />} />
      <Route path="/appointments" element={<Appointments user={user} />} />
      <Route path="/procedures" element={<Procedures user={user} />} />
      <Route path="/procedures/:id" element={<ProcedureDetail user={user} />} />
      <Route path="/setup" element={managementRoles.includes(user.role) ? <Setup user={user} /> : <Navigate to="/" />} />
      <Route path="/staff" element={managementRoles.includes(user.role) ? <StaffOnboarding /> : <Navigate to="/" />} />
      <Route path="/staff/:id" element={managementRoles.includes(user.role) ? <StaffDetail /> : <Navigate to="/" />} />
      <Route path="*" element={<Navigate to="/" />} />
    </Routes>
  </Layout>;
}
