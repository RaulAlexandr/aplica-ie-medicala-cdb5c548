import { api } from '../api/client';

export type Question = { id: string; label: string; type: 'short_text' | 'long_text' | 'yes_no' | 'single_choice' | 'multiple_choice' | 'date' | 'number'; required: boolean; options?: Array<{ value: string; label: string }>; condition?: { question_id: string; operator: 'equals'; value: string } };
export type FormDefinition = { sections: Array<{ id: string; title: string; questions: Question[] }> };
export type PatientForm = { id: string; patient_id: string; template_version_id: string; template_name: string | null; version_number: number; source: 'staff' | 'patient'; status: 'draft' | 'submitted' | 'validated' | 'needs_clarification'; definition?: FormDefinition; answers?: Record<string, unknown>; portal_submissions?: Array<{ revision_number: number; submitted_at: string; author: string; source: 'patient'; template_version_id: string; answers: Record<string, unknown> }>; validated_at: string | null; clarification_reason: string | null };
export const listPatientForms = async (patientId: string) => (await api.get<PatientForm[]>(`/forms/patients/${patientId}`)).data;
export const listPublishedForms = async () => (await api.get<Array<{ id: string; name: string; version_id: string; version_number: number }>>('/forms/templates')).data;
export const createPatientForm = async (patientId: string, versionId: string) => (await api.post<PatientForm>(`/forms/patients/${patientId}/${versionId}`)).data;
export const getPatientForm = async (id: string) => (await api.get<PatientForm>(`/forms/${id}`)).data;
export const getFormRevisions = async (id: string) => (await api.get<Array<{ revision_number: number; answers: Record<string, unknown>; status: string; reason: string; actor_id: string; actor_name: string; created_at: string }>>(`/forms/${id}/revisions`)).data;
export const saveAnswers = async (id: string, answers: Record<string, unknown>) => (await api.put<PatientForm>(`/forms/${id}/answers`, { answers })).data;
export const submitForm = async (id: string) => (await api.post<PatientForm>(`/forms/${id}/submit`)).data;
export const reviewForm = async (id: string, decision: 'validated' | 'needs_clarification', reason?: string) => (await api.post<PatientForm>(`/forms/${id}/review`, { decision, reason })).data;
export const createTemplate = async (name: string) => (await api.post('/forms/templates', { name })).data;
export const createTemplateVersion = async (templateId: string, definition: FormDefinition) => (await api.post(`/forms/templates/${templateId}/versions`, { definition })).data;
export const listManagerTemplates = async () => (await api.get<Array<{ id: string; name: string; version_id: string; version_number: number; status: 'draft' | 'published'; definition: FormDefinition }>>('/forms/templates')).data;
export const updateVersion = async (id: string, definition: FormDefinition) => (await api.put(`/forms/versions/${id}`, { definition })).data;
export const publishVersion = async (id: string) => (await api.post(`/forms/versions/${id}/publish`)).data;
