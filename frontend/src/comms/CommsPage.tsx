import { FormEvent, useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiErrorMessage, getClinic } from '../api/client';
import { Message, MessageStatus, MessageType, getCommSettings, listOutbox, listTemplates, messageTypes, processOutbox, reportDelivery, saveCommSettings, saveTemplate, statuses } from './api';
import { allowedVariables, channelLabel, formatInZone, reasonLabel, statusLabel, typeLabel, unknownVariables } from './labels';
import './comms.css';

function TemplateForm({ type, version, subject, body }: { type: MessageType; version: number; subject: string; body: string }) {
  const client = useQueryClient();
  const [form, setForm] = useState({ subject, body });
  const [problem, setProblem] = useState('');
  useEffect(() => setForm({ subject, body }), [subject, body]);
  const save = useMutation({ mutationFn: () => saveTemplate(type, form), onSuccess: () => { setProblem(''); void client.invalidateQueries({ queryKey: ['comms-templates'] }); } });
  function submit(event: FormEvent) {
    event.preventDefault();
    const bad = unknownVariables(form.subject, form.body);
    if (bad.length) { setProblem(`Variabile nepermise: ${bad.join(', ')}. Permise: ${allowedVariables.map(v => `{${v}}`).join(', ')}.`); return; }
    setProblem('');
    save.mutate();
  }
  return <form className="card comms-template" aria-label={`Șablon ${typeLabel[type]}`} onSubmit={submit}>
    <h3>{typeLabel[type]} <span className="muted">{version ? `versiunea ${version}` : 'șablon implicit'}</span></h3>
    <label>Subiect (email)<input value={form.subject} maxLength={200} onChange={e => setForm({ ...form, subject: e.target.value })} required /></label>
    <label>Text mesaj<textarea value={form.body} maxLength={1000} onChange={e => setForm({ ...form, body: e.target.value })} required /></label>
    {problem && <p className="error" role="alert">{problem}</p>}
    {save.isError && <p className="error" role="alert">{apiErrorMessage(save.error)}</p>}
    {save.isSuccess && !problem && <p className="notice-text" role="status">Salvat ca versiune nouă.</p>}
    <button disabled={save.isPending}>Salvează versiune nouă</button>
  </form>;
}

function Templates() {
  const templates = useQuery({ queryKey: ['comms-templates'], queryFn: listTemplates });
  return <div>
    <p className="muted">Variabile permise: {allowedVariables.map(v => <code key={v}>{`{${v}}`}</code>)}. Fără conținut clinic. Fiecare salvare creează o versiune nouă; mesajele deja trimise își păstrează textul.</p>
    {templates.isError && <p className="error" role="alert">{apiErrorMessage(templates.error)}</p>}
    <div className="comms-templates">{templates.data && messageTypes.map(type => { const t = templates.data.find(x => x.message_type === type); return t && <TemplateForm key={`${type}:${t.version}`} type={type} version={t.version} subject={t.subject} body={t.body} />; })}</div>
  </div>;
}

function Queue() {
  const client = useQueryClient();
  const [status, setStatus] = useState<MessageStatus | ''>('');
  const clinic = useQuery({ queryKey: ['clinic'], queryFn: getClinic });
  const outbox = useQuery({ queryKey: ['comms-outbox', status], queryFn: () => listOutbox(status) });
  const refresh = () => void client.invalidateQueries({ queryKey: ['comms-outbox'] });
  const process = useMutation({ mutationFn: processOutbox, onSuccess: refresh });
  const delivery = useMutation({ mutationFn: (v: { id: string; delivered: boolean }) => reportDelivery(v.id, v.delivered), onSuccess: refresh });
  const timezone = clinic.data?.timezone ?? 'Europe/Bucharest';
  const done = process.data;
  return <div>
    <div className="comms-toolbar">
      <label>Stare<select value={status} onChange={e => setStatus(e.target.value as MessageStatus | '')}><option value="">Toate</option>{statuses.map(s => <option key={s} value={s}>{statusLabel({ status: s, adapter: null })}</option>)}</select></label>
      <button type="button" disabled={process.isPending} onClick={() => process.mutate()}>Procesează mesajele scadente</button>
    </div>
    {done && <p className="notice-text" role="status">Procesare încheiată: {done.sent} trimise, {done.cancelled} anulate, {done.failed} eșuate, {done.retry} de reîncercat, {done.deferred} amânate.</p>}
    {process.isError && <p className="error" role="alert">{apiErrorMessage(process.error)}</p>}
    {delivery.isError && <p className="error" role="alert">{apiErrorMessage(delivery.error)}</p>}
    {outbox.isLoading && <p className="muted">Se încarcă…</p>}
    {outbox.isError && <p className="error" role="alert">{apiErrorMessage(outbox.error)}</p>}
    {outbox.data && !outbox.data.length && <p className="muted">Niciun mesaj în coadă pentru filtrul ales.</p>}
    <ul className="comms-list">{outbox.data?.map((m: Message) => <li key={m.id} className={`comms-message status-${m.status}`}>
      <div className="comms-message-head"><strong>{m.patient_name} · {typeLabel[m.message_type]} · {channelLabel[m.channel]}</strong><span className={`comms-badge status-${m.status}`}>{statusLabel(m)}</span></div>
      <p className="muted">Programare: {formatInZone(m.appointment_starts_at, timezone)} · planificat: {formatInZone(m.scheduled_for, timezone)}{m.attempts ? ` · încercări: ${m.attempts}` : ''}</p>
      {m.reason && <p className="comms-reason">Motiv: {reasonLabel(m.reason)}</p>}
      {m.last_error && <p className="error">Ultima eroare: {m.last_error}</p>}
      {m.body && <p className="comms-body">{m.body}</p>}
      {m.status === 'sent' && <div className="comms-actions"><button type="button" className="secondary" onClick={() => delivery.mutate({ id: m.id, delivered: true })}>Marchează livrat</button><button type="button" className="secondary" onClick={() => delivery.mutate({ id: m.id, delivered: false })}>Marchează eșuat</button></div>}
    </li>)}</ul>
  </div>;
}

