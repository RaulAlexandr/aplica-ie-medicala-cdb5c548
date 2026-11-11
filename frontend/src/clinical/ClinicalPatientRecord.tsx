import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { apiErrorMessage, getClinic, type User } from '../api/client';
import { clinicLocalToUtc } from '../timezone';
import { ClinicalNotesPanel } from './ClinicalNotesPanel';
import { PatientOdontogram } from '../odontogram/PatientOdontogram';
import { getPatientRecord, getPatientRevisions, listPatientTimeline, type PatientTimelineEvent } from './api';

const pageSize = 20;

function nextDate(value: string) {
  const day = new Date(`${value}T12:00:00Z`);
  day.setUTCDate(day.getUTCDate() + 1);
  return day.toISOString().slice(0, 10);
}

function timelineBounds(start: string, end: string, timezone: string) {
  return {
    start_date: start ? clinicLocalToUtc(`${start}T00:00`, timezone) : undefined,
    end_date: end ? new Date(new Date(clinicLocalToUtc(`${nextDate(end)}T00:00`, timezone)).getTime() - 1).toISOString() : undefined,
  };
}

function formatClinicDate(value: string | null | undefined, timezone: string) {
  if (!value) return 'Not recorded';
  return new Intl.DateTimeFormat('ro-RO', { dateStyle: 'medium', timeStyle: 'short', timeZone: timezone }).format(new Date(value));
}

function Field({ label, value }: { label: string; value: string | null | undefined }) {
  return <div className="clinical-field"><dt>{label}</dt><dd>{value?.trim() || 'Not recorded'}</dd></div>;
}

function TimelineSection({ patientId, timezone }: { patientId: string; timezone: string }) {
  const [eventType, setEventType] = useState('');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [page, setPage] = useState(0);
  let bounds = { start_date: undefined as string | undefined, end_date: undefined as string | undefined };
  let dateError = '';
  try {
    bounds = timelineBounds(startDate, endDate, timezone);
  } catch (error) {
    dateError = error instanceof Error ? error.message : 'The selected local time is invalid.';
  }
  const query = useQuery({
    queryKey: ['patient-timeline', patientId, eventType, bounds.start_date, bounds.end_date, page],
    queryFn: () => listPatientTimeline(patientId, { event_type: eventType || undefined, ...bounds, limit: pageSize, offset: page * pageSize }),
    enabled: !dateError && (!startDate || !endDate || startDate <= endDate),
  });
  const changeFilter = (update: () => void) => { update(); setPage(0); };
  return <section className="card clinical-record-section">
    <div className="detail-heading"><div><p className="eyebrow">PERSISTED SERVER EVENTS</p><h2>Timeline</h2></div></div>
    <div className="clinical-filters timeline-filters">
      <label>Event type<select value={eventType} onChange={event => changeFilter(() => setEventType(event.target.value))}><option value="">All events</option><option value="appointment">Appointment</option><option value="performed_procedure">Performed procedure</option><option value="clinical_note">Clinical note</option><option value="patient_revision">Patient revision</option><option value="alert">Alert</option></select></label>
      <label>From clinic date<input type="date" value={startDate} onChange={event => changeFilter(() => setStartDate(event.target.value))} /></label>
      <label>Through clinic date<input type="date" value={endDate} onChange={event => changeFilter(() => setEndDate(event.target.value))} /></label>
    </div>
    {dateError && <p className="error" role="alert">{dateError}</p>}
    {startDate && endDate && startDate > endDate && <p className="error" role="alert">The end date must be on or after the start date.</p>}
    {query.isLoading && <p className="muted">Loading timeline…</p>}
    {query.isError && <p className="error" role="alert">{apiErrorMessage(query.error)}</p>}
    {!dateError && !query.isLoading && !query.isError && query.data?.length === 0 && <p className="muted">No server timeline events match these filters.</p>}
    <div className="clinical-list">{query.data?.map(event => <TimelineItem key={event.id} event={event} timezone={timezone} />)}</div>
    <div className="pagination">
      <button className="secondary" disabled={page === 0} onClick={() => setPage(value => Math.max(0, value - 1))}>Previous</button>
      <span>Page {page + 1}</span>
      <button className="secondary" disabled={(query.data?.length ?? 0) < pageSize} onClick={() => setPage(value => value + 1)}>Next</button>
    </div>
  </section>;
}

function TimelineItem({ event, timezone }: { event: PatientTimelineEvent; timezone: string }) {
  return <article className="clinical-list-item">
    <div className="detail-heading"><div><p className="eyebrow">{event.event_type.replace('_', ' ')}</p><h3>{event.title}</h3></div></div>
    {event.description && <p className="clinical-note-text">{event.description}</p>}
    <p className="muted">Occurred {formatClinicDate(event.occurred_at, timezone)} · Recorded {formatClinicDate(event.recorded_at, timezone)}{event.actor_name ? ` · ${event.actor_name}` : ''}{event.source ? ` · ${event.source}` : ''}</p>
    {event.entity_type === 'appointment' && event.entity_id && <Link to={`/appointments/${event.entity_id}/visit`}>Open visit</Link>}
  </article>;
}

