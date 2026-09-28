import axios, { AxiosError, InternalAxiosRequestConfig } from 'axios';

export type User = { id: string; clinic_id: string; full_name: string; email: string; role: string };
export type Doctor = { id: string; full_name: string; role: string };
export type Patient = { id: string; clinic_id: string; first_name: string; last_name: string; date_of_birth: string | null; sex: string | null; phone: string | null; email: string | null; address: string | null; emergency_contact: string | null; occupation: string | null; allergies: string | null; medications: string | null; chronic_diseases: string | null; pregnancy_status: string | null; smoking_status: string | null; previous_surgeries: string | null; relevant_medical_conditions: string | null; medical_alerts: string | null; notes: string | null; created_at: string; updated_at: string };
export type Room = { id: string; clinic_id: string; name: string; is_active: boolean };
export type Appointment = { id: string; clinic_id: string; patient_id: string; doctor_id: string; assistant_id: string | null; room_id: string; starts_at: string; ends_at: string; duration_minutes: number; appointment_type: string; status: string; notes: string | null };
export type AuthResponse = { access_token: string; refresh_token: string; token_type: string };

export const api = axios.create({ baseURL: import.meta.env.VITE_API_URL ?? 'http://localhost:8000/api' });
const clearSession = () => { localStorage.removeItem('access_token'); localStorage.removeItem('refresh_token'); };
const saveTokens = (data: AuthResponse) => { localStorage.setItem('access_token', data.access_token); localStorage.setItem('refresh_token', data.refresh_token); };

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token');
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

let refreshing: Promise<string> | null = null;
api.interceptors.response.use((response) => response, async (error: AxiosError) => {
  const original = error.config as (InternalAxiosRequestConfig & { _retry?: boolean }) | undefined;
  if (!original) throw error;
  if (error.response?.status !== 401 || original?._retry || original?.url?.includes('/auth/')) throw error;
  const refresh = localStorage.getItem('refresh_token');
  if (!refresh) { clearSession(); throw error; }
  original._retry = true;
  refreshing ??= axios.post<AuthResponse>(`${api.defaults.baseURL}/auth/refresh`, { refresh_token: refresh })
    .then(({ data }) => { saveTokens(data); return data.access_token; })
    .catch((refreshError) => { clearSession(); throw refreshError; })
    .finally(() => { refreshing = null; });
  try {
    original.headers.Authorization = `Bearer ${await refreshing}`;
    return api(original);
  } catch (refreshError) {
    clearSession();
    throw refreshError;
  }
});

export function apiErrorMessage(error: unknown): string {
  if (error instanceof AxiosError) {
    const detail = error.response?.data?.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) return detail.map((item) => item.msg).join(', ');
  }
  return error instanceof Error ? error.message : 'The server returned an error. Please try again.';
}

export async function login(email: string, password: string) { const { data } = await api.post<AuthResponse>('/auth/login', { email, password }); saveTokens(data); return data; }
export async function register(input: { clinic_name: string; full_name: string; email: string; password: string }) { const { data } = await api.post<AuthResponse>('/auth/register', input); saveTokens(data); return data; }
export async function logout(): Promise<void> {
  const token = localStorage.getItem('refresh_token');
  try { if (token) await api.post('/auth/logout', { refresh_token: token }); }
  finally { clearSession(); }
}
export async function me() { return (await api.get<User>('/auth/me')).data; }
export async function listDoctors() { return (await api.get<Doctor[]>('/staff/doctors')).data; }
export async function listPatients(search?: string, limit = 100, offset = 0) { return (await api.get<Patient[]>('/patients', { params: { ...(search ? { search } : {}), limit, offset } })).data; }
export async function countPatients() { return (await api.get<number>('/patients/count')).data; }
export async function getPatient(id: string) { return (await api.get<Patient>(`/patients/${id}`)).data; }
export async function createPatient(input: Partial<Patient> & Pick<Patient, 'first_name' | 'last_name'>) { return (await api.post<Patient>('/patients', input)).data; }
export async function updatePatient(id: string, input: Partial<Patient>) { return (await api.patch<Patient>(`/patients/${id}`, input)).data; }
export async function listRooms() { return (await api.get<Room[]>('/rooms')).data; }
export async function listAppointments() { return (await api.get<Appointment[]>('/appointments')).data; }
export async function createAppointment(input: Omit<Appointment, 'id' | 'clinic_id' | 'ends_at'>) { return (await api.post<Appointment>('/appointments', input)).data; }
