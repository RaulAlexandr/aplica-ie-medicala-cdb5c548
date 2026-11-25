import { FormEvent, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiErrorMessage } from '../api/client';
import { canSeeSterilization, formatDateTime, listPacks, traceAppointment, tracePatient, usePack, type TraceRow } from './api';
import './sterilization.css';

function TraceList({ rows, showVisit }: { rows: TraceRow[]; showVisit: boolean }) {
  return <div className="ster-trace">{rows.map(row => <article className="ster-item" key={row.event_id}>
    <strong>Pachet {row.pack_code} · {row.set_type}</strong>
    <span>Ciclul {row.cycle_number} · {row.equipment_name} · aprobat {formatDateTime(row.approved_at)}</span>
    {showVisit && <span>Programare {formatDateTime(row.appointment_starts_at)}</span>}
    {row.procedure_name && <span>Manoperă: {row.procedure_name}</span>}
    {row.compromised && <span className="error" role="status">Ciclu compromis: {row.recall_reason}</span>}
  </article>)}</div>;
}

/** Reverse traceability on the patient record. Renders nothing for roles without access (reception). */
export function SterilizationPatientTrace({ patientId, role }: { patientId: string; role: string }) {
  const allowed = canSeeSterilization(role);
  const query = useQuery({ queryKey: ['sterilization', 'trace-patient', patientId], queryFn: () => tracePatient(patientId), enabled: allowed, retry: false });
  if (!allowed || query.isError || !query.data) return null;
  return <section className="card ster-card" aria-label="Sterilizare și trasabilitate"><h2>Instrumente sterilizate folosite</h2>
    {query.data.length === 0 ? <p className="muted">Niciun pachet sterilizat legat de acest pacient.</p> : <TraceList rows={query.data} showVisit />}</section>;
}

/** Reverse traceability for one visit, plus linking a sterile pack to it. */
export function SterilizationVisitPanel({ appointmentId, role, procedures, closed }: { appointmentId: string; role: string; procedures: Array<{ id: string; procedure_name: string }>; closed: boolean }) {
  const allowed = canSeeSterilization(role);
  const client = useQueryClient();
  const trace = useQuery({ queryKey: ['sterilization', 'trace-appointment', appointmentId], queryFn: () => traceAppointment(appointmentId), enabled: allowed, retry: false });
  const sterile = useQuery({ queryKey: ['sterilization', 'packs'], queryFn: listPacks, enabled: allowed, retry: false });
  const [packId, setPackId] = useState('');
  const [procedureId, setProcedureId] = useState('');
  const link = useMutation({
    mutationFn: () => usePack(packId, { appointment_id: appointmentId, performed_procedure_id: procedureId || null }),
    onSuccess: () => { setPackId(''); setProcedureId(''); void client.invalidateQueries({ queryKey: ['sterilization'] }); },
  });
  if (!allowed || trace.isError || !trace.data) return null;
  const available = (sterile.data ?? []).filter(pack => pack.state === 'sterile');
  const submit = (event: FormEvent) => { event.preventDefault(); if (packId && !link.isPending) link.mutate(); };
  return <section className="card ster-card" aria-label="Sterilizare la această vizită"><p className="eyebrow">STERILIZARE</p><h2>Pachete folosite</h2>
    {trace.data.length === 0 ? <p className="muted">Niciun pachet legat de această vizită.</p> : <TraceList rows={trace.data} showVisit={false} />}
    {!closed && <form className="ster-form" aria-label="Leagă un pachet steril" onSubmit={submit}>
      <label>Pachet steril<select value={packId} onChange={event => setPackId(event.target.value)}><option value="">Alege pachetul</option>{available.map(pack => <option key={pack.id} value={pack.id}>{pack.code} · {pack.set_type_name}</option>)}</select></label>
      <label>Manoperă (opțional)<select value={procedureId} onChange={event => setProcedureId(event.target.value)}><option value="">Fără manoperă</option>{procedures.map(item => <option key={item.id} value={item.id}>{item.procedure_name}</option>)}</select></label>
      {link.isError && <p className="error wide" role="alert">{apiErrorMessage(link.error)}</p>}
      <div className="wide"><button type="submit" disabled={!packId || link.isPending}>{link.isPending ? 'Se leagă…' : 'Leagă pachetul'}</button></div>
    </form>}
  </section>;
}
