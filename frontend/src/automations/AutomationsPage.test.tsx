import TestRenderer, { act } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as clientApi from '../api/client';
import { AutomationsPage } from './AutomationsPage';
import * as automationApi from './api';
import type { AutomationExecution, AutomationRule } from './api';

vi.mock('./api', async importOriginal => {
  const actual = await importOriginal<typeof import('./api')>();
  return { ...actual, listAutomationRules: vi.fn(), createAutomationRule: vi.fn(), updateAutomationRule: vi.fn(), activateAutomation: vi.fn(), stopAutomation: vi.fn(), evaluateAutomations: vi.fn(), simulateAutomation: vi.fn(), simulateSavedAutomation: vi.fn(), listAutomationExecutions: vi.fn(), listPendingAutomations: vi.fn(), decideAutomation: vi.fn() };
});

const rule = { id: 'rule-1', version_id: 'version-1', version: 1, name: 'Regulă fictivă', trigger: 'appointment_created', conditions: [], action: { target_type: 'role' as const, target_value: 'doctor', title_template: 'Verifică {{appointment.type}}', due_in_days: 2 }, mode: 'approval' as const, active: false, start_at: null, cursor_at: null, created_at: '2026-01-01T00:00:00Z' };
const task = { id: 'run-1', rule_id: 'rule-1', version_id: 'version-1', event_type: 'appointment_created', event_id: 'event-1', occurred_at: '2026-01-01T00:00:00Z', event: {}, conditions: [], action: rule.action, status: 'pending_approval', attempt_count: 0, reason: null, result: null, decided_by_id: null, decided_at: null, decision_reason: null, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' };
const text = (node: TestRenderer.ReactTestInstance) => node.children.filter((child): child is string => typeof child === 'string').join('');

async function render(rules: AutomationRule[] = [], pending: AutomationExecution[] = []) {
  vi.mocked(automationApi.listAutomationRules).mockResolvedValue(rules as never);
  vi.mocked(automationApi.listPendingAutomations).mockResolvedValue(pending as never);
  vi.spyOn(clientApi, 'listStaff').mockResolvedValue([]);
  vi.spyOn(clientApi, 'listRooms').mockResolvedValue([]);
  vi.spyOn(clientApi, 'listProcedures').mockResolvedValue([]);
  vi.mocked(automationApi.listAutomationExecutions).mockResolvedValue([]);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  let renderer!: TestRenderer.ReactTestRenderer;
  await act(async () => { renderer = TestRenderer.create(<QueryClientProvider client={queryClient}><AutomationsPage /></QueryClientProvider>); await new Promise(resolve => setTimeout(resolve, 5)); });
  return renderer;
}

afterEach(() => vi.restoreAllMocks());

describe('AutomationsPage', () => {
  it('creates a rule and previews it without writing an execution', async () => {
    vi.mocked(automationApi.createAutomationRule).mockResolvedValue(rule as never);
    vi.mocked(automationApi.simulateAutomation).mockResolvedValue({ matches: 1, days: 30, events: [{ event_id: 'event-1', event_type: 'appointment_created', occurred_at: '2026-01-01T00:00:00Z', title: 'Verifică Consultație', conditions: [] }] });
    const renderer = await render();
    const inputs = () => renderer.root.findAllByType('input');
    await act(async () => { inputs()[0].props.onChange({ target: { value: 'Regulă nouă fictivă' } }); });
    await act(async () => { renderer.root.findAllByType('form')[0].props.onSubmit({ preventDefault() {} }); await new Promise(resolve => setTimeout(resolve, 5)); });
    expect(automationApi.createAutomationRule).toHaveBeenCalledWith(expect.objectContaining({ name: 'Regulă nouă fictivă', trigger: 'appointment_created', mode: 'approval' }));
    await act(async () => { renderer.root.findAllByType('button').find(button => text(button) === 'Simulează ultimele 30 de zile')!.props.onClick(); await new Promise(resolve => setTimeout(resolve, 5)); });
    expect(automationApi.simulateAutomation).toHaveBeenCalledWith(expect.objectContaining({ days: 30 }));
    expect(renderer.root.findAll(node => node.props?.['aria-live'] === 'polite')).toHaveLength(1);
    expect(automationApi.createAutomationRule).toHaveBeenCalledTimes(1);
    renderer.unmount();
  });

  it('shows rule versions, stop/activate controls, run history and pending approval actions', async () => {
    vi.mocked(automationApi.decideAutomation).mockResolvedValue({ ...task, status: 'succeeded' } as never);
    vi.mocked(automationApi.listAutomationExecutions).mockResolvedValue([task as never]);
    const renderer = await render([rule], [task]);
    expect(renderer.root.findAllByType('button').some(button => text(button) === 'Activează')).toBe(true);
    await act(async () => { renderer.root.findAllByType('button').find(button => text(button) === 'Editează')!.props.onClick(); });
    expect(renderer.root.findAll(node => node.type === 'h2').some(node => text(node) === 'Editează v1 (salvarea creează o versiune nouă)')).toBe(true);
    expect(renderer.root.findAllByType('button').some(button => text(button) === 'Aprobă sarcina')).toBe(true);
    await act(async () => { renderer.root.findAllByType('button').find(button => text(button) === 'Aprobă sarcina')!.props.onClick(); await new Promise(resolve => setTimeout(resolve, 5)); });
    expect(automationApi.decideAutomation).toHaveBeenCalledWith('run-1', 'approve');
    renderer.unmount();
  });
});
