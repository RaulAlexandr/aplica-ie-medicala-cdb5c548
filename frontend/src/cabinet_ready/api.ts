import { api } from '../api/client';

export type ChecklistKind = 'material' | 'instrument' | 'document' | 'action';
export type ChecklistLine = { kind: ChecklistKind; label: string; required: boolean; prep_minutes: number };
export type Checklist = { procedure_catalog_id: string; procedure_name: string; version: number; versions: number[]; lines: ChecklistLine[] };
export type Blocker = { id?: string; type: string; type_label: string; text?: string; check_with?: string };
export type Warning = { type: string; text: string; check_with: string };
export type PreparationLine = {
  id: string | null; kind: ChecklistKind; label: string; required: boolean; prep_minutes: number; procedure_name: string | null;
  from_particularity: boolean; confirmed: boolean; confirmed_by: string | null; confirmed_at: string | null;
};
export type Particularity = {
  id: string; text: string; status: 'pending' | 'approved' | 'rejected'; item_label: string | null; item_kind: ChecklistKind | null;
  item_required: boolean | null; item_minutes: number | null;
};
export type CabinetCard = {
  appointment_id: string; starts_at: string; ends_at: string; local_time: string; local_date: string; appointment_status: string;
  room_id: string; room_name: string; doctor_id: string; doctor_name: string; patient_name: string;
  state: 'unknown' | 'in_preparation' | 'blocked' | 'ready'; state_label: string; confirmed_required: number; total_required: number; counter: string;
  started: boolean; start_by: string; start_by_local: string; late: boolean; blockers: Blocker[];
  warnings?: Warning[]; missing?: string[]; explanation?: string | null; started_at?: string | null; ready_at?: string | null;
  prep_minutes?: number; items?: PreparationLine[]; particularities?: Particularity[];
};
export type CabinetBoard = { day: string; timezone: string; server_now: string; rooms: Array<{ id: string; name: string }>; cards: CabinetCard[] };
export type ParticularityInput = { text: string; item_kind?: ChecklistKind; item_label?: string; item_required?: boolean; item_minutes?: number };

const base = '/cabinet-ready';
export async function getBoard(day: string) { return (await api.get<CabinetBoard>(`${base}/board`, { params: { day } })).data; }
export async function getChecklist(procedureId: string) { return (await api.get<Checklist>(`${base}/checklists/${procedureId}`)).data; }
export async function saveChecklist(procedureId: string, expected_version: number, lines: ChecklistLine[], client_request_id: string) {
  return (await api.put(`${base}/checklists/${procedureId}`, { expected_version, lines, client_request_id })).data;
}
export async function startPreparation(appointmentId: string, client_request_id: string) {
  return (await api.post(`${base}/appointments/${appointmentId}/start`, { client_request_id })).data;
}
export async function confirmItem(itemId: string, client_request_id: string) {
  return (await api.post(`${base}/items/${itemId}/confirm`, { client_request_id })).data;
}
export async function revokeItem(itemId: string, reason: string, client_request_id: string) {
  return (await api.post(`${base}/items/${itemId}/revoke`, { reason, client_request_id })).data;
}
export async function addBlock(appointmentId: string, reason: string, check_with: string, client_request_id: string) {
  return (await api.post(`${base}/appointments/${appointmentId}/blocks`, { reason, check_with, client_request_id })).data;
}
export async function closeBlock(blockId: string, reason: string, client_request_id: string) {
  return (await api.post(`${base}/blocks/${blockId}/close`, { reason, client_request_id })).data;
}
export async function proposeParticularity(appointmentId: string, input: ParticularityInput, client_request_id: string) {
  return (await api.post(`${base}/appointments/${appointmentId}/particularities`, { ...input, client_request_id })).data;
}
export async function decideParticularity(id: string, decision: 'approved' | 'rejected', client_request_id: string) {
  return (await api.post(`${base}/particularities/${id}/decision`, { decision, client_request_id })).data;
}
