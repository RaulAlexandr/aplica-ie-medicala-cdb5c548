import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiErrorMessage, getClinic } from '../api/client';
import { Channel, ConsentSource, channels, getConsents, patientMessages, recordConsent } from './api';
import { channelLabel, formatInZone, reasonLabel, sourceLabel, statusLabel, typeLabel } from './labels';
import './comms.css';

export function MessageHistory({ messages, timezone }: { messages: import('./api').Message[]; timezone: string }) {
  if (!messages.length) return <p className="muted">Niciun mesaj pentru acest pacient.</p>;
  return <ul className="comms-list">{messages.map(m => <li key={m.id} className={`comms-message status-${m.status}`}>
    <div className="comms-message-head">
      <strong>{typeLabel[m.message_type]} · {channelLabel[m.channel]}</strong>
      <span className={`comms-badge status-${m.status}`}>{statusLabel(m)}</span>
    </div>
    <p className="muted">Programare: {formatInZone(m.appointment_starts_at, timezone)} · planificat: {formatInZone(m.scheduled_for, timezone)}{m.attempts ? ` · încercări: ${m.attempts}` : ''}</p>
    {m.reason && <p className="comms-reason">Motiv: {reasonLabel(m.reason)}</p>}
    {m.last_error && m.status !== 'sent' && m.status !== 'delivered' && <p className="error">Ultima eroare: {m.last_error}</p>}
    {m.body && <p className="comms-body">{m.body}</p>}
    <details><summary>Tranziții ({m.events.length})</summary><ol className="comms-events">{m.events.map(ev => <li key={ev.id}>{formatInZone(ev.occurred_at, timezone)} · {ev.from_status ?? '—'} → {ev.to_status}{ev.reason ? ` (${reasonLabel(ev.reason)})` : ''}</li>)}</ol></details>
  </li>)}</ul>;
}

export function PatientCommsPanel({ patientId, canRecord }: { patientId: string; canRecord: boolean }) {
  const client = useQueryClient();
  const consents = useQuery({ queryKey: ['comms-consents', patientId], queryFn: () => getConsents(patientId) });
  const messages = useQuery({ queryKey: ['comms-messages', patientId], queryFn: () => patientMessages(patientId) });
  const clinic = useQuery({ queryKey: ['clinic'], queryFn: getClinic });
  const [source, setSource] = useState<ConsentSource>('in_person');
  const timezone = clinic.data?.timezone ?? 'Europe/Bucharest';
  const record = useMutation({
    mutationFn: (input: { channel: Channel; granted: boolean }) => recordConsent(patientId, { ...input, source }),
    onSuccess: () => { void client.invalidateQueries({ queryKey: ['comms-consents', patientId] }); void client.invalidateQueries({ queryKey: ['comms-messages', patientId] }); },
  });
  return <section className="card comms-panel" aria-label="Comunicare">
    <h2>Comunicare</h2>
    <h3>Preferințe și consimțământ</h3>
    {consents.isLoading && <p className="muted">Se încarcă…</p>}
    {consents.isError && <p className="error" role="alert">{apiErrorMessage(consents.error)}</p>}
    {consents.data && <div className="comms-channels">{channels.map(channel => {
      const current = consents.data.current[channel];
      return <article key={channel} className="comms-channel">
        <h4>{channelLabel[channel]}</h4>
        <p className={current?.granted ? 'comms-yes' : 'comms-no'}>{current ? (current.granted ? 'Acord da' : 'Dezabonat') : 'Fără acord înregistrat'}</p>
        {current && <p className="muted">{sourceLabel[current.source]} · {formatInZone(current.recorded_at, timezone)} · {current.recorded_by ?? '—'}</p>}
        {!current?.granted && <p className="muted">Nu se trimite nimic pe acest canal.</p>}
        {canRecord && <button type="button" className="secondary" disabled={record.isPending} onClick={() => record.mutate({ channel, granted: !current?.granted })}>{current?.granted ? `Înregistrează dezabonare ${channelLabel[channel]}` : `Înregistrează acord ${channelLabel[channel]}`}</button>}
      </article>;
    })}</div>}
    {canRecord && <label className="comms-source">Sursa acordului
      <select value={source} onChange={e => setSource(e.target.value as ConsentSource)}>{Object.entries(sourceLabel).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
    </label>}
    {record.isError && <p className="error" role="alert">{apiErrorMessage(record.error)}</p>}
    {consents.data && consents.data.history.length > 0 && <details><summary>Istoric consimțământ ({consents.data.history.length})</summary><ul className="comms-events">{consents.data.history.map(c => <li key={c.id}>{formatInZone(c.recorded_at, timezone)} · {channelLabel[c.channel]} · {c.granted ? 'acord' : 'dezabonare'} · {sourceLabel[c.source]} · {c.recorded_by ?? '—'}</li>)}</ul></details>}
    <h3>Istoric mesaje</h3>
    {messages.isLoading && <p className="muted">Se încarcă…</p>}
    {messages.isError && <p className="error" role="alert">{apiErrorMessage(messages.error)}</p>}
    {messages.data && <MessageHistory messages={messages.data} timezone={timezone} />}
  </section>;
}
