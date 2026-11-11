import { Link, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiErrorMessage, getAppointment, listProcedures, updateAppointmentStatus, type Appointment, type User } from '../api/client';
import { ProcedureCard } from './ProcedureCard';
import { getVisitSummary, type PerformedProcedure, type VisitSummary } from './api';
import { ProcedureForm } from './ProcedureForm';

const clinicalRoles = new Set(['doctor', 'clinic_manager', 'administrator']);
const appointmentManagerRoles = new Set(['clinic_manager', 'doctor', 'reception', 'administrator']);
const closedStatuses = new Set(['completed', 'cancelled', 'no_show']);
const nextStatus: Record<string, string | undefined> = {
  scheduled: 'confirmed',
  confirmed: 'arrived',
  arrived: 'in_progress',
};

function AppointmentStatus({ appointment, canManage, canCloseVisit, isSummaryLoading, hasSummary, onChanged }: {
  appointment: Appointment;
  canManage: boolean;
  canCloseVisit: boolean;
  isSummaryLoading: boolean;
  hasSummary: boolean;
  onChanged: () => Promise<void>;
}) {
  const statusMutation = useMutation({
    mutationFn: (status: string) => updateAppointmentStatus(appointment.id, status),
    onSuccess: onChanged,
  });
  const next = nextStatus[appointment.status];
  const canClose = canManage && appointment.status === 'in_progress' && canCloseVisit;
  const blockedByProcedures = appointment.status === 'in_progress' && hasSummary && !canCloseVisit;
  return <section className="card visit-status-panel">
    <div className="detail-heading"><div><p className="eyebrow">VISIT STATUS</p><h2>{appointment.status.replace('_', ' ')}</h2></div><span className="clinical-status">{appointment.status.replace('_', ' ')}</span></div>
    {canManage && next && <div className="clinical-actions"><button disabled={statusMutation.isPending} onClick={() => statusMutation.mutate(next)}>{statusMutation.isPending ? 'Saving…' : `Mark ${next.replace('_', ' ')}`}</button><p className="muted">Appointment status advances one allowed step at a time.</p></div>}
    {canManage && appointment.status === 'in_progress' && <div className="visit-close-action">
      <button disabled={!canClose || statusMutation.isPending || isSummaryLoading} onClick={() => statusMutation.mutate('completed')}>{statusMutation.isPending ? 'Closing…' : 'Close visit'}</button>
      {isSummaryLoading && <p className="muted">Checking unresolved procedures…</p>}
      {!isSummaryLoading && !hasSummary && <p className="error" role="status">Visit readiness is unavailable. Reload the summary before closing.</p>}
      {blockedByProcedures && <p className="error" role="status">Resolve every pending, running or paused procedure before closing this visit.</p>}
      {canClose && <p className="muted">All procedures are resolved. Closing the appointment is a separate action.</p>}
    </div>}
    {statusMutation.isError && <p className="error" role="alert">{apiErrorMessage(statusMutation.error)}</p>}
  </section>;
}

