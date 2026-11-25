import { api } from '../api/client';

export const sterilizationRoles = ['clinic_manager', 'administrator', 'assistant', 'doctor'];
export const sterilizationWriters = ['clinic_manager', 'administrator', 'assistant'];
export const canSeeSterilization = (role: string) => sterilizationRoles.includes(role);
export const canWriteSterilization = (role: string) => sterilizationWriters.includes(role);

export type PackState = 'unsterile' | 'in_cycle' | 'sterile' | 'used' | 'expired' | 'compromised';
export type IndicatorResult = 'pending' | 'pass' | 'fail';
export type CycleStatus = 'in_progress' | 'finished' | 'approved' | 'rejected';
export type SetType = { id: string; name: string; description: string | null; expiry_days: number; is_active: boolean };
export type Pack = { id: string; code: string; set_type_id: string; set_type_name: string; state: PackState; expires_at: string | null; cycle_id: string | null; cycle_number: number | null; equipment_name: string | null };
export type Cycle = {
  id: string; equipment_id: string; equipment_name: string | null; cycle_number: number; program: string; started_at: string; ended_at: string | null;
  operator_id: string; operator_name: string | null; temperature_c: string | null; pressure_kpa: string | null; hold_minutes: number | null; status: CycleStatus;
  decided_by_id: string | null; decided_by_name: string | null; decided_at: string | null; decision_note: string | null;
  chemical_result: IndicatorResult | null; biological_result: IndicatorResult | null; compromised: boolean; recall_reason: string | null; pack_codes: string[]; pack_count: number;
  indicators?: Array<{ kind: string; result: IndicatorResult; note: string | null; recorded_by_name: string; recorded_at: string }>;
};
export type RecallItem = { pack_id: string; pack_code: string; set_type: string; used_at: string; appointment_id: string; appointment_starts_at: string; patient_id: string; patient_name: string; doctor_name: string; reason: string };
export type Recall = { cycle: Cycle; compromised: boolean; reason: string | null; recorded_at: string | null; items: RecallItem[]; unused_packs: string[] };
export type TraceRow = {
  event_id: string; pack_code: string; set_type: string; used_at: string; appointment_id: string; appointment_starts_at: string; patient_id: string; procedure_name: string | null;
  cycle_id: string; cycle_number: number; cycle_status: CycleStatus; equipment_name: string; approved_at: string | null; compromised: boolean; recall_reason: string | null;
};
export type FinishInput = { temperature_c: string; pressure_kpa: string; hold_minutes: number; chemical_result: IndicatorResult; biological_result: IndicatorResult };

export const listSetTypes = async () => (await api.get<SetType[]>('/sterilization/set-types')).data;
export const createSetType = async (input: { name: string; description: string | null; expiry_days: number }) => (await api.post<SetType>('/sterilization/set-types', input)).data;
export const listPacks = async () => (await api.get<Pack[]>('/sterilization/packs')).data;
export const createPack = async (input: { set_type_id: string; code: string }) => (await api.post<Pack>('/sterilization/packs', input)).data;
export const listCycles = async () => (await api.get<Cycle[]>('/sterilization/cycles')).data;
export const getCycle = async (id: string) => (await api.get<Cycle>(`/sterilization/cycles/${id}`)).data;
export const startCycle = async (input: { equipment_id: string; program: string; pack_ids: string[] }) => (await api.post<Cycle>('/sterilization/cycles', input)).data;
export const finishCycle = async (id: string, input: FinishInput) => (await api.post<Cycle>(`/sterilization/cycles/${id}/finish`, input)).data;
export const decideCycle = async (id: string, decision: 'approved' | 'rejected', note: string) => (await api.post<Cycle>(`/sterilization/cycles/${id}/decision`, { decision, note: note.trim() || null })).data;
export const recordIndicator = async (id: string, input: { kind: 'chemical' | 'biological'; result: IndicatorResult; note: string | null }) => (await api.post<Cycle>(`/sterilization/cycles/${id}/indicators`, input)).data;
export const getRecall = async (id: string) => (await api.get<Recall>(`/sterilization/cycles/${id}/recall`)).data;
export const downloadRecallCsv = async (id: string) => (await api.get<Blob>(`/sterilization/cycles/${id}/recall.csv`, { responseType: 'blob' })).data;
export const usePack = async (packId: string, input: { appointment_id: string; performed_procedure_id: string | null }) => (await api.post(`/sterilization/packs/${packId}/use`, input)).data;
export const traceAppointment = async (id: string) => (await api.get<TraceRow[]>(`/sterilization/trace/appointments/${id}`)).data;
export const tracePatient = async (id: string) => (await api.get<TraceRow[]>(`/sterilization/trace/patients/${id}`)).data;

export const packStateLabel: Record<PackState, string> = { unsterile: 'Nesterilizat', in_cycle: 'În ciclu', sterile: 'Steril', used: 'Folosit', expired: 'Expirat', compromised: 'Compromis' };
export const cycleStatusLabel: Record<CycleStatus, string> = { in_progress: 'În curs', finished: 'Încheiat', approved: 'Aprobat', rejected: 'Respins' };
export const indicatorLabel: Record<IndicatorResult, string> = { pending: 'în așteptare', pass: 'conform', fail: 'neconform' };
export const formatDateTime = (value: string | null) => value ? new Intl.DateTimeFormat('ro-RO', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : '—';
