import { api, type Patient } from '../api/client';

export type PerformedProcedure = {
  id: string;
  clinic_id: string;
  appointment_id: string;
  patient_id: string;
  procedure_catalog_id: string;
  executed_by_id: string;
  procedure_code: string;
  procedure_name: string;
  procedure_base_price: string;
  procedure_currency: string;
  procedure_default_duration: number;
  tooth_fdi: string | null;
  tooth_surface: string | null;
  status: 'pending' | 'running' | 'paused' | 'completed' | 'cancelled';
  current_price: string | null;
  total_duration_seconds: number | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
};

export type VisitSummary = {
  appointment_id: string;
  patient_id: string;
  patient_name: string;
  appointment_starts_at: string;
  appointment_status: string;
  performed_procedures: PerformedProcedure[];
  can_close_visit: boolean;
  unresolved_procedures: Array<{
    id: string;
    procedure_name: string;
    status: PerformedProcedure['status'];
    executed_by: string;
  }>;
};

export type ProcedureSegment = {
  id: string;
  performed_procedure_id: string;
  segment_type: 'run' | 'pause';
  started_at: string;
  ended_at: string | null;
  duration_seconds: number | null;
  created_at: string;
};

export type PerformedProcedureRevision = {
  id: string;
  clinic_id: string;
  performed_procedure_id: string;
  actor_id: string;
  field: string;
  previous_value: string | null;
  new_value: string | null;
  reason: string | null;
  occurred_at: string;
};

export type PerformedProcedureCreate = {
  appointment_id: string;
  procedure_catalog_id: string;
  tooth_fdi?: string;
  tooth_surface?: string;
  notes?: string;
};

export type ClinicalNote = {
  id: string;
  clinic_id: string;
  patient_id: string;
  appointment_id: string | null;
  author_id: string;
  author_name: string;
  note_type: string;
  title: string | null;
  content: string;
  tooth_fdi: string | null;
  tooth_surface: string | null;
  status: 'draft' | 'finalized';
  is_alert: boolean;
  created_at: string;
  updated_at: string;
  finalized_at: string | null;
};

export type ClinicalNoteCreate = {
  patient_id: string;
  appointment_id?: string;
  note_type: string;
  content: string;
  title?: string;
  tooth_fdi?: string;
  tooth_surface?: string;
  is_alert?: boolean;
};

export type ClinicalNoteUpdate = {
  title?: string | null;
  content?: string;
  tooth_fdi?: string | null;
  tooth_surface?: string | null;
  is_alert?: boolean;
};

export type ClinicalNoteRevision = {
  id: string;
  clinic_id: string;
  clinical_note_id: string;
  actor_id: string;
  actor_name: string;
  field: string;
  previous_value: string | null;
  new_value: string | null;
  reason: string | null;
  occurred_at: string;
};

export type PatientTimelineEvent = {
  id: string;
  clinic_id: string;
  patient_id: string;
  event_type: string;
  entity_type: string | null;
  entity_id: string | null;
  title: string;
  description: string | null;
  actor_id: string | null;
  actor_name: string | null;
  occurred_at: string;
  recorded_at: string;
  source: string | null;
  metadata: Record<string, unknown> | null;
};

export type PatientRevision = {
  id: string;
  actor_id: string;
  field: string;
  previous_value: string | null;
  new_value: string | null;
  occurred_at: string;
};

export type PatientRecord = {
  patient: Pick<Patient, 'id' | 'first_name' | 'last_name' | 'date_of_birth' | 'sex' | 'phone' | 'email' | 'allergies' | 'medications' | 'chronic_diseases' | 'pregnancy_status' | 'smoking_status' | 'medical_alerts' | 'relevant_medical_conditions'>;
  alerts: ClinicalNote[];
  clinical_notes: ClinicalNote[];
  performed_procedures: Array<{
    id: string;
    appointment_id: string;
    procedure_name: string;
    procedure_code: string;
    status: PerformedProcedure['status'];
    tooth_fdi: string | null;
    tooth_surface: string | null;
    created_at: string;
    updated_at: string;
  }>;
  timeline: PatientTimelineEvent[];
};

