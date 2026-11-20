import { api } from '../api/client';

export type ReportRecord = { id: string; entity: string; date: string; status: string; amount: string | null; patient_name: string | null; [key: string]: unknown };
export type ReportResult = {
  indicator: string;
  value: number | string | null | Record<string, unknown>;
  unit: string;
  definition: string;
  period: { from: string; to: string; timezone: string; semantics: string };
  filters: { doctor_id: string | null; room_id: string | null };
  source: string;
  undefined_reason: string | null;
  records_url: string;
};
export type ReportRecords = { indicator: string; total: number; limit: number; offset: number; records: ReportRecord[] };

export async function getReport(indicator: string, from: string, to: string, filters: { doctor_id?: string; room_id?: string } = {}) {
  return (await api.get<ReportResult>(`/reports/${indicator}`, { params: { from, to, ...filters } })).data;
}
export async function getReportRecords(indicator: string, from: string, to: string, filters: { doctor_id?: string; room_id?: string } = {}) {
  return (await api.get<ReportRecords>(`/reports/${indicator}/records`, { params: { from, to, ...filters } })).data;
}
