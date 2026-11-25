import axios, { AxiosError, InternalAxiosRequestConfig } from 'axios';

// The portal keeps its own tokens under its own keys and never sends the staff token.
const ACCESS = 'portal_access_token';
const REFRESH = 'portal_refresh_token';
const CLINIC = 'portal_clinic';

export type PortalTokens = { access_token: string; refresh_token: string };
export type PortalClinic = { id: string; name: string };
export type PortalMe = { account: { full_name: string; email: string }; clinic: { id: string; name: string; timezone: string }; patients: Array<{ id: string; name: string; relationship: string }> };
export type PortalAppointment = { id: string; starts_at: string; local_date: string; local_time: string; duration_minutes: number; doctor_name: string; clinic_name: string; status: string; status_label: string; confirmed_by_patient_at: string | null; reschedule_request: { status: string; created_at: string } | null; can_confirm: boolean; can_request_reschedule: boolean };
export type PortalDocument = { id: string; filename: string; content_type: string; size_bytes: number; category: string; published_at: string };
export type PortalPlan = { id: string; published_at: string; title: string; version: number; stages: Array<{ position: number; title: string; items: Array<{ procedure_name: string; tooth_fdi: string | null; tooth_surface: string | null; estimated_price: string; currency: string; estimated_duration_minutes: number }> }>; totals: Array<{ currency: string; estimated_total: string; estimated_minutes: number }> };
export type PortalQuestion = { id: string; label: string; type: 'short_text' | 'long_text' | 'yes_no' | 'single_choice' | 'multiple_choice' | 'date' | 'number'; required: boolean; options?: Array<{ value: string; label: string }>; condition?: { question_id: string; operator: 'equals'; value: string } };
export type PortalForm = { id: string; name: string; status: string; status_label: string; editable: boolean; submitted_at: string | null; definition?: { sections: Array<{ id: string; title: string; questions: PortalQuestion[] }> }; answers?: Record<string, unknown> };

type Retry = InternalAxiosRequestConfig & { _retry?: boolean };

export const portalApi = axios.create({ baseURL: import.meta.env.VITE_API_URL ?? 'http://localhost:8000/api' });

function read(key: string): string | null { try { return localStorage.getItem(key); } catch { return null; } }
function write(key: string, value: string | null) { try { if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value); } catch { /* storage unavailable */ } }

export function hasPortalSession() { return Boolean(read(ACCESS)); }
export function rememberedClinic(): PortalClinic | null { try { return JSON.parse(read(CLINIC) ?? 'null'); } catch { return null; } }
export function rememberClinic(clinic: PortalClinic) { write(CLINIC, JSON.stringify(clinic)); }
function saveTokens(tokens: PortalTokens) { write(ACCESS, tokens.access_token); write(REFRESH, tokens.refresh_token); }
export function clearPortalSession() {
  write(ACCESS, null); write(REFRESH, null);
  window.dispatchEvent(new CustomEvent('dentacare:portal-session-cleared'));
}

portalApi.interceptors.request.use(config => {
  const token = read(ACCESS);
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

let refreshing: Promise<string> | null = null;
portalApi.interceptors.response.use(response => response, async (error: AxiosError) => {
  const original = error.config as Retry | undefined;
  const url = original?.url ?? '';
  if (!original || error.response?.status !== 401 || original._retry || url.includes('/portal/auth/')) throw error;
  const refresh = read(REFRESH);
  if (!refresh) { clearPortalSession(); throw error; }
  original._retry = true;
  refreshing ??= axios.post<PortalTokens>(`${portalApi.defaults.baseURL}/portal/auth/refresh`, { refresh_token: refresh })
    .then(({ data }) => { saveTokens(data); return data.access_token; })
    .catch(refreshError => { clearPortalSession(); throw refreshError; })
    .finally(() => { refreshing = null; });
  const token = await refreshing;
  original.headers.Authorization = `Bearer ${token}`;
  return portalApi(original);
});

export async function clinicIdentity(id: string) { return (await portalApi.get<PortalClinic>(`/portal/clinics/${id}`)).data; }
export async function portalLogin(clinicId: string, email: string, password: string) {
  const { data } = await portalApi.post<PortalTokens>('/portal/auth/login', { clinic_id: clinicId, email, password });
  saveTokens(data);
}
export async function portalLogout() {
  try { await portalApi.post('/portal/auth/logout'); } catch { /* the local session is cleared anyway */ }
  clearPortalSession();
}
export async function invitationInfo(token: string) { return (await portalApi.post<{ clinic_id: string; clinic_name: string; full_name: string; email: string }>('/portal/auth/invitation', { token })).data; }
export async function acceptPortalInvitation(token: string, password: string) { return (await portalApi.post<{ clinic_id: string; clinic_name: string; email: string }>('/portal/auth/accept', { token, password })).data; }
export async function portalMe() { return (await portalApi.get<PortalMe>('/portal/me')).data; }
export async function portalAppointments(patientId: string) { return (await portalApi.get<{ upcoming: PortalAppointment[]; past: PortalAppointment[] }>(`/portal/patients/${patientId}/appointments`)).data; }
export async function confirmAppointment(patientId: string, appointmentId: string) { return (await portalApi.post(`/portal/patients/${patientId}/appointments/${appointmentId}/confirm`)).data; }
export async function requestReschedule(patientId: string, appointmentId: string, message: string, clientRequestId: string) {
  return (await portalApi.post(`/portal/patients/${patientId}/appointments/${appointmentId}/reschedule-requests`, { message, client_request_id: clientRequestId })).data;
}
export async function portalDocuments(patientId: string) { return (await portalApi.get<PortalDocument[]>(`/portal/patients/${patientId}/documents`)).data; }
export async function downloadPortalDocument(patientId: string, publicationId: string) {
  return (await portalApi.get<Blob>(`/portal/patients/${patientId}/documents/${publicationId}/content`, { responseType: 'blob' })).data;
}
export async function portalPlans(patientId: string) { return (await portalApi.get<PortalPlan[]>(`/portal/patients/${patientId}/plans`)).data; }
export async function portalForms(patientId: string) { return (await portalApi.get<PortalForm[]>(`/portal/patients/${patientId}/forms`)).data; }
export async function portalForm(patientId: string, formId: string) { return (await portalApi.get<PortalForm>(`/portal/patients/${patientId}/forms/${formId}`)).data; }
export async function savePortalAnswers(patientId: string, formId: string, answers: Record<string, unknown>) { return (await portalApi.put<PortalForm>(`/portal/patients/${patientId}/forms/${formId}/answers`, { answers })).data; }
export async function submitPortalForm(patientId: string, formId: string, answers: Record<string, unknown>) { return (await portalApi.post<PortalForm>(`/portal/patients/${patientId}/forms/${formId}/submit`, { answers })).data; }

const GENERIC = 'A apărut o eroare. Încearcă din nou.';
export function portalError(error: unknown): string {
  if (error instanceof AxiosError) {
    const detail = error.response?.data?.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) return 'Verifică datele introduse.';
    if (!error.response) return 'Nu s-a putut contacta clinica. Verifică conexiunea și încearcă din nou.';
  }
  return GENERIC;
}
