import { api } from '../api/client';

export type Channel = 'sms' | 'email';
export type MessageType = 'confirmation' | 'reminder' | 'cancellation' | 'reschedule';
export type MessageStatus = 'pending' | 'sent' | 'delivered' | 'failed' | 'cancelled';
export type ConsentSource = 'in_person' | 'phone' | 'paper_form' | 'patient_reply' | 'other';
export type Consent = { id: string; channel: Channel; granted: boolean; source: ConsentSource; recorded_by_id: string; recorded_by: string | null; recorded_at: string };
export type Consents = { current: Record<Channel, Consent | null>; history: Consent[] };
export type MessageEvent = { id: string; from_status: string | null; to_status: string; reason: string | null; occurred_at: string };
export type Message = {
  id: string; patient_id: string; patient_name: string | null; appointment_id: string; message_type: MessageType; channel: Channel; status: MessageStatus;
  scheduled_for: string; appointment_starts_at: string; attempts: number; reason: string | null; last_error: string | null; subject: string | null; body: string | null;
  provider_ref: string | null; adapter: string | null; template_version: number | null; sent_at: string | null; created_at: string; events: MessageEvent[];
};
export type Template = { message_type: MessageType; version: number; subject: string; body: string; created_at: string | null };
export type CommSettings = { reminder_hours: number; quiet_start: string; quiet_end: string; clinic_phone: string | null; allowed_variables: string[] };

export const channels: Channel[] = ['sms', 'email'];
export const messageTypes: MessageType[] = ['confirmation', 'reminder', 'cancellation', 'reschedule'];
export const statuses: MessageStatus[] = ['pending', 'sent', 'delivered', 'failed', 'cancelled'];

export async function getConsents(patientId: string) { return (await api.get<Consents>(`/patients/${patientId}/comms/consents`)).data; }
export async function recordConsent(patientId: string, input: { channel: Channel; granted: boolean; source: ConsentSource }) { return (await api.post<Consent>(`/patients/${patientId}/comms/consents`, input)).data; }
export async function patientMessages(patientId: string) { return (await api.get<Message[]>(`/patients/${patientId}/comms/messages`)).data; }
export async function listTemplates() { return (await api.get<Template[]>('/comms/templates')).data; }
export async function saveTemplate(type: MessageType, input: { subject: string; body: string }) { return (await api.put<Template>(`/comms/templates/${type}`, input)).data; }
export async function getCommSettings() { return (await api.get<CommSettings>('/comms/settings')).data; }
export async function saveCommSettings(input: Omit<CommSettings, 'allowed_variables'>) { return (await api.put<CommSettings>('/comms/settings', input)).data; }
export async function listOutbox(status?: MessageStatus | '') { return (await api.get<Message[]>('/comms/outbox', { params: status ? { status } : {} })).data; }
export async function processOutbox() { return (await api.post<{ sent: number; failed: number; cancelled: number; retry: number; deferred: number }>('/comms/process')).data; }
export async function reportDelivery(id: string, delivered: boolean) { return (await api.post<Message>(`/comms/outbox/${id}/delivery`, { delivered })).data; }
