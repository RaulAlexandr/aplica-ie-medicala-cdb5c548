// H3 D1: a rejected save shows a red role="alert" next to the form that was submitted, not at the top of the page.
import TestRenderer, { act } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as clientApi from '../api/client';
import type { User } from '../api/client';
import * as equipmentApi from './api';
import type { Equipment, EquipmentHistory } from './api';
import { EquipmentPage } from './EquipmentPage';

const equipment: Equipment = { id: 'equipment-1', name: 'Unit fictiv', equipment_type: 'unit dentar', serial_number: null, room_id: null, responsible_id: 'manager-1', state: 'available', is_active: true };
const manager: User = { id: 'manager-1', clinic_id: 'clinic-1', full_name: 'Manager Fictiv', email: 'm@example.test', role: 'clinic_manager' };
const text = (node: TestRenderer.ReactTestInstance): string => node.children.map(child => (typeof child === 'string' ? child : text(child))).join('');
async function waitFor(condition: () => boolean, description: string, timeoutMs = 2_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    let ready = false;
    await act(async () => { ready = condition(); if (!ready) await new Promise(resolve => setTimeout(resolve, 5)); });
    if (ready) return;
  }
  throw new Error(`Timed out waiting for ${description}`);
}
const history = (values: Partial<EquipmentHistory> = {}): EquipmentHistory => ({ checks: [], documents: [], dependencies: [], unavailabilities: [], ...values });

const scrolled = vi.fn();
async function render(historyValue: EquipmentHistory = history()) {
  vi.spyOn(clientApi, 'getClinic').mockResolvedValue({ timezone: 'Europe/Bucharest' } as never);
  vi.spyOn(clientApi, 'listRooms').mockResolvedValue([]);
  vi.spyOn(clientApi, 'listStaff').mockResolvedValue([]);
  vi.spyOn(clientApi, 'listProcedures').mockResolvedValue([]);
  vi.spyOn(equipmentApi, 'listEquipment').mockResolvedValue([equipment]);
  vi.spyOn(equipmentApi, 'listDueChecks').mockResolvedValue([]);
  const historyRequest = vi.spyOn(equipmentApi, 'equipmentHistory').mockResolvedValue(historyValue);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
  let renderer!: TestRenderer.ReactTestRenderer;
  await act(async () => { renderer = TestRenderer.create(<QueryClientProvider client={client}><EquipmentPage user={manager} /></QueryClientProvider>, { createNodeMock: () => ({ scrollIntoView: scrolled }) }); });
  let row = renderer.root.findAllByType('button').find(button => text(button).includes(equipment.name));
  await waitFor(() => { row = renderer.root.findAllByType('button').find(button => text(button).includes(equipment.name)); return Boolean(row); }, 'equipment row');
  await act(async () => { row!.props.onClick(); });
  await waitFor(() => historyRequest.mock.calls.length > 0 && !text(renderer.root).includes('Se încarcă istoricul…'), 'equipment history');
  return renderer;
}
const alerts = (renderer: TestRenderer.ReactTestRenderer, needle: string) => renderer.root.findAll(node => typeof node.type === 'string' && node.props.role === 'alert' && text(node).includes(needle));
const ancestor = (node: TestRenderer.ReactTestInstance, type: string) => { let current: TestRenderer.ReactTestInstance | null = node; while (current && current.type !== type) current = current.parent; return current; };
const formWith = (renderer: TestRenderer.ReactTestRenderer, heading: string) => renderer.root.findAllByType('form').find(form => text(form).includes(heading))!;

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); scrolled.mockClear(); });

