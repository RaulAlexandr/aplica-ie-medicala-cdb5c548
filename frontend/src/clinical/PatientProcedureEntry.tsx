import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { apiErrorMessage, getAppointment, listAppointments, listProcedures, type Appointment } from '../api/client';
import type { ToothId } from '../odontogram/types';
import { ProcedureForm } from './ProcedureForm';
import type { PerformedProcedure, VisitSummary } from './api';

const closedStatuses = new Set(['completed', 'cancelled', 'no_show']);
const appointmentKey = (appointmentId: string) => ['appointment', appointmentId] as const;

function visitLabel(appointment: Appointment) {
  const date = new Intl.DateTimeFormat('ro-RO', { dateStyle: 'medium', timeStyle: 'short', timeZone: appointment.timezone }).format(new Date(appointment.local_start));
  return `${date} · ${appointment.appointment_type} · ${appointment.status.replace('_', ' ')}`;
}

export function PatientProcedureEntry({ patientId, patientName, appointmentSearchTerm, appointmentId, initialTooth, onCancel, onSaved }: {
  patientId: string;
  patientName: string;
  appointmentSearchTerm: string;
  appointmentId?: string;
  initialTooth: ToothId;
  onCancel: () => void;
  onSaved: (procedure: PerformedProcedure) => void | Promise<void>;
}) {
  const client = useQueryClient();
  const [selectedAppointmentId, setSelectedAppointmentId] = useState(appointmentId ?? '');
  const selectedAppointment = useQuery({
    queryKey: appointmentKey(selectedAppointmentId),
    queryFn: () => getAppointment(selectedAppointmentId),
    enabled: Boolean(selectedAppointmentId),
  });
  const isSelectedAppointmentInvalid = Boolean(selectedAppointmentId && selectedAppointment.isSuccess && (
    selectedAppointment.data.patient_id !== patientId || closedStatuses.has(selectedAppointment.data.status)
  ));
  const eligibleVisits = useQuery({
    queryKey: ['patient-procedure-entry-visits', patientId, appointmentSearchTerm],
    queryFn: async () => (await listAppointments({ patient_search: appointmentSearchTerm }))
      .filter(appointment => appointment.patient_id === patientId && !closedStatuses.has(appointment.status)),
    enabled: Boolean(appointmentSearchTerm) && (!selectedAppointmentId || selectedAppointment.isError || isSelectedAppointmentInvalid),
  });
  const validAppointment = selectedAppointment.isSuccess && !selectedAppointment.isFetching && selectedAppointment.data.patient_id === patientId && !closedStatuses.has(selectedAppointment.data.status)
    ? selectedAppointment.data : undefined;
  const catalog = useQuery({
    queryKey: ['visit-procedure-catalog'],
    queryFn: () => listProcedures(undefined, true, 100, 0),
    enabled: Boolean(validAppointment),
  });

  async function saved(procedure: PerformedProcedure) {
    const key = ['visit-summary', procedure.appointment_id] as const;
    client.setQueryData<VisitSummary>(key, current => {
      if (!current || current.performed_procedures.some(item => item.id === procedure.id)) return current;
      const unresolved = current.unresolved_procedures.some(item => item.id === procedure.id)
        ? current.unresolved_procedures
        : [...current.unresolved_procedures, { id: procedure.id, procedure_name: procedure.procedure_name, status: procedure.status, executed_by: procedure.executed_by_id }];
      return {
        ...current,
        performed_procedures: [...current.performed_procedures, procedure],
        unresolved_procedures: unresolved,
        can_close_visit: false,
      };
    });
    await Promise.all([
      client.invalidateQueries({ queryKey: key }),
      client.invalidateQueries({ queryKey: ['performed-procedures'] }),
      client.invalidateQueries({ queryKey: ['patient-record', patientId] }),
      client.invalidateQueries({ queryKey: ['patient-timeline', patientId] }),
    ]);
    await onSaved(procedure);
  }

  const appointmentChoice = <>
    {selectedAppointment.isError && <p className="error" role="alert">The selected visit could not be verified: {apiErrorMessage(selectedAppointment.error)}</p>}
    {selectedAppointment.data?.patient_id !== undefined && selectedAppointment.data.patient_id !== patientId && <p className="error" role="alert">The selected visit belongs to another patient. Choose a visit for {patientName}.</p>}
    {selectedAppointment.data && selectedAppointment.data.patient_id === patientId && closedStatuses.has(selectedAppointment.data.status) && <p className="error" role="alert">This visit is {selectedAppointment.data.status.replace('_', ' ')} and cannot receive a new procedure. Choose another visit.</p>}
    <label>Select an eligible visit<select aria-label="Procedure visit" value={selectedAppointmentId} onChange={event => setSelectedAppointmentId(event.target.value)}>
      <option value="">Choose a visit</option>
      {eligibleVisits.data?.map(visit => <option key={visit.id} value={visit.id}>{visitLabel(visit)}</option>)}
    </select></label>
    {eligibleVisits.isLoading && <p className="muted">Loading visits for {patientName}…</p>}
    {eligibleVisits.isError && <p className="error" role="alert">Visits could not be loaded: {apiErrorMessage(eligibleVisits.error)}</p>}
    {!eligibleVisits.isLoading && !eligibleVisits.isError && eligibleVisits.data?.length === 0 && <p className="muted">No eligible visit exists for this patient. Select or create a visit from the calendar first.</p>}
    <Link className="button secondary" to="/appointments">Open calendar</Link>
  </>;

  return <section className="card clinical-record-section t07-entry-panel" aria-label="Add procedure for selected tooth">
    <div className="detail-heading"><div><p className="eyebrow">T04 PERFORMED PROCEDURE</p><h2>Manoperă pentru dintele FDI {initialTooth}</h2></div>
      <button type="button" className="secondary" onClick={onCancel}>Cancel</button>
    </div>
    {(selectedAppointment.isLoading || selectedAppointment.isFetching) && selectedAppointmentId && <p className="muted">Verifying the selected visit…</p>}
    {!selectedAppointmentId && appointmentChoice}
    {selectedAppointmentId && !selectedAppointment.isFetching && !validAppointment && appointmentChoice}
    {validAppointment && <>
      <p className="t07-verified-visit"><strong>Verified patient:</strong> {patientName} · <strong>Visit:</strong> {visitLabel(validAppointment)}</p>
      <button type="button" className="secondary t07-change-visit" onClick={() => setSelectedAppointmentId('')}>Choose another visit</button>
      {catalog.isLoading && <p className="muted">Loading active procedure catalog…</p>}
      {catalog.isError && <p className="error" role="alert">Procedure catalog could not be loaded: {apiErrorMessage(catalog.error)}</p>}
      {catalog.data && <ProcedureForm
        key={`${patientId}:${validAppointment.id}:${initialTooth}`}
        appointment={validAppointment}
        patientName={patientName}
        catalog={catalog.data}
        initialTooth={initialTooth}
        onSaved={saved}
        onCancel={onCancel}
      />}
    </>}
  </section>;
}
