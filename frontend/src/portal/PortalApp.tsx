import { FormEvent, useEffect, useMemo, useState } from 'react';
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AxiosError } from 'axios';
import {
  acceptPortalInvitation, clinicIdentity, confirmAppointment, downloadPortalDocument, hasPortalSession, invitationInfo, portalAppointments,
  PortalAppointment, portalDocuments, portalError, portalForm, portalForms, portalLogin, portalLogout, portalMe, portalPlans,
  PortalQuestion, rememberClinic, rememberedClinic, requestReschedule, savePortalAnswers, submitPortalForm,
} from './api';
import './portal.css';

const RELATIONSHIPS: Record<string, string> = { self: 'Eu', parent: 'Copil', guardian: 'Persoană în grijă', other: 'Persoană reprezentată' };
const dayFormat = new Intl.DateTimeFormat('ro-RO', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' });
const shortFormat = new Intl.DateTimeFormat('ro-RO', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' });
const longDay = (localDate: string) => dayFormat.format(new Date(`${localDate}T12:00:00Z`));
const shortDay = (iso: string) => shortFormat.format(new Date(iso));
const newKey = () => (typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`.replace(/[^0-9a-f]/g, '').padEnd(32, '0').replace(/^(.{8})(.{4})(.{4})(.{4})(.{12}).*$/, '$1-$2-$3-$4-$5'));

function Alert({ error }: { error: unknown }) { return <p className="portal-alert" role="alert">{portalError(error)}</p>; }

export function PortalApp() {
  const location = useLocation();
  const [signedIn, setSignedIn] = useState(hasPortalSession());
  useEffect(() => {
    const handler = () => setSignedIn(false);
    window.addEventListener('dentacare:portal-session-cleared', handler);
    return () => window.removeEventListener('dentacare:portal-session-cleared', handler);
  }, []);
  let body;
  if (location.pathname === '/portal/invitatie') body = <InvitationPage />;
  else if (!signedIn) body = <LoginPage onDone={() => setSignedIn(true)} />;
  else body = <PortalHome onSignedOut={() => setSignedIn(false)} />;
  return <div className="portal">{body}</div>;
}

function useClinic() {
  const [params] = useSearchParams();
  const fromLink = params.get('clinic');
  const [clinic, setClinic] = useState(rememberedClinic());
  const lookup = useQuery({ queryKey: ['portal-clinic', fromLink], queryFn: () => clinicIdentity(fromLink!), enabled: Boolean(fromLink) && fromLink !== clinic?.id, retry: false });
  useEffect(() => { if (lookup.data) { rememberClinic(lookup.data); setClinic(lookup.data); } }, [lookup.data]);
  return { clinic: fromLink && fromLink !== clinic?.id ? lookup.data ?? null : clinic, loading: lookup.isLoading, missing: lookup.isError };
}

function LoginPage({ onDone }: { onDone: () => void }) {
  const { clinic, loading, missing } = useClinic();
  const [form, setForm] = useState({ email: '', password: '' });
  const login = useMutation({ mutationFn: () => portalLogin(clinic!.id, form.email.trim(), form.password), onSuccess: onDone });
  if (loading) return <main className="portal-card"><p>Se încarcă…</p></main>;
  if (!clinic || missing) return <main className="portal-card">
    <p className="portal-eyebrow">Portalul pacientului</p>
    <h1>Deschide linkul primit de la clinică</h1>
    <p>Pentru a intra în portal, folosește linkul de invitație sau linkul de autentificare primit de la clinica ta.</p>
  </main>;
  return <main className="portal-card">
    <p className="portal-eyebrow">Portalul pacientului</p>
    <h1>{clinic.name}</h1>
    <form onSubmit={(event: FormEvent) => { event.preventDefault(); login.mutate(); }}>
      <label>Email<input type="email" autoComplete="username" required value={form.email} onChange={event => setForm({ ...form, email: event.target.value })} /></label>
      <label>Parolă<input type="password" autoComplete="current-password" required value={form.password} onChange={event => setForm({ ...form, password: event.target.value })} /></label>
      {login.isError && <Alert error={login.error} />}
      <button className="portal-primary" disabled={login.isPending}>{login.isPending ? 'Se verifică…' : 'Intră în portal'}</button>
    </form>
    <p className="portal-muted">Ai uitat parola? Cere clinicii o invitație nouă.</p>
  </main>;
}

function InvitationPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const token = params.get('token') ?? '';
  const info = useQuery({ queryKey: ['portal-invitation', token], queryFn: () => invitationInfo(token), enabled: token.length >= 20, retry: false });
  const [password, setPassword] = useState('');
  const [repeat, setRepeat] = useState('');
  const [mismatch, setMismatch] = useState(false);
  const accept = useMutation({
    mutationFn: () => acceptPortalInvitation(token, password),
    onSuccess: data => { rememberClinic({ id: data.clinic_id, name: data.clinic_name }); navigate(`/portal?clinic=${data.clinic_id}`, { replace: true }); },
  });
  if (!token || info.isError) return <main className="portal-card"><p className="portal-eyebrow">Portalul pacientului</p><h1>Invitație nevalabilă</h1>
    <p>Invitația a expirat, a fost folosită deja sau a fost înlocuită. Cere clinicii o invitație nouă.</p></main>;
  if (info.isLoading) return <main className="portal-card"><p>Se încarcă…</p></main>;
  return <main className="portal-card">
    <p className="portal-eyebrow">{info.data?.clinic_name}</p>
    <h1>Setează parola</h1>
    <p>Bun venit, {info.data?.full_name}. Contul tău este {info.data?.email}.</p>
    <form onSubmit={(event: FormEvent) => { event.preventDefault(); setMismatch(password !== repeat); if (password === repeat) accept.mutate(); }}>
      <label>Parolă nouă (minimum 12 caractere)<input type="password" autoComplete="new-password" minLength={12} maxLength={128} required value={password} onChange={event => setPassword(event.target.value)} /></label>
      <label>Repetă parola<input type="password" autoComplete="new-password" minLength={12} maxLength={128} required value={repeat} onChange={event => setRepeat(event.target.value)} /></label>
      {mismatch && <p className="portal-alert" role="alert">Parolele nu coincid.</p>}
      {accept.isError && <Alert error={accept.error} />}
      <button className="portal-primary" disabled={accept.isPending}>{accept.isPending ? 'Se salvează…' : 'Salvează parola'}</button>
    </form>
  </main>;
}

type Tab = 'appointments' | 'documents' | 'plans' | 'forms';
const TABS: Array<[Tab, string]> = [['appointments', 'Programări'], ['documents', 'Documente'], ['plans', 'Planuri'], ['forms', 'Formulare']];

function PortalHome({ onSignedOut }: { onSignedOut: () => void }) {
  const me = useQuery({ queryKey: ['portal-me'], queryFn: portalMe });
  const client = useQueryClient();
  const [patientId, setPatientId] = useState<string>('');
  const [tab, setTab] = useState<Tab>('appointments');
  useEffect(() => { if (me.data && !me.data.patients.some(row => row.id === patientId)) setPatientId(me.data.patients[0]?.id ?? ''); }, [me.data, patientId]);
  useEffect(() => { if (me.data) rememberClinic({ id: me.data.clinic.id, name: me.data.clinic.name }); }, [me.data]);
  async function signOut() { await portalLogout(); client.removeQueries({ predicate: query => String(query.queryKey[0]).startsWith('portal') }); onSignedOut(); }
  if (me.isLoading) return <main className="portal-card"><p>Se încarcă…</p></main>;
  if (me.isError || !me.data) return <main className="portal-card"><Alert error={me.error} /><button className="portal-secondary" onClick={() => void signOut()}>Ieșire</button></main>;
  const patient = me.data.patients.find(row => row.id === patientId);
  return <>
    <header className="portal-header">
      <div><p className="portal-eyebrow">{me.data.clinic.name}</p><p className="portal-account">{me.data.account.full_name}</p></div>
      <button className="portal-secondary" onClick={() => void signOut()}>Ieșire</button>
    </header>
    {me.data.patients.length === 0 ? <main className="portal-card"><p>Contul nu are acces la niciun pacient. Contactează clinica.</p></main> : <main className="portal-main">
      {me.data.patients.length > 1 ? <label className="portal-patient">Pacient<select value={patientId} onChange={event => setPatientId(event.target.value)}>
        {me.data.patients.map(row => <option key={row.id} value={row.id}>{row.name} ({RELATIONSHIPS[row.relationship] ?? row.relationship})</option>)}
      </select></label> : <h1 className="portal-title">{patient?.name}</h1>}
      <nav className="portal-tabs" aria-label="Secțiuni">
        {TABS.map(([key, label]) => <button key={key} type="button" aria-pressed={tab === key} onClick={() => setTab(key)}>{label}</button>)}
      </nav>
      {patientId && tab === 'appointments' && <Appointments key={patientId} patientId={patientId} />}
      {patientId && tab === 'documents' && <Documents key={patientId} patientId={patientId} />}
      {patientId && tab === 'plans' && <Plans key={patientId} patientId={patientId} />}
      {patientId && tab === 'forms' && <Forms key={patientId} patientId={patientId} />}
    </main>}
  </>;
}

function Appointments({ patientId }: { patientId: string }) {
  const query = useQuery({ queryKey: ['portal-appointments', patientId], queryFn: () => portalAppointments(patientId) });
  if (query.isLoading) return <p className="portal-muted">Se încarcă programările…</p>;
  if (query.isError || !query.data) return <Alert error={query.error} />;
  return <section aria-label="Programări">
    <h2>Programări viitoare</h2>
    {query.data.upcoming.length === 0 && <p className="portal-muted">Nu ai programări viitoare.</p>}
    {query.data.upcoming.map(row => <AppointmentCard key={row.id} patientId={patientId} appointment={row} />)}
    {query.data.past.length > 0 && <details className="portal-past"><summary>Programări trecute ({query.data.past.length})</summary>
      {query.data.past.map(row => <article key={row.id} className="portal-item"><strong>{longDay(row.local_date)}, ora {row.local_time}</strong><span>{row.doctor_name} · {row.status_label}</span></article>)}
    </details>}
  </section>;
}

function AppointmentCard({ patientId, appointment }: { patientId: string; appointment: PortalAppointment }) {
  const client = useQueryClient();
  const refresh = () => client.invalidateQueries({ queryKey: ['portal-appointments', patientId] });
  const confirm = useMutation({ mutationFn: () => confirmAppointment(patientId, appointment.id), onSuccess: refresh });
  const [asking, setAsking] = useState(false);
  const [message, setMessage] = useState('');
  const key = useMemo(() => newKey(), [asking]); // eslint-disable-line react-hooks/exhaustive-deps
  const ask = useMutation({ mutationFn: () => requestReschedule(patientId, appointment.id, message.trim(), key), onSuccess: () => { setAsking(false); setMessage(''); void refresh(); } });
  return <article className="portal-item portal-appointment" aria-label={`Programare ${appointment.local_date} ${appointment.local_time}`}>
    <strong>{longDay(appointment.local_date)}</strong>
    <span className="portal-time">Ora {appointment.local_time} · {appointment.duration_minutes} min</span>
    <span>{appointment.doctor_name} · {appointment.clinic_name}</span>
    <span className="portal-status">{appointment.status_label}</span>
    {appointment.confirmed_by_patient_at && <p className="portal-ok">Ai confirmat prezența.</p>}
    {appointment.reschedule_request && <p className="portal-note">Cererea de reprogramare a fost trimisă. Clinica te va contacta.</p>}
    <div className="portal-actions">
      {appointment.can_confirm && <button className="portal-primary" disabled={confirm.isPending} onClick={() => confirm.mutate()}>{confirm.isPending ? 'Se confirmă…' : 'Confirm prezența'}</button>}
      {appointment.can_request_reschedule && !asking && <button className="portal-secondary" onClick={() => setAsking(true)}>Cer reprogramare</button>}
    </div>
    {confirm.isError && <Alert error={confirm.error} />}
    {asking && <form className="portal-request" onSubmit={(event: FormEvent) => { event.preventDefault(); if (message.trim()) ask.mutate(); }}>
      <label>Mesaj pentru clinică<textarea required maxLength={1000} value={message} placeholder="De exemplu: pot doar după ora 16." onChange={event => setMessage(event.target.value)} /></label>
      <p className="portal-muted">Programarea rămâne neschimbată până când clinica îți răspunde.</p>
      {ask.isError && <Alert error={ask.error} />}
      <div className="portal-actions"><button className="portal-primary" disabled={ask.isPending || !message.trim()}>{ask.isPending ? 'Se trimite…' : 'Trimite cererea'}</button>
        <button type="button" className="portal-secondary" onClick={() => setAsking(false)}>Renunță</button></div>
    </form>}
  </article>;
}

function Documents({ patientId }: { patientId: string }) {
  const query = useQuery({ queryKey: ['portal-documents', patientId], queryFn: () => portalDocuments(patientId) });
  const [error, setError] = useState<string | null>(null);
  async function download(id: string, filename: string) {
    setError(null);
    try {
      const blob = await downloadPortalDocument(patientId, id);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url; link.download = filename; link.rel = 'noopener';
      document.body.appendChild(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) {
      setError(err instanceof AxiosError && err.response?.status === 404 ? 'Documentul nu mai este disponibil.' : portalError(err));
    }
  }
  if (query.isLoading) return <p className="portal-muted">Se încarcă documentele…</p>;
  if (query.isError || !query.data) return <Alert error={query.error} />;
  return <section aria-label="Documente">
    <h2>Documente</h2>
    {error && <p className="portal-alert" role="alert">{error}</p>}
    {query.data.length === 0 && <p className="portal-muted">Clinica nu a publicat încă documente pentru tine.</p>}
    {query.data.map(row => <article key={row.id} className="portal-item">
      <strong className="portal-filename">{row.filename}</strong>
      <span>Publicat pe {shortDay(row.published_at)} · {Math.max(1, Math.round(row.size_bytes / 1024))} KB</span>
      <div className="portal-actions"><button className="portal-secondary" onClick={() => void download(row.id, row.filename)}>Descarcă</button></div>
    </article>)}
  </section>;
}

function Plans({ patientId }: { patientId: string }) {
  const query = useQuery({ queryKey: ['portal-plans', patientId], queryFn: () => portalPlans(patientId) });
  if (query.isLoading) return <p className="portal-muted">Se încarcă planurile…</p>;
  if (query.isError || !query.data) return <Alert error={query.error} />;
  return <section aria-label="Planuri">
    <h2>Planuri de tratament</h2>
    {query.data.length === 0 && <p className="portal-muted">Clinica nu a publicat încă un plan de tratament.</p>}
    {query.data.map(plan => <article key={plan.id} className="portal-item portal-plan">
      <strong>{plan.title}</strong>
      <span>Versiunea {plan.version}, publicată pe {shortDay(plan.published_at)}</span>
      {plan.stages.map(stage => <div key={stage.position} className="portal-stage">
        <h3>Etapa {stage.position}: {stage.title}</h3>
        <ul>{stage.items.map((item, index) => <li key={index}><span>{item.procedure_name}{item.tooth_fdi ? ` · dinte ${item.tooth_fdi}${item.tooth_surface ? ` ${item.tooth_surface}` : ''}` : ''}</span><span className="portal-price">{item.estimated_price} {item.currency}</span></li>)}</ul>
      </div>)}
      {plan.totals.map(total => <p key={total.currency} className="portal-total">Estimare totală: <strong>{total.estimated_total} {total.currency}</strong> · aproximativ {total.estimated_minutes} min</p>)}
      <p className="portal-muted">Estimarea poate fi actualizată de medic pe parcursul tratamentului.</p>
    </article>)}
  </section>;
}

function Forms({ patientId }: { patientId: string }) {
  const query = useQuery({ queryKey: ['portal-forms', patientId], queryFn: () => portalForms(patientId) });
  const [open, setOpen] = useState<string | null>(null);
  if (open) return <FormEditor patientId={patientId} formId={open} onClose={() => setOpen(null)} />;
  if (query.isLoading) return <p className="portal-muted">Se încarcă formularele…</p>;
  if (query.isError || !query.data) return <Alert error={query.error} />;
  return <section aria-label="Formulare">
    <h2>Formulare</h2>
    {query.data.length === 0 && <p className="portal-muted">Nu ai formulare de completat.</p>}
    {query.data.map(row => <article key={row.id} className="portal-item">
      <strong>{row.name}</strong><span className="portal-status">{row.status_label}</span>
      <div className="portal-actions"><button className={row.editable ? 'portal-primary' : 'portal-secondary'} onClick={() => setOpen(row.id)}>{row.editable ? 'Completează' : 'Vezi răspunsurile'}</button></div>
    </article>)}
  </section>;
}

function shown(question: PortalQuestion, answers: Record<string, unknown>) {
  if (!question.condition) return true;
  const value = answers[question.condition.question_id];
  return (typeof value === 'boolean' ? (value ? 'yes' : 'no') : value) === question.condition.value;
}

function Field({ question, value, disabled, onChange }: { question: PortalQuestion; value: unknown; disabled: boolean; onChange: (value: unknown) => void }) {
  const label = `${question.label}${question.required ? ' *' : ''}`;
  if (question.type === 'yes_no') return <fieldset className="portal-choice" disabled={disabled}><legend>{label}</legend>
    <label><input type="radio" name={question.id} checked={value === true} onChange={() => onChange(true)} />Da</label>
    <label><input type="radio" name={question.id} checked={value === false} onChange={() => onChange(false)} />Nu</label></fieldset>;
  if (question.type === 'single_choice') return <fieldset className="portal-choice" disabled={disabled}><legend>{label}</legend>
    {question.options?.map(option => <label key={option.value}><input type="radio" name={question.id} checked={value === option.value} onChange={() => onChange(option.value)} />{option.label}</label>)}</fieldset>;
  if (question.type === 'multiple_choice') return <fieldset className="portal-choice" disabled={disabled}><legend>{label}</legend>
    {question.options?.map(option => <label key={option.value}><input type="checkbox" checked={Array.isArray(value) && value.includes(option.value)} onChange={event => {
      const current = Array.isArray(value) ? value as string[] : [];
      onChange(event.target.checked ? [...current, option.value] : current.filter(item => item !== option.value));
    }} />{option.label}</label>)}</fieldset>;
  if (question.type === 'long_text') return <label>{label}<textarea disabled={disabled} maxLength={5000} value={typeof value === 'string' ? value : ''} onChange={event => onChange(event.target.value)} /></label>;
  return <label>{label}<input disabled={disabled} type={question.type === 'number' ? 'number' : question.type === 'date' ? 'date' : 'text'} maxLength={question.type === 'short_text' ? 500 : undefined}
    value={value == null ? '' : String(value)} onChange={event => onChange(question.type === 'number' ? (event.target.value === '' ? null : Number(event.target.value)) : event.target.value)} /></label>;
}

function clean(answers: Record<string, unknown>, questions: PortalQuestion[]) {
  const visible = questions.filter(question => shown(question, answers));
  return Object.fromEntries(visible.map(question => [question.id, answers[question.id]]).filter(([, value]) => value !== undefined && value !== null && value !== '' && !(Array.isArray(value) && value.length === 0)));
}

function FormEditor({ patientId, formId, onClose }: { patientId: string; formId: string; onClose: () => void }) {
  const client = useQueryClient();
  const query = useQuery({ queryKey: ['portal-form', patientId, formId], queryFn: () => portalForm(patientId, formId) });
  const [answers, setAnswers] = useState<Record<string, unknown>>({});
  const [saved, setSaved] = useState('');
  useEffect(() => { if (query.data?.answers) setAnswers(query.data.answers); }, [query.data]);
  const questions = useMemo(() => query.data?.definition?.sections.flatMap(section => section.questions) ?? [], [query.data]);
  const save = useMutation({ mutationFn: () => savePortalAnswers(patientId, formId, clean(answers, questions)), onSuccess: () => setSaved('Răspunsurile au fost salvate. Poți reveni mai târziu.') });
  const submit = useMutation({
    mutationFn: () => submitPortalForm(patientId, formId, clean(answers, questions)),
    onSuccess: () => { void client.invalidateQueries({ queryKey: ['portal-forms', patientId] }); void client.invalidateQueries({ queryKey: ['portal-form', patientId, formId] }); setSaved('Formularul a fost trimis clinicii.'); },
  });
  if (query.isLoading) return <p className="portal-muted">Se încarcă formularul…</p>;
  if (query.isError || !query.data) return <><Alert error={query.error} /><button className="portal-secondary" onClick={onClose}>Înapoi</button></>;
  const editable = query.data.editable;
  return <section aria-label={query.data.name} className="portal-form">
    <button type="button" className="portal-link" onClick={onClose}>← Formulare</button>
    <h2>{query.data.name}</h2>
    <p className="portal-status">{query.data.status_label}</p>
    {query.data.status === 'needs_clarification' && <p className="portal-note">Clinica a cerut clarificări. Verifică răspunsurile și trimite din nou sau contactează clinica.</p>}
    <form onSubmit={(event: FormEvent) => { event.preventDefault(); setSaved(''); submit.mutate(); }}>
      {query.data.definition?.sections.map(section => <fieldset key={section.id} className="portal-section"><legend>{section.title}</legend>
        {section.questions.filter(question => shown(question, answers)).map(question => <Field key={question.id} question={question} value={answers[question.id]} disabled={!editable}
          onChange={value => { setSaved(''); setAnswers(current => ({ ...current, [question.id]: value })); }} />)}
      </fieldset>)}
      {(save.isError || submit.isError) && <Alert error={save.error ?? submit.error} />}
      {saved && <p className="portal-ok" role="status">{saved}</p>}
      {editable && <div className="portal-actions">
        <button className="portal-primary" disabled={submit.isPending}>{submit.isPending ? 'Se trimite…' : 'Trimite formularul'}</button>
        <button type="button" className="portal-secondary" disabled={save.isPending} onClick={() => { setSaved(''); save.mutate(); }}>Salvează și continuă mai târziu</button>
      </div>}
    </form>
  </section>;
}
