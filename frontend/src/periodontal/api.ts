import { api } from '../api/client';

export const sites = ['buccal_mesial', 'buccal_mid', 'buccal_distal', 'oral_mesial', 'oral_mid', 'oral_distal'] as const;
export type SiteName = typeof sites[number];
export type Site = { site: SiteName; probing_depth_mm: number | null; recession_mm: number | null; bop: boolean | null; plaque: boolean | null; suppuration: boolean | null };
export type Tooth = { tooth_fdi: string; is_excluded: boolean; mobility: string | null; furcation: string | null; sites: Site[] };
export type ComparisonValue = { first: string | number | boolean | null; second: string | number | boolean | null; changed: boolean | null };
export type SiteComparison = { site: SiteName; first: Site | null; second: Site | null; differences: Record<'probing_depth_mm' | 'recession_mm' | 'bop' | 'plaque' | 'suppuration', number | boolean | null> };
export type Comparison = { first: { id: string; clinical_date: string; author_name: string }; second: { id: string; clinical_date: string; author_name: string }; teeth: Array<{ tooth_fdi: string; mobility: ComparisonValue; furcation: ComparisonValue; sites: SiteComparison[]; second_bop_positive: number; second_bop_known_denominator: number; second_bop_percent: number | null }> };
export type Assessment = { id: string; patient_id: string; author_id: string; author_name: string; clinical_date: string; status: 'draft' | 'finalized'; previous_assessment_id: string | null; relationship_kind: string | null; change_reason: string | null; created_at: string; finalized_at: string | null; teeth: Tooth[] };
export const listAssessments = async (patientId: string) => (await api.get<Assessment[]>(`/periodontal/patients/${patientId}/assessments`)).data;
export const getAssessment = async (id: string) => (await api.get<Assessment>(`/periodontal/assessments/${id}`)).data;
export const createAssessment = async (patientId: string, body: { clinical_date: string; previous_assessment_id?: string; relationship_kind?: 'follow_up' | 'correction'; change_reason?: string }) => (await api.post<Assessment>(`/periodontal/patients/${patientId}/assessments`, body)).data;
export const saveMeasurements = async (id: string, teeth: Tooth[]) => (await api.put<Assessment>(`/periodontal/assessments/${id}/measurements`, { teeth })).data;
export const finalizeAssessment = async (id: string) => (await api.post<Assessment>(`/periodontal/assessments/${id}/finalize`)).data;
export const compareAssessments = async (patientId: string, first: string, second: string) => (await api.get<Comparison>(`/periodontal/patients/${patientId}/compare`, { params: { first_id: first, second_id: second } })).data;
export const emptyTooth = (tooth_fdi: string): Tooth => ({ tooth_fdi, is_excluded: false, mobility: null, furcation: null, sites: sites.map(site => ({ site, probing_depth_mm: null, recession_mm: null, bop: null, plaque: null, suppuration: null })) });
