import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { apiErrorMessage, listAccessLog } from '../api/client';

export function AccessLogPage() {
  const [patient, setPatient] = useState('');
  const [actor, setActor] = useState('');
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [offset, setOffset] = useState(0);
  const query = useQuery({ queryKey: ['patient-access-log', patient, actor, start, end, offset],
    queryFn: () => listAccessLog({ ...(patient ? { patient_id: patient } : {}), ...(actor ? { actor_id: actor } : {}),
      ...(start ? { start: new Date(start).toISOString() } : {}), ...(end ? { end: new Date(end).toISOString() } : {}), offset }) });
  return <section className="card" aria-label="Jurnal de acces">
    <header><div><p className="eyebrow">SECURITATE</p><h1>Jurnal de acces</h1></div></header>
    <div className="grid two">
      <label>Pacient (ID)<input value={patient} onChange={event => { setPatient(event.target.value); setOffset(0); }} /></label>
      <label>Utilizator (ID)<input value={actor} onChange={event => { setActor(event.target.value); setOffset(0); }} /></label>
      <label>De la<input type="datetime-local" value={start} onChange={event => { setStart(event.target.value); setOffset(0); }} /></label>
      <label>Până la<input type="datetime-local" value={end} onChange={event => { setEnd(event.target.value); setOffset(0); }} /></label>
    </div>
    {query.isLoading && <p className="muted">Se încarcă…</p>}
    {query.isError && <p className="error" role="alert">{apiErrorMessage(query.error)}</p>}
    {query.data?.items.length === 0 && <p className="muted">Nu există accesări pentru aceste filtre.</p>}
    {query.data && <><div className="list">{query.data.items.map(item => <article key={item.id}>
      <strong>{item.action}</strong><span>Pacient {item.patient_id} · utilizator {item.actor_id}</span>
      <time dateTime={item.occurred_at}>{new Date(item.occurred_at).toLocaleString('ro-RO')}</time>
    </article>)}</div><nav aria-label="Pagini jurnal"><button disabled={!offset} onClick={() => setOffset(value => Math.max(0, value - 50))}>Înapoi</button>
      <span>Înregistrări {offset + 1}–{offset + query.data.items.length}</span>
      <button disabled={!query.data.has_more} onClick={() => setOffset(value => value + 50)}>Înainte</button></nav></>}
  </section>;
}