function Settings() {
  const client = useQueryClient();
  const settings = useQuery({ queryKey: ['comms-settings'], queryFn: getCommSettings });
  const [form, setForm] = useState({ reminder_hours: 24, quiet_start: '20:00', quiet_end: '08:00', clinic_phone: '' });
  useEffect(() => { if (settings.data) setForm({ reminder_hours: settings.data.reminder_hours, quiet_start: settings.data.quiet_start.slice(0, 5), quiet_end: settings.data.quiet_end.slice(0, 5), clinic_phone: settings.data.clinic_phone ?? '' }); }, [settings.data]);
  const save = useMutation({ mutationFn: () => saveCommSettings({ reminder_hours: Number(form.reminder_hours), quiet_start: `${form.quiet_start}:00`, quiet_end: `${form.quiet_end}:00`, clinic_phone: form.clinic_phone.trim() || null }), onSuccess: () => void client.invalidateQueries({ queryKey: ['comms-settings'] }) });
  return <form className="card comms-settings" aria-label="Setări comunicare" onSubmit={e => { e.preventDefault(); save.mutate(); }}>
    <label>Reminder cu câte ore înainte<input type="number" min={1} max={336} value={form.reminder_hours} onChange={e => setForm({ ...form, reminder_hours: Number(e.target.value) })} required /></label>
    <label>Fereastră de liniște: de la<input type="time" value={form.quiet_start} onChange={e => setForm({ ...form, quiet_start: e.target.value })} required /></label>
    <label>Fereastră de liniște: până la<input type="time" value={form.quiet_end} onChange={e => setForm({ ...form, quiet_end: e.target.value })} required /></label>
    <label>Telefonul clinicii (pentru {'{telefon_clinica}'})<input value={form.clinic_phone} maxLength={40} onChange={e => setForm({ ...form, clinic_phone: e.target.value })} /></label>
    <p className="muted">Mesajele din fereastra de liniște se amână până la sfârșitul ei, nu se pierd. Orele sunt în fusul orar al clinicii.</p>
    {save.isError && <p className="error" role="alert">{apiErrorMessage(save.error)}</p>}
    {save.isSuccess && <p className="notice-text" role="status">Setări salvate.</p>}
    <button disabled={save.isPending}>Salvează setările</button>
  </form>;
}

export function CommsPage() {
  const [tab, setTab] = useState<'templates' | 'queue' | 'settings'>('templates');
  const tabs: Array<[typeof tab, string]> = [['templates', 'Șabloane'], ['queue', 'Coada de mesaje'], ['settings', 'Setări']];
  return <>
    <header><div><p className="eyebrow">COMUNICARE CU PACIENȚII</p><h1>Comunicare</h1></div></header>
    <p className="muted">Canal activ: simulat. Nu se trimite nimic în afara aplicației; textul final se păstrează în istoric.</p>
    <div className="comms-tabs" role="tablist">{tabs.map(([key, label]) => <button key={key} type="button" role="tab" aria-selected={tab === key} className={tab === key ? 'active' : 'secondary'} onClick={() => setTab(key)}>{label}</button>)}</div>
    {tab === 'templates' && <Templates />}
    {tab === 'queue' && <Queue />}
    {tab === 'settings' && <Settings />}
  </>;
}