describe('equipment: errors next to the form (D1)', () => {
  const server = 'Rezultatul se completează numai pentru o verificare efectuată';
  it.each([
    ['check', 'Service / verificare periodică', 'addCheck', { check_type: 'service', due_at: '2026-12-01T10:00', completed_at: '', result: 'x' }],
    ['document', 'Document (referință)', 'addDocument', { title: 'Titlu', document_type: 'manual', reference: 'doc://fictiv' }],
    ['outage', 'Raportează indisponibilitate', 'reportOutage', { reason: 'Defect', starts_at: '2026-12-01T10:00', ends_at: '2026-12-01T11:00' }],
    ['dependency', 'Dependență', 'addDependency', { room_id: 'room-1', procedure_id: '' }],
  ] as const)('puts the %s error inside its own form and nowhere else', async (kind, heading, apiName, values) => {
    vi.spyOn(equipmentApi, apiName as 'addCheck').mockRejectedValue(new Error(server));
    vi.stubGlobal('FormData', class { get(name: string) { return (values as Record<string, string>)[name] ?? ''; } });
    const renderer = await render();
    if (kind === 'dependency') act(() => renderer.root.findByProps({ 'aria-label': 'Tipul dependenței' }).props.onChange({ target: { value: 'room' } }));
    const form = formWith(renderer, heading);
    const target = { reset: vi.fn() };
    await act(async () => { form.props.onSubmit({ preventDefault() {}, currentTarget: target }); });
    await waitFor(() => alerts(renderer, server).length === 1, `${kind} submit error`);
    const found = alerts(renderer, server);
    expect(found).toHaveLength(1);
    expect(found[0].props.className).toContain('error');
    expect(ancestor(found[0], 'form')).toBe(form);
    expect(target.reset).not.toHaveBeenCalled();
    await act(async () => renderer.unmount());
  });

  it('shows the error of one form and clears it when another form is submitted successfully', async () => {
    const reject = vi.spyOn(equipmentApi, 'addDocument').mockRejectedValue(new Error('Titlul este prea lung'));
    vi.spyOn(equipmentApi, 'addCheck').mockResolvedValue({} as never);
    vi.stubGlobal('FormData', class { get(name: string) { return ({ title: 'T', document_type: 'm', reference: 'r', check_type: 'service', due_at: '2026-12-01T10:00', completed_at: '', result: '' } as Record<string, string>)[name] ?? ''; } });
    const renderer = await render();
    await act(async () => { formWith(renderer, 'Document (referință)').props.onSubmit({ preventDefault() {}, currentTarget: { reset: vi.fn() } }); });
    await waitFor(() => alerts(renderer, 'Titlul este prea lung').length === 1, 'document error');
    expect(alerts(renderer, 'Titlul este prea lung')).toHaveLength(1);
    await act(async () => { formWith(renderer, 'Service / verificare periodică').props.onSubmit({ preventDefault() {}, currentTarget: { reset: vi.fn() } }); });
    await waitFor(() => alerts(renderer, 'Titlul este prea lung').length === 0, 'document error clear');
    expect(alerts(renderer, 'Titlul este prea lung')).toHaveLength(0);
    expect(reject).toHaveBeenCalledTimes(1);
    await act(async () => renderer.unmount());
  });

  it('shows a rejected close next to that outage and a rejected completion next to that check', async () => {
    vi.spyOn(equipmentApi, 'closeOutage').mockRejectedValue(new Error('Indisponibilitatea este deja închisă'));
    vi.spyOn(equipmentApi, 'completeCheck').mockRejectedValue(new Error('Verificarea este deja efectuată'));
    const outage = { id: 'outage-1', equipment_id: 'equipment-1', reason: 'Defect fictiv', starts_at: '2026-12-01T10:00:00Z', ends_at: '2026-12-01T12:00:00Z', closed_at: null, affected_appointments: [] };
    const check = { id: 'check-1', equipment_id: 'equipment-1', check_type: 'service', due_at: '2026-12-02T10:00:00Z', completed_at: null, result: null, author_id: 'manager-1' };
    const renderer = await render(history({ unavailabilities: [outage], checks: [check] } as never));
    const closeButton = renderer.root.findAllByType('button').find(button => text(button) === 'Închide indisponibilitatea')!;
    await act(async () => { closeButton.props.onClick(); });
    await waitFor(() => alerts(renderer, 'Indisponibilitatea este deja închisă').length === 1, 'outage close error');
    const closeAlert = alerts(renderer, 'Indisponibilitatea este deja închisă');
    expect(closeAlert).toHaveLength(1);
    expect(text(ancestor(closeAlert[0], 'article')!)).toContain('Defect fictiv');
    const completeForm = renderer.root.findAllByType('form').find(form => String(form.props['aria-label'] ?? '').startsWith('Completează verificarea'))!;
    await act(async () => { completeForm.findByType('input').props.onChange({ target: { value: 'ok' } }); });
    await act(async () => { completeForm.props.onSubmit({ preventDefault() {} }); });
    await waitFor(() => alerts(renderer, 'Verificarea este deja efectuată').length === 1, 'check completion error');
    const completeAlert = alerts(renderer, 'Verificarea este deja efectuată');
    expect(completeAlert).toHaveLength(1);
    expect(ancestor(completeAlert[0], 'article')).toBe(ancestor(completeForm, 'article'));
    await act(async () => renderer.unmount());
  });

  it('no longer renders the error above the register form', async () => {
    vi.spyOn(equipmentApi, 'addDocument').mockRejectedValue(new Error('Eroare document'));
    vi.stubGlobal('FormData', class { get(name: string) { return ({ title: 'T', document_type: 'm', reference: 'r' } as Record<string, string>)[name] ?? ''; } });
    const renderer = await render();
    await act(async () => { formWith(renderer, 'Document (referință)').props.onSubmit({ preventDefault() {}, currentTarget: { reset: vi.fn() } }); });
    await waitFor(() => alerts(renderer, 'Eroare document').length === 1, 'top error removed');
    const found = alerts(renderer, 'Eroare document');
    expect(found).toHaveLength(1);
    expect(ancestor(found[0], 'form')).not.toBeNull();
    await act(async () => renderer.unmount());
  });

  it('brings a new error into view, because the form may sit at the bottom of the screen', async () => {
    vi.spyOn(equipmentApi, 'addDocument').mockRejectedValue(new Error('Eroare document'));
    vi.stubGlobal('FormData', class { get(name: string) { return ({ title: 'T', document_type: 'm', reference: 'r' } as Record<string, string>)[name] ?? ''; } });
    const renderer = await render();
    scrolled.mockClear();
    await act(async () => { formWith(renderer, 'Document (referință)').props.onSubmit({ preventDefault() {}, currentTarget: { reset: vi.fn() } }); });
    await waitFor(() => scrolled.mock.calls.length === 1, 'error scroll');
    expect(scrolled).toHaveBeenCalledWith({ block: 'nearest' });
    await act(async () => renderer.unmount());
  });
});