export function ClinicalPatientRecord({ patientId, appointmentId, user }: { patientId: string; appointmentId?: string; user: User }) {
  const clinic = useQuery({ queryKey: ['clinic'], queryFn: getClinic });
  const timezone = clinic.data?.timezone ?? 'UTC';
  const record = useQuery({ queryKey: ['patient-record', patientId], queryFn: () => getPatientRecord(patientId) });
  const revisions = useQuery({ queryKey: ['patient-revisions', patientId], queryFn: () => getPatientRevisions(patientId) });
  const [showPatientRevisions, setShowPatientRevisions] = useState(false);

  const patientFields = useMemo(() => record.data?.patient, [record.data?.patient]);
  return <div className="clinical-record">
    {record.isLoading && <section className="card"><p className="muted">Loading clinical record…</p></section>}
    {record.isError && <section className="card"><p className="error" role="alert">{apiErrorMessage(record.error)}</p><button className="secondary" onClick={() => void record.refetch()}>Retry clinical record</button></section>}
    {record.data && <>
      <section className="card clinical-record-section">
        <div className="detail-heading"><div><p className="eyebrow">AUTHORIZED CLINICAL VIEW</p><h2>Medical record</h2></div></div>
        <dl className="clinical-fields">
          <Field label="Date of birth" value={patientFields?.date_of_birth} />
          <Field label="Sex" value={patientFields?.sex} />
          <Field label="Allergies" value={patientFields?.allergies} />
          <Field label="Medication" value={patientFields?.medications} />
          <Field label="Chronic conditions" value={patientFields?.chronic_diseases} />
          <Field label="Pregnancy status" value={patientFields?.pregnancy_status} />
          <Field label="Smoking status" value={patientFields?.smoking_status} />
          <Field label="Relevant conditions" value={patientFields?.relevant_medical_conditions} />
          <Field label="Medical alerts" value={patientFields?.medical_alerts} />
        </dl>
        <button className="link clinical-link" onClick={() => setShowPatientRevisions(value => !value)}>{showPatientRevisions ? 'Hide patient revisions' : 'Read patient revisions'}</button>
        {showPatientRevisions && <section className="clinical-history" aria-label="Patient revisions">
          {revisions.isLoading && <p className="muted">Loading patient revisions…</p>}
          {revisions.isError && <p className="error" role="alert">{apiErrorMessage(revisions.error)}</p>}
          {revisions.data?.length === 0 && <p className="muted">No patient revisions recorded.</p>}
          {revisions.data?.map(revision => <p key={revision.id}><strong>{revision.field}</strong>: {revision.previous_value ?? '—'} → {revision.new_value ?? '—'} · {revision.actor_id} · {formatClinicDate(revision.occurred_at, timezone)}</p>)}
        </section>}
      </section>

      <section className="card clinical-record-section">
        <div className="detail-heading"><div><p className="eyebrow">PERSISTED ALERTS</p><h2>Clinical alerts</h2></div></div>
        {record.data.alerts.length === 0 && <p className="muted">No finalized clinical alerts.</p>}
        <div className="clinical-list">{record.data.alerts.map(alert => <article className="clinical-list-item alert-item" key={alert.id}><strong>{alert.title || alert.note_type.replace('_', ' ')}</strong><p className="clinical-note-text">{alert.content}</p><p className="muted">{alert.author_name} · {formatClinicDate(alert.created_at, timezone)}</p></article>)}</div>
      </section>

      <section className="card clinical-record-section">
        <div className="detail-heading"><div><p className="eyebrow">T04 VISITS</p><h2>Procedures and visits</h2></div></div>
        {record.data.performed_procedures.length === 0 && <p className="muted">No procedures recorded yet.</p>}
        <div className="clinical-list">{record.data.performed_procedures.map(procedure => <article className="clinical-list-item detail-heading" key={procedure.id}>
          <div><h3>{procedure.procedure_code} · {procedure.procedure_name}</h3><p className="muted">{procedure.status} · {procedure.tooth_fdi ? `FDI ${procedure.tooth_fdi}` : 'No tooth'}{procedure.tooth_surface ? ` · ${procedure.tooth_surface}` : ''} · {formatClinicDate(procedure.created_at, timezone)}</p></div>
          <Link className="button secondary" to={`/appointments/${procedure.appointment_id}/visit`}>Open visit</Link>
        </article>)}</div>
      </section>
    </>}
    <PatientOdontogram
      key={patientId}
      patientId={patientId}
      user={user}
      timezone={timezone}
      patientName={patientFields ? `${patientFields.first_name} ${patientFields.last_name}` : undefined}
      appointmentSearchTerm={patientFields ? (patientFields.phone || patientFields.email || patientFields.last_name) : undefined}
      appointmentId={appointmentId}
    />
    <ClinicalNotesPanel key={`${patientId}:${appointmentId ?? 'patient'}`} patientId={patientId} appointmentId={appointmentId} user={user} timezone={timezone} />
    <TimelineSection key={patientId} patientId={patientId} timezone={timezone} />
    {clinic.isError && <p className="error" role="alert">Clinic time zone could not be loaded: {apiErrorMessage(clinic.error)}</p>}
  </div>;
}
