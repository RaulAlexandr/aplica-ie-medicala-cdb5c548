import axios from 'axios';

export type User = { id: string; clinic_id: string; full_name: string; email: string; role: string };
export type Patient = { id: string; clinic_id: string; first_name: string; last_name: string; phone?: string; email?: string; date_of_birth?: string; notes?: string; created_at: string; updated_at: string };
export type Room = { id: string; clinic_id: string; name: string; is_active: boolean };
export type Appointment = { id: string; clinic_id: string; patient_id: string; doctor_id: string; room_id: string; starts_at: string; ends_at: string; duration_minutes: number; appointment_type: string; status: string; notes?: string };
export type AuthResponse = { access_token: string; refresh_token: string; token_type: string };

export const api = axios.create({ baseURL: import.meta.env.VITE_API_URL ?? 'http://localhost:8000/api' });
api.interceptors.request.use((config) => { const token = localStorage.getItem('access_token'); if (token) config.headers.Authorization = `Bearer ${token}`; return config; });

export async function login(email: string, password: string) { const { data } = await api.post<AuthResponse>('/auth/login', { email, password }); localStorage.setItem('access_token', data.access_token); localStorage.setItem('refresh_token', data.refresh_token); return data; }
export async function register(input: { clinic_name: string; full_name: string; email: string; password: string }) { const { data } = await api.post<AuthResponse>('/auth/register', input); localStorage.setItem('access_token', data.access_token); localStorage.setItem('refresh_token', data.refresh_token); return data; }
export async function me() { return (await api.get<User>('/auth/me')).data; }
export async function listPatients(search?: string) { return (await api.get<Patient[]>('/patients', { params: search ? { search } : {} })).data; }
export async function createPatient(input: Pick<Patient, 'first_name' | 'last_name' | 'phone' | 'email' | 'notes'>) { return (await api.post<Patient>('/patients', input)).data; }
export async function listRooms() { return (await api.get<Room[]>('/rooms')).data; }
export async function listAppointments() { return (await api.get<Appointment[]>('/appointments')).data; }