export type TimelineFilters = {
  event_type?: string;
  start_date?: string;
  end_date?: string;
  limit?: number;
  offset?: number;
};

export async function getVisitSummary(appointmentId: string) {
  return (await api.get<VisitSummary>(`/performed-procedures/visit/${appointmentId}/summary`)).data;
}

export async function listPerformedProcedures(filters: { appointment_id?: string; patient_id?: string; limit?: number; offset?: number }) {
  return (await api.get<PerformedProcedure[]>('/performed-procedures', { params: filters })).data;
}

export async function createPerformedProcedure(payload: PerformedProcedureCreate) {
  return (await api.post<PerformedProcedure>('/performed-procedures', payload)).data;
}

export async function startPerformedProcedure(id: string) {
  return (await api.post<PerformedProcedure>(`/performed-procedures/${id}/start`)).data;
}

export async function pausePerformedProcedure(id: string) {
  return (await api.post<PerformedProcedure>(`/performed-procedures/${id}/pause`)).data;
}

export async function resumePerformedProcedure(id: string) {
  return (await api.post<PerformedProcedure>(`/performed-procedures/${id}/resume`)).data;
}

export async function completePerformedProcedure(id: string) {
  return (await api.post<PerformedProcedure>(`/performed-procedures/${id}/complete`)).data;
}

export async function cancelPerformedProcedure(id: string, notes?: string) {
  return (await api.post<PerformedProcedure>(`/performed-procedures/${id}/cancel`, { transition: 'cancel', ...(notes ? { notes } : {}) })).data;
}

export async function updatePerformedProcedureNotes(id: string, notes: string) {
  return (await api.patch<PerformedProcedure>(`/performed-procedures/${id}/notes`, { notes })).data;
}

export async function getProcedureSegments(id: string) {
  return (await api.get<ProcedureSegment[]>(`/performed-procedures/${id}/segments`)).data;
}

export async function getPerformedProcedureRevisions(id: string) {
  return (await api.get<PerformedProcedureRevision[]>(`/performed-procedures/${id}/revisions`)).data;
}

export async function listPatientClinicalNotes(patientId: string, filters: { note_type?: string; status?: string; is_alert?: boolean; limit?: number; offset?: number } = {}) {
  return (await api.get<ClinicalNote[]>(`/clinical-notes/patients/${patientId}`, { params: filters })).data;
}

export async function getClinicalNote(id: string) {
  return (await api.get<ClinicalNote>(`/clinical-notes/${id}`)).data;
}

export async function createClinicalNote(payload: ClinicalNoteCreate) {
  return (await api.post<ClinicalNote>('/clinical-notes', payload)).data;
}

export async function updateClinicalNote(id: string, payload: ClinicalNoteUpdate) {
  return (await api.patch<ClinicalNote>(`/clinical-notes/${id}`, payload)).data;
}

export async function finalizeClinicalNote(id: string, versionNotes?: string) {
  return (await api.post<ClinicalNote>(`/clinical-notes/${id}/finalize`, versionNotes ? { version_notes: versionNotes } : {})).data;
}

export async function getClinicalNoteRevisions(id: string) {
  return (await api.get<ClinicalNoteRevision[]>(`/clinical-notes/${id}/revisions`)).data;
}

export async function getPatientRecord(patientId: string) {
  return (await api.get<PatientRecord>(`/clinical-notes/patients/${patientId}/record`)).data;
}

export async function listPatientTimeline(patientId: string, filters: TimelineFilters = {}) {
  return (await api.get<PatientTimelineEvent[]>(`/clinical-notes/patients/${patientId}/timeline`, { params: filters })).data;
}

export async function getPatientRevisions(patientId: string) {
  return (await api.get<PatientRevision[]>(`/patients/${patientId}/history`)).data;
}
