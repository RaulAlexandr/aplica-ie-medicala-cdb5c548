import { api } from '../api/client';

export type PortalAccountRow = { link_id: string; account_id: string; full_name: string; email: string; relationship: string; relationship_label: string; link_active: boolean; status: 'invited' | 'active' | 'revoked'; status_label: string; activated_at: string | null; invitation_expires_at: string | null };
export type PortalSubmission = { revision_number: number; submitted_at: string; completed_by: string; label: string; answers?: Record<string, unknown> };
export type PortalSection = {
  patient_id: string; login_path: string; accounts: PortalAccountRow[];
  documents: Array<{ id: string; document_id: string; filename: string; published_at: string; withdrawn_at: string | null; document_archived: boolean }> | null;
  plans: Array<{ id: string; plan_id: string; title: string; version: number; published_at: string; withdrawn_at: string | null }> | null;
  forms: Array<{ id: string; name: string; status: string; source: string; sent_at: string; submissions: PortalSubmission[] }>;
  can: { manage_access: boolean; publish: boolean; send_forms: boolean };
};
export type GrantResult = { account_id: string; link_id: string; status: string; invitation_url: string | null; invitation_expires_at: string | null };
export type RescheduleRequestRow = { id: string; status: 'open' | 'rescheduled' | 'rejected'; message: string; created_at: string; patient_id: string; patient_name: string; requested_by: string; appointment_id: string; appointment_starts_at: string; appointment_local: string; appointment_status: string; doctor_name: string; resolved_at: string | null; resolution_note: string | null };

const base = '/portal-staff';
export async function getPortalSection(patientId: string) { return (await api.get<PortalSection>(`${base}/patients/${patientId}`)).data; }
export async function grantPortalAccess(patientId: string, input: { email: string; full_name: string; relationship: string }) { return (await api.post<GrantResult>(`${base}/patients/${patientId}/access`, input)).data; }
export async function reissuePortalInvitation(accountId: string) { return (await api.post<{ invitation_url: string; invitation_expires_at: string }>(`${base}/accounts/${accountId}/invitation`)).data; }
export async function revokePortalAccount(accountId: string, reason: string) { return (await api.post(`${base}/accounts/${accountId}/revoke`, { reason })).data; }
export async function revokePortalLink(linkId: string) { return (await api.post(`${base}/links/${linkId}/revoke`)).data; }
export async function publishPortalDocument(patientId: string, documentId: string) { return (await api.post(`${base}/patients/${patientId}/documents`, { document_id: documentId })).data; }
export async function withdrawPortalDocument(publicationId: string) { return (await api.post(`${base}/document-publications/${publicationId}/withdraw`)).data; }
export async function publishPortalPlan(patientId: string, planId: string) { return (await api.post(`${base}/patients/${patientId}/plans`, { plan_id: planId })).data; }
export async function withdrawPortalPlan(publicationId: string) { return (await api.post(`${base}/plan-publications/${publicationId}/withdraw`)).data; }
export async function sendPortalForm(patientId: string, templateVersionId: string, clientRequestId: string) { return (await api.post(`${base}/patients/${patientId}/forms`, { template_version_id: templateVersionId, client_request_id: clientRequestId })).data; }
export async function listRescheduleRequests(status: 'open' | 'resolved' | 'all' = 'open') { return (await api.get<RescheduleRequestRow[]>(`${base}/reschedule-requests`, { params: { status } })).data; }
export async function resolveRescheduleRequest(id: string, resolution: 'rescheduled' | 'rejected', note?: string) { return (await api.post(`${base}/reschedule-requests/${id}/resolve`, { resolution, note: note || null })).data; }
