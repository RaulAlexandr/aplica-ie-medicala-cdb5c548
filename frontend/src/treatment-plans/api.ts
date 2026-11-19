import { api } from '../api/client';

export type PlanStatus = 'draft' | 'accepted' | 'in_progress' | 'completed' | 'cancelled';
export type Priority = 'urgent' | 'high' | 'normal' | 'low';
export type ItemProgress = 'planned' | 'scheduled' | 'in_progress' | 'done' | 'attention' | 'cancelled';

export type Progress = { total: number; done: number; cancelled: number; in_progress: number; scheduled: number; planned: number; attention: number; remaining: number; percent: number };
export type CurrencyTotal = { currency: string; estimated_total: string; completed_estimate: string; remaining_estimate: string; estimated_minutes: number };

export type PlanItem = {
  id: string; stage_id: string; position: number; procedure_catalog_id: string; procedure_code: string; procedure_name: string;
  currency: string; estimated_price: string; estimated_duration_minutes: number; tooth_fdi: string | null; tooth_surface: string | null;
  priority: Priority; notes: string | null; status: 'active' | 'cancelled'; progress: ItemProgress;
  cancel_reason: string | null; cancelled_at: string | null; cancelled_by: string | null;
  appointment: null | { id: string; starts_at: string; status: string; appointment_type: string; linked_by: string | null; linked_at: string | null };
  performed_procedure: null | { id: string; status: string; procedure_code: string; procedure_name: string; tooth_fdi: string | null; tooth_surface: string | null; appointment_id: string; linked_by: string | null; linked_at: string | null };
};

export type PlanStage = { id: string; position: number; title: string; notes: string | null; progress: Progress; items: PlanItem[] };
export type Acceptance = { id: string; version: number; accepted_by: string | null; accepted_by_id: string; accepted_at: string; note: string; is_current: boolean; snapshot: { version: number; title: string; totals: Array<{ currency: string; estimated_total: string; estimated_minutes: number }>; stages: Array<{ title: string; items: Array<{ procedure_code: string; procedure_name: string; tooth_fdi: string | null; estimated_price: string; currency: string }> }> } };
export type PlanEvent = { id: string; action: string; plan_version: number; item_id: string | null; actor: string | null; occurred_at: string; details: Record<string, unknown> };

export type TreatmentPlan = {
  id: string; clinic_id: string; patient_id: string; title: string; notes: string | null; status: PlanStatus;
  version: number; accepted_version: number | null; needs_acceptance: boolean;
  created_by: string | null; created_at: string; updated_at: string;
  completed_at: string | null; completed_by: string | null; cancelled_at: string | null; cancelled_by: string | null; cancel_reason: string | null;
  can_complete: boolean; completion_blocker: string | null; estimate_label: string;
  totals: CurrencyTotal[]; progress: Progress; stages: PlanStage[]; acceptances: Acceptance[]; history: PlanEvent[];
};
export type PlanSummary = Pick<TreatmentPlan, 'id' | 'patient_id' | 'title' | 'status' | 'version' | 'accepted_version' | 'needs_acceptance' | 'created_at' | 'updated_at' | 'totals' | 'progress'>;

export type ItemInput = {
  procedure_catalog_id: string; tooth_fdi?: string | null; tooth_surface?: string | null; priority?: Priority;
  estimated_price?: string; estimated_duration_minutes?: number; notes?: string | null;
};
export type ItemPatch = Partial<Omit<ItemInput, 'procedure_catalog_id'>> & { stage_id?: string };
export type PlanCreate = { title: string; notes?: string | null; stages: Array<{ title: string; notes?: string | null; items: ItemInput[] }> };

export type LinkCandidates = {
  appointments: Array<{ id: string; starts_at: string; status: string; appointment_type: string }>;
  performed_procedures: Array<{ id: string; appointment_id: string; procedure_catalog_id: string; procedure_code: string; procedure_name: string; tooth_fdi: string | null; tooth_surface: string | null; status: string; created_at: string; linked_item_id: string | null }>;
};

const base = '/treatment-plans';

export async function listPatientPlans(patientId: string) { return (await api.get<PlanSummary[]>(`${base}/patients/${patientId}`)).data; }
export async function getPlan(planId: string) { return (await api.get<TreatmentPlan>(`${base}/${planId}`)).data; }
export async function getLinkCandidates(patientId: string) { return (await api.get<LinkCandidates>(`${base}/patients/${patientId}/link-candidates`)).data; }
export async function createPlan(patientId: string, payload: PlanCreate, idempotencyKey: string) {
  return (await api.post<TreatmentPlan>(`${base}/patients/${patientId}`, payload, { headers: { 'Idempotency-Key': idempotencyKey } })).data;
}
export async function updatePlan(planId: string, payload: { title?: string; notes?: string | null }) { return (await api.patch<TreatmentPlan>(`${base}/${planId}`, payload)).data; }
export async function addStage(planId: string, title: string) { return (await api.post<TreatmentPlan>(`${base}/${planId}/stages`, { title })).data; }
export async function updateStage(planId: string, stageId: string, payload: { title?: string; move?: 'up' | 'down' }) { return (await api.patch<TreatmentPlan>(`${base}/${planId}/stages/${stageId}`, payload)).data; }
export async function deleteStage(planId: string, stageId: string) { return (await api.delete<TreatmentPlan>(`${base}/${planId}/stages/${stageId}`)).data; }
export async function addItem(planId: string, stageId: string, payload: ItemInput, idempotencyKey: string) {
  return (await api.post<TreatmentPlan>(`${base}/${planId}/stages/${stageId}/items`, payload, { headers: { 'Idempotency-Key': idempotencyKey } })).data;
}
export async function updateItem(itemId: string, payload: ItemPatch) { return (await api.patch<TreatmentPlan>(`${base}/items/${itemId}`, payload)).data; }
export async function deleteItem(itemId: string) { return (await api.delete<TreatmentPlan>(`${base}/items/${itemId}`)).data; }
export async function cancelItem(itemId: string, reason: string) { return (await api.post<TreatmentPlan>(`${base}/items/${itemId}/cancel`, { reason })).data; }
export async function linkAppointment(itemId: string, appointmentId: string | null) { return (await api.put<TreatmentPlan>(`${base}/items/${itemId}/appointment`, { appointment_id: appointmentId })).data; }
export async function linkPerformed(itemId: string, performedId: string) { return (await api.put<TreatmentPlan>(`${base}/items/${itemId}/performed-procedure`, { performed_procedure_id: performedId })).data; }
export async function unlinkPerformed(itemId: string, reason: string) { return (await api.delete<TreatmentPlan>(`${base}/items/${itemId}/performed-procedure`, { data: { reason } })).data; }
export async function acceptPlan(planId: string, note: string, expectedVersion: number) { return (await api.post<TreatmentPlan>(`${base}/${planId}/accept`, { note, expected_version: expectedVersion })).data; }
export async function completePlan(planId: string) { return (await api.post<TreatmentPlan>(`${base}/${planId}/complete`)).data; }
export async function cancelPlan(planId: string, reason: string) { return (await api.post<TreatmentPlan>(`${base}/${planId}/cancel`, { reason })).data; }
