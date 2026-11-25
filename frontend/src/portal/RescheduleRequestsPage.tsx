import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiErrorMessage } from '../api/client';
import { listRescheduleRequests, resolveRescheduleRequest, RescheduleRequestRow } from './staffApi';
import './portal-staff.css';

const LABELS: Record<RescheduleRequestRow['status'], string> = { open: 'În așteptare', rescheduled: 'Reprogramată', rejected: 'Respinsă' };
const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString('ro-RO', { dateStyle: 'medium', timeStyle: 'short' }) : '');

export function RescheduleRequestsPage() {
  const [status, setStatus] = useState<'open' | 'resolved'>('open');
  const query = useQuery({ queryKey: ['portal-requests', status], queryFn: () => listRescheduleRequests(status), refetchInterval: 60_000 });
  return <>
    <header><div><p className="eyebrow">PORTAL PACIENT</p><h1>Cereri de reprogramare</h1></div></header>
    <section className="card pp-section">
      <p className="muted">Cererea nu mută programarea. Mută programarea din calendar, apoi marchează cererea ca rezolvată.</p>
      <div className="pp-actions pp-filter" role="group" aria-label="Filtru">
        <button type="button" className={status === 'open' ? '' : 'secondary'} aria-pressed={status === 'open'} onClick={() => setStatus('open')}>În așteptare</button>
        <button type="button" className={status === 'resolved' ? '' : 'secondary'} aria-pressed={status === 'resolved'} onClick={() => setStatus('resolved')}>Rezolvate</button>
      </div>
      {query.isLoading && <p className="muted">Se încarcă…</p>}
      {query.isError && <p className="error" role="alert">{apiErrorMessage(query.error)}</p>}
      {query.data?.length === 0 && <p className="muted">{status === 'open' ? 'Nicio cerere în așteptare.' : 'Nicio cerere rezolvată.'}</p>}
      <div className="pp-list">{query.data?.map(row => <RequestRow key={row.id} row={row} />)}</div>
    </section>
  </>;
}

function RequestRow({ row }: { row: RescheduleRequestRow }) {
  const client = useQueryClient();
  const [note, setNote] = useState('');
  const resolve = useMutation({
    mutationFn: (resolution: 'rescheduled' | 'rejected') => resolveRescheduleRequest(row.id, resolution, note.trim()),
    onSuccess: () => client.invalidateQueries({ queryKey: ['portal-requests'] }),
  });
  return <article className="pp-row pp-column" aria-label={`Cerere ${row.patient_name}`}>
    <div>
      <strong><Link className="clinical-link" to={`/patients/${row.patient_id}`}>{row.patient_name}</Link> · {row.appointment_local}</strong>
      <span>{row.doctor_name} · cerută de {row.requested_by} la {when(row.created_at)} · {LABELS[row.status]}</span>
      <p className="pp-message">„{row.message}”</p>
      {row.resolution_note && <span>Notă: {row.resolution_note}</span>}
    </div>
    {row.status === 'open' && <div className="pp-resolve">
      <label>Notă (opțional)<input maxLength={500} value={note} onChange={event => setNote(event.target.value)} /></label>
      <div className="pp-actions">
        <button type="button" disabled={resolve.isPending} onClick={() => resolve.mutate('rescheduled')}>Reprogramată</button>
        <button type="button" className="secondary" disabled={resolve.isPending} onClick={() => resolve.mutate('rejected')}>Respinsă</button>
      </div>
      {resolve.isError && <p className="error" role="alert">{apiErrorMessage(resolve.error)}</p>}
    </div>}
  </article>;
}
