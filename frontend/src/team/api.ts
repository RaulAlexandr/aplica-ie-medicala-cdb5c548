import { api } from '../api/client';

export type TeamMember = {
  id: string; full_name: string; role: string; is_active: boolean; specialization: string | null; job_title: string | null;
  email?: string; phone?: string | null; employment_start?: string | null; emergency_contact_name?: string | null; emergency_contact_phone?: string | null;
};
export type ProfileInput = Partial<Pick<TeamMember, 'phone' | 'emergency_contact_name' | 'emergency_contact_phone' | 'job_title' | 'specialization' | 'employment_start'>>;
export type DocumentCategory = 'contract' | 'certificate' | 'medical' | 'other';
export type TeamDocument = {
  id: string; user_id: string; category: DocumentCategory; description: string | null; original_filename: string; content_type: string;
  size_bytes: number; expires_on: string | null; status: 'active' | 'archived'; uploaded_at: string; uploaded_by: string;
  archived_at: string | null; archived_by: string | null; archive_reason: string | null;
};
export type Shift = {
  id: string; user_id: string; user_name: string; room_id: string | null; room_name: string | null; post: 'doctor' | 'assistant' | 'front_desk';
  starts_at: string; ends_at: string; local_start: string; local_end: string; status: string;
};
export type ScheduleDay = { date: string; start: string; end: string; hours: number };
export type ScheduleLeave = { id: string; user_id: string; user_name: string; starts_on: string; ends_on: string };
export type Schedule = { timezone: string; range_start: string; range_end: string; days: ScheduleDay[]; shifts: Shift[]; leaves: ScheduleLeave[] };
export type ShiftInput = { user_id: string; room_id: string | null; local_start: string; local_end: string; start_fold?: 0 | 1; end_fold?: 0 | 1 };
export type LeaveType = 'annual' | 'medical' | 'unpaid' | 'other';
export type Leave = {
  id: string; user_id: string; user_name: string; starts_on: string; ends_on: string; status: 'pending' | 'approved' | 'rejected' | 'cancelled';
  agenda_blocked: boolean; leave_type?: LeaveType; reason?: string | null; decision_reason?: string | null; decided_by?: string | null;
  affected_appointments?: Array<{ id: string; starts_at: string; status: string }>; affected_shifts?: Array<{ id: string; starts_at: string; ends_at: string }>;
};
export type AttendanceCorrection = { revision: number; clock_in_at: string; clock_out_at: string | null; reason: string; created_by: string; created_at: string };
export type Attendance = {
  id: string; user_id: string; user_name: string; source: 'clock' | 'manager'; clock_in_at: string; clock_out_at: string | null;
  effective_clock_in_at: string; effective_clock_out_at: string | null; is_open: boolean; worked_seconds: number | null;
  needs_review: boolean; open_over_limit: boolean; user_is_active: boolean; corrections: AttendanceCorrection[];
};
export type InactiveAlert = { user_id: string; user_name: string; future_shifts: number; open_attendance: boolean };
export type AttendanceAlerts = { review: Attendance[]; inactive: InactiveAlert[] };

export const listMembers = async () => (await api.get<TeamMember[]>('/team/members')).data;
export const updateProfile = async (id: string, body: ProfileInput) => (await api.put<TeamMember>(`/team/members/${id}/profile`, body)).data;
export const listDocuments = async (id: string, includeArchived = false) => (await api.get<TeamDocument[]>(`/team/members/${id}/documents`, { params: { include_archived: includeArchived } })).data;
export async function uploadDocument(id: string, file: File, category: DocumentCategory, expiresOn: string, key: string) {
  const params: Record<string, string> = { category };
  if (expiresOn) params.expires_on = expiresOn;
  return (await api.post<TeamDocument>(`/team/members/${id}/documents`, file, { params, headers: { 'Content-Type': 'application/octet-stream', 'X-File-Name': encodeURIComponent(file.name), 'Idempotency-Key': key } })).data;
}
export const fetchDocument = async (id: string) => (await api.get<Blob>(`/team/documents/${id}/content`, { params: { download: true }, responseType: 'blob' })).data;
export const archiveDocument = async (id: string, reason: string) => (await api.post<TeamDocument>(`/team/documents/${id}/archive`, { reason })).data;
export const getSchedule = async (mode: 'day' | 'week', date: string) => (await api.get<Schedule>('/team/schedule', { params: mode === 'day' ? { day: date } : { week_start: date } })).data;
export const createShift = async (body: ShiftInput) => (await api.post<Shift>('/team/shifts', body)).data;
export const cancelShift = async (id: string, reason: string) => (await api.post<Shift>(`/team/shifts/${id}/cancel`, { reason })).data;
export const listLeave = async () => (await api.get<Leave[]>('/team/leave')).data;
export const createLeave = async (body: { user_id?: string; leave_type: LeaveType; starts_on: string; ends_on: string; reason?: string }) => (await api.post<Leave>('/team/leave', body)).data;
export const approveLeave = async (id: string) => (await api.post<Leave>(`/team/leave/${id}/approve`)).data;
export const rejectLeave = async (id: string, reason: string) => (await api.post<Leave>(`/team/leave/${id}/reject`, { reason })).data;
export const cancelLeave = async (id: string, reason: string) => (await api.post<Leave>(`/team/leave/${id}/cancel`, { reason })).data;
export const myAttendance = async () => (await api.get<{ open: Attendance | null; sessions: Attendance[] }>('/team/attendance/me')).data;
export const clockIn = async () => (await api.post<Attendance>('/team/attendance/clock-in')).data;
export const clockOut = async () => (await api.post<Attendance>('/team/attendance/clock-out')).data;
export const listAttendance = async (start: string, end: string) => (await api.get<Attendance[]>('/team/attendance', { params: { start, end } })).data;
export const attendanceAlerts = async () => (await api.get<AttendanceAlerts>('/team/attendance/alerts')).data;
export const correctAttendance = async (id: string, body: { clock_in_at?: string; clock_out_at?: string; reason: string }) => (await api.post<Attendance>(`/team/attendance/${id}/corrections`, body)).data;
export const addManualAttendance = async (body: { user_id: string; clock_in_at: string; clock_out_at: string; reason: string }) => (await api.post<Attendance>('/team/attendance/manual', body)).data;
