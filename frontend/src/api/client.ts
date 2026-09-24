import axios from 'axios';

export type User = { id: string; clinic_id: string; full_name: string; email: string; role: string };
export type Patient = { id: string; clinic_id: string; first_name: string; last_name: string; date_of_birth: string | null; sex: string | null; phone: string | null; email: string | null; address: string | null; emergency_contact: string | null; occupation: string | null; allergies: string | null; medications: string | null; chronic_diseases: string | null; pregnancy_status: string | null; smoking_status: string | null; previous_surgeries: string | null; relevant_medical_conditions: string | null; medical_alerts: string | null; notes: string | null; created_at: string; updated_at: string };
export type Room = { id: string; clinic_id: string; name: string; is_active: boolean };
export type Appointment = { id: string; clinic_id: string; patient_id: string; doctor_id: string; assistant_id: string | null; room_id: string; starts_at: string; ends_at: string; duration_minutes: number; appointment_type: string; status: string; notes: string | null };
export type AuthResponse = { access_token: string; refresh_token: string; token_type: string };
export const api = axios.create({ baseURL: import.meta.env.VITE_API_URL ?? 'http://localhost:8000/api' });
const saveTokens = (data: AuthResponse) => { localStorage.setItem('access_token', data.access_token); localStorage.setItem('refresh_token', data.refresh_token); };
api.interceptors.request.use((config) => { const token = localStorage.getItem('access_token'); if (token) config.headers.Authorization = `Bearer ${token}`; return config; });
let refreshing: Promise<string> | null = null;
api.interceptors.response.use((response) => response, async (error) => {
  const original = error.config;
  if (error.response?.status !== 401 || original?._retry || original?.url?.includes('/auth/')) throw error;
  const refresh = localStorage.getItem('refresh_token');
  if (!refresh) throw error;
  original._retry = true;
  refreshing ??= axios.post<AuthResponse>(`${api.defaults.baseURL}/auth/refresh`, { refresh_token: refresh }).then(({ data }) => { saveTokens(data); return data.access_token; }).finally(() => { refreshing = null; });
  original.headers.Authorization = `Bearer ${await refreshing}`;
  return api(original);
});
export async function login(email: string, password: string) { const { data } = await api.post<AuthResponse>('/auth/login', { email, password }); saveTokens(data); return data; }
export async function register(input: { clinic_name: string; full_name: string; email: string; password: string }) { const { data } = await api.post<AuthResponse>('/auth/register', input); saveTokens(data); return data; }
export async function logout() { const token = localStorage.getItem('refresh_token'); if (token) await api.post('/auth/logout', { refresh_token: token }); localStorage.removeItem('access_token'); localStorage.removeItem('refresh_token'); }
export async function me() { return (await api.get<User>('/auth/me')).data; }
export async function listPatients(search?: string) { return (await api.get<Patient[]>('/patients', { params: search ? { search } : {} })).data; }
export async function countPatients() { return (await api.get<number>('/patients/count')).data; }
export async function getPatient(id: string) { return (await api.get<Patient>(`/patients/${id}`)).data; }
export async function createPatient(input: Partial<Patient> & Pick<Patient, 'first_name' | 'last_name'>) { return (await api.post<Patient>('/patients', input)).data; }
export async function updatePatient(id: string, input: Partial<Patient>) { return (await api.patch<Patient>(`/patients/${id}`, input)).data; }
export async function listRooms() { return (await api.get<Room[]>('/rooms')).data; }
export async function listAppointments() { return (await api.get<Appointment[]>('/appointments')).data; }
export async function createAppointment(input: Omit<Appointment, 'id' | 'clinic_id' | 'ends_at'>) { return (await api.post<Appointment>('/appointments', input)).data; }
