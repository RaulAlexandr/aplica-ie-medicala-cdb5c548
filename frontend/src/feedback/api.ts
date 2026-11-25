import { api } from '../api/client';

export type FeedbackSettings = { delay_minutes: number; negative_threshold: number; review_enabled: boolean; review_url: string | null; hourly_limit: number };
export type FeedbackRow = { id: string; appointment_id: string; patient_id: string; patient_name: string; doctor_id: string; doctor_name: string; rating: number; comment: string | null; created_at: string; task: { id: string; status: string } | null };
export type FilterType = 'last_visit_before' | 'recall_due' | 'procedure_in_period' | 'no_future_appointment';
export type CampaignFilter = { filter_type: FilterType; filter_date?: string; period_start?: string; period_end?: string };
export type Campaign = { id: string; name: string; filter_type: FilterType; status: string; scheduled_for: string; launched_at: string | null };
export type RecipientPreview = { id: string; name: string; channel: string | null; eligible: boolean };

export const getFeedbackSettings = async () => (await api.get<FeedbackSettings>('/feedback/settings')).data;
export const saveFeedbackSettings = async (data: FeedbackSettings) => (await api.put<FeedbackSettings>('/feedback/settings', data)).data;
export const listFeedback = async (params: { start?: string; end?: string } = {}) => (await api.get<FeedbackRow[]>('/feedback', { params })).data;
export const feedbackReport = async (params: { start?: string; end?: string } = {}) => (await api.get<{ requests: number; responses: number; response_rate: number; ratings: Record<number, number>; by_doctor: Record<string, { name: string; responses: number; ratings: Record<number, number> }> }>('/feedback/report', { params })).data;
export const listCampaigns = async () => (await api.get<Campaign[]>('/comms/campaigns')).data;
export const previewCampaign = async (data: CampaignFilter) => (await api.post<{ count: number; eligible_count: number; recipients: RecipientPreview[] }>('/comms/campaigns/preview', data)).data;
export const createCampaign = async (data: CampaignFilter & { name: string; scheduled_for: string }) => (await api.post<{ id: string; status: string }>('/comms/campaigns', { ...data, template_type: 'campaign' })).data;
export const launchCampaign = async (id: string) => (await api.post<{ id: string; status: string; queued: number }>(`/comms/campaigns/${id}/launch`)).data;
export const getPublicFeedback = async (token: string) => (await api.get<{ valid: boolean; message: string; first_name: string | null; clinic_name: string | null; visit_date: string | null }>(`/feedback/public/${token}`)).data;
export const submitPublicFeedback = async (token: string, rating: number, comment: string) => (await api.post<{ valid: boolean; message: string }>(`/feedback/public/${token}`, { rating, comment })).data;
export const checkCampaignUnsubscribe = async (token: string) => (await api.get<{ valid: boolean; message: string }>(`/comms/campaign-unsubscribe/${token}`)).data;
export const unsubscribeCampaigns = async (token: string) => (await api.post<{ message: string }>(`/comms/campaign-unsubscribe/${token}`)).data;
