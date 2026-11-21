import { api } from '../api/client';

export type Equipment = { id: string; name: string; equipment_type: string; serial_number: string | null; room_id: string | null; responsible_id: string; state: string; is_active: boolean };
export type AffectedAppointment = { appointment_id: string; patient: string; starts_at: string; ends_at: string | null; doctor: string; room: string; appointment_type: string; responsible: string; match: 'sigur' | 'posibil' };
export type Outage = { id: string; equipment_id: string; reason: string; starts_at: string; ends_at: string; closed_at: string | null; affected_appointments: AffectedAppointment[] };
export type EquipmentHistory = { checks: Array<Record<string, unknown>>; documents: Array<Record<string, unknown>>; dependencies: Array<Record<string, unknown>>; unavailabilities: Outage[] };

export async function listEquipment() { return (await api.get<Equipment[]>('/equipment')).data; }
export async function createEquipment(input: Omit<Equipment, 'id'>) { return (await api.post<Equipment>('/equipment', input)).data; }
export async function updateEquipment(input: Equipment) { return (await api.patch<Equipment>(`/equipment/${input.id}`, input)).data; }
export async function listDueChecks() { return (await api.get<Array<Record<string, unknown> & { equipment_name: string; overdue: boolean }>>('/equipment/checks/due')).data; }
export async function equipmentHistory(id: string) { return (await api.get<EquipmentHistory>(`/equipment/${id}/history`)).data; }
export async function reportOutage(id: string, input: { reason: string; starts_at: string; ends_at: string }) { return (await api.post<Outage>(`/equipment/${id}/unavailabilities`, input)).data; }
export async function closeOutage(equipmentId: string, outageId: string) { return (await api.post(`/equipment/${equipmentId}/unavailabilities/${outageId}/close`)).data; }
export async function addCheck(id: string, input: { check_type: string; due_at: string; completed_at?: string; result?: string }) { return (await api.post(`/equipment/${id}/checks`, input)).data; }
export async function completeCheck(equipmentId: string, checkId: string, result: string) { return (await api.post(`/equipment/${equipmentId}/checks/${checkId}/complete`, { result })).data; }
export async function addDocument(id: string, input: { title: string; document_type: string; reference: string; notes?: string }) { return (await api.post(`/equipment/${id}/documents`, input)).data; }
export async function addDependency(id: string, input: { room_id?: string; procedure_id?: string }) { return (await api.post(`/equipment/${id}/dependencies`, input)).data; }