function VisitWorkspace({ appointmentId, user }: { appointmentId: string; user: User }) {
  const client = useQueryClient();
  const canManageProcedures = clinicalRoles.has(user.role);
  const appointmentQuery = useQuery({ queryKey: ['appointment', appointmentId], queryFn: () => getAppointment(appointmentId) });
  const summaryQuery = useQuery({
    queryKey: ['visit-summary', appointmentId],
    queryFn: () => getVisitSummary(appointmentId),
    enabled: appointmentQuery.isSuccess && (canManageProcedures || appointmentManagerRoles.has(user.role)),
  });
  const catalogQuery = useQuery({
    queryKey: ['visit-procedure-catalog'],
    queryFn: () => listProcedures(undefined, true, 100, 0),
    enabled: canManageProcedures && appointmentQuery.isSuccess && !closedStatuses.has(appointmentQuery.data.status),
  });
  const refreshVisit = async () => {
    await Promise.all([
      appointmentQuery.refetch(),
      summaryQuery.refetch(),
      client.invalidateQueries({ queryKey: ['performed-procedures'] }),
      client.invalidateQueries({ queryKey: ['patient-record'] }),
      client.invalidateQueries({ queryKey: ['patient-notes'] }),
      client.invalidateQueries({ queryKey: ['patient-timeline'] }),
    ]);
  };
  const procedureSaved = async (procedure: PerformedProcedure) => {
    client.setQueryData<VisitSummary>(['visit-summary', appointmentId], current => {
      if (!current || current.performed_procedures.some(item => item.id === procedure.id)) return current;
      const unresolved = current.unresolved_procedures.some(item => item.id === procedure.id)
        ? current.unresolved_procedures
        : [...current.unresolved_procedures, { id: procedure.id, procedure_name: procedure.procedure_name, status: procedure.status, executed_by: procedure.executed_by_id }];
      return { ...current, performed_procedures: [...current.performed_procedures, procedure], unresolved_procedures: unresolved, can_close_visit: false };
    });
    await refreshVisit();
  };
  if (appointmentQuery.isLoading) return <p className="muted">Loading appointment…</p>;
  if (appointmentQuery.isError || !appointmentQuery.data) return <p className="error" role="alert">{apiErrorMessage(appointmentQuery.error)}</p>;
  const appointment = appointmentQuery.data;
  const procedures = summaryQuery.data?.performed_procedures ?? [];
  const isVisitClosed = closedStatuses.has(appointment.status);

  return <>
    <Link to="/appointments">← Calendar</Link>
    <header className="clinical-page-header"><div><p className="eyebrow">TREATMENT VISIT</p><h1>{summaryQuery.data?.patient_name ?? appointment.patient_name ?? 'Patient'}</h1><p className="muted">{new Date(appointment.local_start).toLocaleString('ro-RO', { timeZone: appointment.timezone })} · {appointment.appointment_type}</p></div><div className="clinical-actions"><Link className="button secondary" to={`/patients/${appointment.patient_id}?appointment_id=${appointment.id}`}>Patient record</Link></div></header>
    <div className="visit-layout">
      <section>
        <AppointmentStatus appointment={appointment} canManage={appointmentManagerRoles.has(user.role)} canCloseVisit={summaryQuery.data?.can_close_visit ?? false} isSummaryLoading={summaryQuery.isFetching} hasSummary={Boolean(summaryQuery.data)} onChanged={refreshVisit} />
        <section className="card">
          <div className="detail-heading"><div><p className="eyebrow">PROCEDURES</p><h2>Performed procedures</h2></div><span className="muted">{procedures.length} saved</span></div>
          {summaryQuery.isLoading && <p className="muted">Loading visit summary…</p>}
          {summaryQuery.isError && <p className="error" role="alert">{apiErrorMessage(summaryQuery.error)}</p>}
          {canManageProcedures && !summaryQuery.isLoading && !summaryQuery.isError && procedures.length === 0 && <p className="muted">No procedures recorded for this visit.</p>}
          {canManageProcedures && <div className="clinical-list">{procedures.map(procedure => <ProcedureCard key={procedure.id} procedure={procedure} user={user} timezone={appointment.timezone} isVisitClosed={isVisitClosed} onChanged={refreshVisit} />)}</div>}
          {!canManageProcedures && <p className="muted">Procedure and clinical note details are available to clinical roles.</p>}
        </section>
        {canManageProcedures && !isVisitClosed && catalogQuery.data && <ProcedureForm
          key={appointmentId}
          appointment={appointment}
          patientName={appointment.patient_name ?? 'Patient'}
          catalog={catalogQuery.data}
          onSaved={procedureSaved}
        />}
        {canManageProcedures && catalogQuery.isLoading && <p className="muted">Loading procedure catalog…</p>}
        {canManageProcedures && catalogQuery.isError && <p className="error" role="alert">{apiErrorMessage(catalogQuery.error)}</p>}
      </section>
      <section className="visit-summary card">
        <p className="eyebrow">APPOINTMENT</p>
        <h2>Visit summary</h2>
        <p><strong>Date:</strong> {new Date(appointment.local_start).toLocaleString('ro-RO', { timeZone: appointment.timezone })}</p>
        <p><strong>Doctor:</strong> {appointment.doctor_name ?? '—'}</p>
        <p><strong>Room:</strong> {appointment.room_name ?? '—'}</p>
        <p><strong>Status:</strong> {appointment.status.replace('_', ' ')}</p>
        <p><strong>Time zone:</strong> {appointment.timezone}</p>
        {canManageProcedures && summaryQuery.data?.unresolved_procedures.length ? <div className="notice-text"><strong>Needs resolution:</strong><ul>{summaryQuery.data.unresolved_procedures.map(item => <li key={item.id}>{item.procedure_name} · {item.status}</li>)}</ul></div> : null}
        {summaryQuery.isError && <button className="secondary" onClick={() => void summaryQuery.refetch()}>Retry summary</button>}
      </section>
    </div>
  </>;
}

export function VisitPage({ user }: { user: User }) {
  const { appointmentId = '' } = useParams();
  if (!appointmentId) return <p className="error" role="alert">Appointment ID is missing.</p>;
  return <VisitWorkspace key={appointmentId} appointmentId={appointmentId} user={user} />;
}
