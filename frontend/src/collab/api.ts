import { api } from '../api/client';

export type ConversationKind = 'direct' | 'group' | 'channel' | 'room';
export type Conversation = {
  id: string; kind: ConversationKind; title: string; room_id: string | null; created_by_id: string; last_read_seq: number; unread: number; can_write: boolean;
  members: Array<{ id: string; full_name: string; is_active: boolean }>;
  last_message: { seq: number; sender: string; preview: string; created_at: string } | null; activity_at: string;
};
export type Message = { id: string; conversation_id: string; seq: number; sender_id: string; sender: string; body: string; client_request_id: string; created_at: string; recipients: string[] };
export type MessagePage = { messages: Message[]; has_more: boolean };
export type Summary = { unread: number; pending_handovers: number };
export type TaskStatus = 'open' | 'in_progress' | 'blocked' | 'resolved' | 'cancelled';
export type TaskEvent = { version: number; from_status: TaskStatus | null; to_status: TaskStatus; from_assignee: string | null; to_assignee: string; reason: string | null; actor: string; created_at: string };
export type Task = {
  id: string; title: string; description: string | null; assignee_id: string; assignee: string; created_by_id: string; created_by: string; due_at: string | null;
  status: TaskStatus; version: number; overdue: boolean; created_at: string; updated_at: string; events: TaskEvent[];
};
export type TaskFilters = { mine?: boolean; overdue?: boolean; status?: TaskStatus };
export type TaskInput = { title: string; description?: string; assignee_id: string; local_due?: string; due_fold?: 0 | 1 };
export type TaskChange = { expected_version: number; status?: TaskStatus; assignee_id?: string; reason?: string };
export type Handover = {
  id: string; shift_id: string; shift_starts_at: string; shift_ends_at: string; from_user_id: string; from_user: string; to_user_id: string; to_user: string;
  created_by: string; notes: string; created_at: string; acknowledged_at: string | null;
  tasks: Array<{ task_id: string; title: string; status: TaskStatus; due_at: string | null; can_take: boolean; version: number | null; with_me: boolean }>;
};

/** crypto.randomUUID exists only in secure contexts; the clinic network may be plain HTTP. */
export function newRequestId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export const getSummary = async () => (await api.get<Summary>('/collab/summary')).data;
export const listConversations = async () => (await api.get<Conversation[]>('/collab/conversations')).data;
export const openDirect = async (userId: string) => (await api.post<Conversation>('/collab/direct', { user_id: userId })).data;
export const createConversation = async (body: { kind: 'group' | 'channel'; title: string; member_ids: string[] }) => (await api.post<Conversation>('/collab/conversations', body)).data;
export const addMember = async (id: string, userId: string) => (await api.post<Conversation>(`/collab/conversations/${id}/members`, { user_id: userId })).data;
export const listMessages = async (id: string, limit: number) => (await api.get<MessagePage>(`/collab/conversations/${id}/messages`, { params: { limit } })).data;
export const sendMessage = async (id: string, body: string, requestId: string) => (await api.post<Message>(`/collab/conversations/${id}/messages`, { body, client_request_id: requestId })).data;
export const sendToRoom = async (roomId: string, body: string, requestId: string) => (await api.post<Message>(`/collab/rooms/${roomId}/messages`, { body, client_request_id: requestId })).data;
export const markRead = async (id: string, seq: number) => (await api.post<{ last_read_seq: number }>(`/collab/conversations/${id}/read`, { seq })).data;
export const listTasks = async (filters: TaskFilters) => (await api.get<Task[]>('/collab/tasks', { params: { ...(filters.mine ? { mine: true } : {}), ...(filters.overdue ? { overdue: true } : {}), ...(filters.status ? { status: filters.status } : {}) } })).data;
export const createTask = async (body: TaskInput) => (await api.post<Task>('/collab/tasks', body)).data;
export const changeTask = async (id: string, body: TaskChange) => (await api.post<Task>(`/collab/tasks/${id}/changes`, body)).data;
export const listHandovers = async () => (await api.get<Handover[]>('/collab/handovers')).data;
export const createHandover = async (body: { shift_id: string; to_user_id: string; notes: string }) => (await api.post<Handover>('/collab/handovers', body)).data;
export const takeHandoverTask = async (id: string, taskId: string, version: number) => (await api.post<Task>(`/collab/handovers/${id}/tasks/${taskId}/take`, { expected_version: version })).data;
export const acknowledgeHandover = async (id: string) => (await api.post<Handover>(`/collab/handovers/${id}/acknowledge`)).data;
