import { api } from '../api/client';

export type DocumentCategory = 'photo' | 'radiograph' | 'document' | 'other';

export type ClinicalDocument = {
  id: string; patient_id: string; category: DocumentCategory; description: string | null;
  original_filename: string; content_type: 'image/jpeg' | 'image/png' | 'application/pdf';
  size_bytes: number; width: number | null; height: number | null; is_image: boolean; tooth_fdi: string | null;
  appointment: null | { id: string; starts_at: string; appointment_type: string; status: string };
  clinical_date: string | null; uploaded_at: string; uploaded_by: { id: string; name: string };
  status: 'active' | 'archived'; archived_at: string | null; archived_by: string | null; archive_reason: string | null;
  sha256: string;
};

export type DocumentLimits = {
  accepted_types: string[]; accepted_extensions: string[]; max_bytes: number; max_image_side: number;
  max_image_pixels: number; categories: DocumentCategory[]; image_only_categories: DocumentCategory[];
  not_supported: string[]; antivirus_scan: boolean;
};

export type VisitOption = { id: string; starts_at: string; appointment_type: string; status: string };

export type UploadInput = {
  file: Blob; fileName: string; category: DocumentCategory; appointmentId?: string; toothFdi?: string;
  clinicalDate?: string; description?: string;
};

const base = '/documents';

export async function getDocumentLimits() { return (await api.get<DocumentLimits>(`${base}/limits`)).data; }
export async function listPatientDocuments(patientId: string, includeArchived = false) {
  return (await api.get<ClinicalDocument[]>(`${base}/patients/${patientId}`, { params: { include_archived: includeArchived } })).data;
}
export async function listVisitOptions(patientId: string) {
  return (await api.get<VisitOption[]>(`${base}/patients/${patientId}/visit-options`)).data;
}
export async function uploadDocument(patientId: string, input: UploadInput, idempotencyKey: string) {
  const headers: Record<string, string> = {
    'Content-Type': 'application/octet-stream',
    'X-File-Name': encodeURIComponent(input.fileName),
    'Idempotency-Key': idempotencyKey,
  };
  if (input.description?.trim()) headers['X-Document-Description'] = encodeURIComponent(input.description.trim());
  const params: Record<string, string> = { category: input.category };
  if (input.appointmentId) params.appointment_id = input.appointmentId;
  if (input.toothFdi) params.tooth_fdi = input.toothFdi;
  if (input.clinicalDate) params.clinical_date = input.clinicalDate;
  return (await api.post<ClinicalDocument>(`${base}/patients/${patientId}`, input.file, { headers, params })).data;
}
/** Authorized content fetch (Bearer header); never a token or permanent link in a URL. */
export async function fetchDocumentContent(documentId: string, download = false) {
  return (await api.get<Blob>(`${base}/${documentId}/content`, { params: download ? { download: true } : undefined, responseType: 'blob' })).data;
}
export async function archiveDocument(documentId: string, reason: string) {
  return (await api.post<ClinicalDocument>(`${base}/${documentId}/archive`, { reason })).data;
}
