import axios, { AxiosError, CanceledError, InternalAxiosRequestConfig } from 'axios';

export type User = { id: string; clinic_id: string; full_name: string; email: string; role: string };
export type Doctor = { id: string; full_name: string; role: string };
export type Patient = { id: string; clinic_id: string; first_name: string; last_name: string; date_of_birth: string | null; sex: string | null; phone: string | null; email: string | null; address: string | null; emergency_contact: string | null; occupation: string | null; allergies: string | null; medications: string | null; chronic_diseases: string | null; pregnancy_status: string | null; smoking_status: string | null; previous_surgeries: string | null; relevant_medical_conditions: string | null; medical_alerts: string | null; notes: string | null; created_at: string; updated_at: string };
export type Room = { id: string; clinic_id: string; name: string; is_active: boolean };
export type Appointment = { id: string; clinic_id: string; patient_id: string; doctor_id: string; assistant_id: string | null; room_id: string; starts_at: string; ends_at: string; duration_minutes: number; appointment_type: string; status: string; notes: string | null };
export type AuthResponse = { access_token: string; refresh_token: string; token_type: string };

type RequestConfig = InternalAxiosRequestConfig & { _retry?: boolean; _sessionGeneration?: number; _controller?: AbortController };
type SessionHandlers = { onSessionCleared?: () => void };

export const api = axios.create({ baseURL: import.meta.env.VITE_API_URL ?? 'http://localhost:8000/api' });
let sessionGeneration = 0;
let refreshing: Promise<string> | null = null;
let refreshGeneration: number | null = null;
let sessionHandlers: SessionHandlers = {};
const activeControllers = new Set<AbortController>();

export function configureSessionHandlers(handlers: SessionHandlers): void { sessionHandlers = handlers; }

const saveTokens = (data: AuthResponse): void => {
  localStorage.setItem('access_token', data.access_token);
  localStorage.setItem('refresh_token', data.refresh_token);
};

export function clearAuthenticatedSession(): void {
  sessionGeneration += 1;
  for (const controller of activeControllers) controller.abort();
  activeControllers.clear();
  refreshing = null;
  refreshGeneration = null;
  localStorage.removeItem('access_token');
  localStorage.removeItem('refresh_token');
  sessionHandlers.onSessionCleared?.();
  window.dispatchEvent(new CustomEvent('dentacare:session-cleared'));
}

api.interceptors.request.use((config) => {
  const controller = new AbortController();
  const requestConfig = config as RequestConfig;
  requestConfig._sessionGeneration = sessionGeneration;
  const existingSignal = config.signal;
  if (existingSignal) {
    const signal = existingSignal;
    if (signal.aborted) controller.abort();
    else if (signal.addEventListener) signal.addEventListener('abort', () => controller.abort(), { once: true });
  }
  activeControllers.add(controller);
  requestConfig._controller = controller;
  controller.signal.addEventListener('abort', () => activeControllers.delete(controller), { once: true });
  config.signal = controller.signal;
  const token = localStorage.getItem('access_token');
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

api.interceptors.response.use(
  (response) => {
    const requestConfig = response.config as RequestConfig;
    if (requestConfig._controller) activeControllers.delete(requestConfig._controller);
    if (requestConfig._sessionGeneration !== sessionGeneration) throw new CanceledError('Session ended');
    return response;
  },
  async (error: AxiosError) => {
    const original = error.config as RequestConfig | undefined;
    if (!original) throw error;
    if (original._controller) activeControllers.delete(original._controller);
    if (original._sessionGeneration !== sessionGeneration) throw new CanceledError('Session ended');
    const url = original.url ?? '';
    const isAuthEndpoint = ['/auth/login', '/auth/register', '/auth/refresh', '/auth/logout'].some((route) => url.includes(route));
    if (error.response?.status !== 401 || original._retry || isAuthEndpoint) throw error;

    const refreshToken = localStorage.getItem('refresh_token');
    if (!refreshToken) {
      clearAuthenticatedSession();
      throw error;
    }
    const requestGeneration = sessionGeneration;
    original._retry = true;
    if (!refreshing || refreshGeneration !== requestGeneration) {
      refreshGeneration = requestGeneration;
      const refreshRequest = axios.post<AuthResponse>(`${api.defaults.baseURL}/auth/refresh`, { refresh_token: refreshToken });
      refreshing = refreshRequest
        .then(({ data }) => {
          if (requestGeneration !== sessionGeneration) throw new CanceledError('Session ended');
          saveTokens(data);
          return data.access_token;
        })
        .catch((refreshError) => {
          if (requestGeneration === sessionGeneration) clearAuthenticatedSession();
          throw refreshError;
        })
        .finally(() => {
          if (refreshGeneration === requestGeneration) {
            refreshing = null;
            refreshGeneration = null;
          }
        });
    }
    const accessToken = await refreshing;
    if (requestGeneration !== sessionGeneration) throw new CanceledError('Session ended');
    original.headers.Authorization = `Bearer ${accessToken}`;
    return api(original);
  },
);

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
  const refreshToken = localStorage.getItem('refresh_token');
  const accessToken = localStorage.getItem('access_token');
  clearAuthenticatedSession();
  if (!refreshToken) return;
  try {
    await axios.post(`${api.defaults.baseURL}/auth/logout`, { refresh_token: refreshToken }, { headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : undefined });
  } catch {
    // Local session clearing is authoritative when the server session is already expired.
  }
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
