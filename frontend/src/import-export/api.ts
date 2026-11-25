import { api } from '../api/client';

export type ImportRow = { row_number: number; status: string; message: string | null; data?: Record<string, unknown>; patient_id?: string | null; entity_type?: string | null; entity_id?: string | null };
export type Preview = { filename: string; sha256: string; delimiter: ',' | ';'; total: number; new: number; existing: number; errors: number; rows: ImportRow[] };
export type ImportRun = { id: string; kind: string; filename: string; sha256: string; created_at: string; created: number; updated: number; rows: ImportRow[] };

const uploadHeaders = (file: File) => ({ 'Content-Type': 'text/csv; charset=utf-8', 'X-Filename': encodeURIComponent(file.name) });
const query = (columns: Record<string, string>) => ({ columns: JSON.stringify(columns) });

export async function previewCsv(kind: 'patients' | 'appointments', file: File, columns: Record<string, string>) {
  const { data } = await api.post<Preview>(`/import-export/${kind}/preview`, file, { params: query(columns), headers: uploadHeaders(file) });
  return data;
}
export async function confirmCsv(kind: 'patients' | 'appointments', file: File, columns: Record<string, string>, updateRows: number[] = []) {
  const { data } = await api.post<ImportRun>(`/import-export/${kind}/confirm`, file, { params: { ...query(columns), update_rows: updateRows.join(',') }, headers: uploadHeaders(file) });
  return data;
}
export async function listImportRuns() { return (await api.get<ImportRun[]>('/import-export/runs')).data; }
export async function downloadCsv(kind: 'patients' | 'appointments', start: string, end: string) {
  return (await api.get(`/import-export/export/${kind}`, { params: { start, end }, responseType: 'blob' })).data as Blob;
}
