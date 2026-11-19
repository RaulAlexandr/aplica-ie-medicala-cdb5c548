import { api } from '../api/client';

export type EntryKind = 'payment' | 'refund' | 'adjustment_credit' | 'adjustment_debit' | 'reversal';
export type PaymentMethod = 'cash' | 'card' | 'transfer' | 'other';

export type AccountProcedure = { id: string; code: string; name: string; tooth_fdi: string | null; status: string; counted: boolean; amount: string | null; price_overridden: boolean };
export type AccountVisit = { appointment_id: string; starts_at: string | null; appointment_type: string | null; appointment_status: string | null; completed_total: string; procedures: AccountProcedure[] };
export type AccountEntry = {
  id: string; kind: EntryKind; amount: string; currency: string; method: PaymentMethod | null; reason: string | null;
  appointment_id: string | null; reverses_entry_id: string | null; created_at: string; created_by: { id: string; name: string | null };
  reversed_by: null | { id: string; created_at: string; by: string | null; reason: string | null };
};
export type PatientAccount = {
  patient_id: string; currency: string; timezone: string;
  totals: { services_completed: string; payments: string; refunds: string; paid_net: string; adjustments_debit: string; adjustments_credit: string; balance: string; balance_status: 'due' | 'credit' | 'settled' };
  services: { visits: AccountVisit[]; in_progress_count: number; cancelled_count: number };
  entries: AccountEntry[];
  permissions: { can_record_cash: boolean; can_adjust: boolean };
};
export type EntryInput = { kind: Exclude<EntryKind, 'reversal'>; amount: string; method?: PaymentMethod | null; reason?: string | null; appointment_id?: string | null; client_request_id: string };
export type VisitFinance = { appointment_id: string; patient_id: string; currency: string; starts_at: string | null; services_completed: string; completed_count: number; in_progress_count: number; paid_net_for_visit: string; permissions: { can_record_cash: boolean } };
export type DailyReport = {
  date: string; timezone: string; currency: string; range: { start: string; end: string };
  by_method: Record<PaymentMethod, string>; received: string; refunded: string; reversed: string; net: string;
  entries: { id: string; kind: EntryKind; method: PaymentMethod | null; signed_amount: string; patient_id: string; patient_name: string | null; reverses_entry_id: string | null; reason: string | null; created_at: string; created_by: string | null }[];
};

export const KIND_LABELS: Record<EntryKind, string> = {
  payment: 'Încasare', refund: 'Rambursare', adjustment_credit: 'Ajustare în favoarea pacientului',
  adjustment_debit: 'Ajustare de plată suplimentară', reversal: 'Anulare',
};
export const METHOD_LABELS: Record<PaymentMethod, string> = { cash: 'Numerar', card: 'Card', transfer: 'Transfer', other: 'Altă metodă' };

export function newRequestId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => { const r = Math.random() * 16 | 0; return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16); });
}

/** Client-side check mirroring the server: decimal string, > 0, at most two decimals. */
export function amountError(value: string): string | null {
  const text = value.trim();
  if (!text) return 'Introdu suma.';
  if (!/^\d{1,10}(\.\d{1,2})?$/.test(text)) return 'Suma trebuie să fie un număr pozitiv cu cel mult două zecimale (ex. 150.00).';
  if (Number(text) <= 0) return 'Suma trebuie să fie mai mare decât zero.';
  return null;
}

export async function getPatientAccount(patientId: string) { return (await api.get<PatientAccount>(`/finance/patients/${patientId}/account`)).data; }
export async function createEntry(patientId: string, input: EntryInput) { return (await api.post<AccountEntry>(`/finance/patients/${patientId}/entries`, input)).data; }
export async function reverseEntry(entryId: string, reason: string, clientRequestId: string) { return (await api.post<AccountEntry>(`/finance/entries/${entryId}/reverse`, { reason, client_request_id: clientRequestId })).data; }
export async function getVisitFinance(appointmentId: string) { return (await api.get<VisitFinance>(`/finance/appointments/${appointmentId}/summary`)).data; }
export async function getDailyReport(date: string) { return (await api.get<DailyReport>('/finance/daily', { params: { date } })).data; }
