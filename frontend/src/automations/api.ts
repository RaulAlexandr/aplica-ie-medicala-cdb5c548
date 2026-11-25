import { api } from '../api/client';

export const AUTOMATION_TRIGGERS = [
  ['appointment_created', 'Programare creată'], ['appointment_cancelled', 'Programare anulată'],
  ['appointment_no_show', 'Neprezentare'], ['visit_closed', 'Vizită închisă'],
  ['procedure_completed', 'Manoperă finalizată'], ['recall_due', 'Rechemare scadentă'],
  ['stock_below_minimum', 'Stoc sub minim'], ['plan_unbooked', 'Plan acceptat cu elemente neprogramate'],
] as const;
export const AUTOMATION_FIELDS = [
  ['doctor_role', 'Rolul medicului'], ['appointment_type', 'Tipul programării'],
  ['procedure_id', 'Procedura din catalog (ID)'], ['days', 'Numărul de zile'], ['room_id', 'Cabinet (ID)'],
] as const;
export type AutomationCondition = { field: string; operator: string; value: string | number };
export type AutomationAction = { target_type: 'role' | 'user'; target_value: string; title_template: string; due_in_days: number };
export type AutomationDefinition = { name: string; trigger: string; conditions: AutomationCondition[]; action: AutomationAction; mode: 'automatic' | 'approval' };
export type AutomationRule = AutomationDefinition & { id: string; version_id: string; version: number; active: boolean; start_at: string | null; cursor_at: string | null; created_at: string };
export type AutomationExecution = { id: string; rule_id: string; version_id: string; event_type: string; event_id: string; occurred_at: string; event: Record<string, unknown>; conditions: Array<{ field: string; operator: string; expected: unknown; actual: unknown; matched: boolean }>; action: AutomationAction; status: string; attempt_count: number; reason: string | null; result: { task_id?: string; title?: string } | null; decided_by_id: string | null; decided_at: string | null; decision_reason: string | null; created_at: string; updated_at: string };
export const listAutomationRules = async () => (await api.get<AutomationRule[]>('/automations')).data;
export const createAutomationRule = async (body: AutomationDefinition) => (await api.post<AutomationRule>('/automations', body)).data;
export const updateAutomationRule = async (id: string, body: AutomationDefinition) => (await api.put<AutomationRule>(`/automations/${id}`, body)).data;
export const activateAutomation = async (id: string, start_at?: string) => (await api.post<AutomationRule>(`/automations/${id}/activate`, { ...(start_at ? { start_at } : {}) })).data;
export const stopAutomation = async (id: string) => (await api.post<AutomationRule>(`/automations/${id}/stop`)).data;
export const evaluateAutomations = async () => (await api.post<{ created: number; active_rules: number }>('/automations/evaluate')).data;
export const simulateAutomation = async (body: AutomationDefinition & { days: number }) => (await api.post<{ matches: number; events: Array<{ event_id: string; event_type: string; occurred_at: string; title: string; conditions: AutomationExecution['conditions'] }>; days: number }>('/automations/simulate', body)).data;
export const simulateSavedAutomation = async (id: string, days: number) => (await api.post<{ matches: number; events: Array<{ event_id: string; event_type: string; occurred_at: string; title: string; conditions: AutomationExecution['conditions'] }>; days: number }>(`/automations/${id}/simulate`, { days })).data;
export const listAutomationExecutions = async (id: string) => (await api.get<AutomationExecution[]>(`/automations/${id}/executions`)).data;
export const listPendingAutomations = async () => (await api.get<AutomationExecution[]>('/automations/executions/pending')).data;
export const decideAutomation = async (id: string, decision: 'approve' | 'reject', reason?: string) => (await api.post<AutomationExecution>(`/automations/executions/${id}/decision`, { decision, reason })).data;
