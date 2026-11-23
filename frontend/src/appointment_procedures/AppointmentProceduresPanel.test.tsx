import TestRenderer, { act } from 'react-test-renderer';
import { AxiosError } from 'axios';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as client from '../api/client';
import * as api from './api';
import { AppointmentProceduresPanel } from './AppointmentProceduresPanel';

const set = { appointment_id: 'visit', revision: 4, procedures: [
  { procedure_catalog_id: 'p1', code: 'OBT', name: 'Obturație', quantity: 2, manual_quantity: 1, plan_quantity: 2, source: 'both' as const },
  { procedure_catalog_id: 'p2', code: 'DET', name: 'Detartraj', quantity: 1, manual_quantity: null, plan_quantity: 1, source: 'plan' as const },
] };
function render(canManage: boolean, status = 'scheduled') { return TestRenderer.create(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AppointmentProceduresPanel appointmentId="visit" status={status} canManage={canManage} /></QueryClientProvider>); }
function label(node: TestRenderer.ReactTestInstance) { return node.children.filter((child): child is string => typeof child === 'string').join(''); }
async function waitFor(renderer: TestRenderer.ReactTestRenderer, predicate: () => boolean) { for (let attempt = 0; attempt < 100 && !predicate(); attempt += 1) await act(async () => { await new Promise(resolve => setTimeout(resolve, 5)); }); }
afterEach(() => vi.restoreAllMocks());

describe('appointment procedures panel', () => {
  it('shows source and keeps closed or unauthorized views read only', async () => {
    vi.spyOn(api, 'getAppointmentProcedures').mockResolvedValue(set);
    vi.spyOn(client, 'listProcedures').mockResolvedValue([]);
    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = render(false); });
    await waitFor(renderer!, () => renderer!.root.findAllByType('li').length === 2);
    expect(renderer.root.findAllByType('li').map(label)).toHaveLength(2);
    expect(renderer.root.findAllByType('button').map(label)).not.toContain('Salvează procedurile');
    expect(renderer.root.findAllByType('button').map(label)).not.toContain('Elimină');
    renderer.unmount();
    let closed!: TestRenderer.ReactTestRenderer;
    await act(async () => { closed = render(true, 'completed'); });
    await waitFor(closed!, () => closed!.root.findAllByType('li').length === 2);
    expect(closed.root.findAllByType('button').map(label)).not.toContain('Salvează procedurile');
    closed.unmount();
  });

  it('submits manual edits while preserving plan-only lines', async () => {
    vi.spyOn(api, 'getAppointmentProcedures').mockResolvedValue(set);
    vi.spyOn(client, 'listProcedures').mockResolvedValue([]);
    const replace = vi.spyOn(api, 'replaceAppointmentProcedures').mockResolvedValue({ ...set, revision: 5 });
    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = render(true); });
    await waitFor(renderer!, () => renderer!.root.findAllByProps({ 'aria-label': 'Cantitate Obturație' }).length === 1);
    const qty = renderer.root.findByProps({ 'aria-label': 'Cantitate Obturație' });
    await act(async () => { qty.props.onChange({ target: { value: '3' } }); });
    await act(async () => { renderer.root.findAllByType('button').find(button => label(button) === 'Salvează procedurile')!.props.onClick(); await Promise.resolve(); await Promise.resolve(); });
    expect(replace).toHaveBeenCalledWith('visit', 4, [{ procedure_catalog_id: 'p1', quantity: 3 }]);
    renderer.unmount();
  });

  it('keeps the draft after a server conflict and offers reload', async () => {
    vi.spyOn(api, 'getAppointmentProcedures').mockResolvedValue(set);
    vi.spyOn(client, 'listProcedures').mockResolvedValue([]);
    const conflict = new AxiosError('conflict');
    conflict.response = { status: 409, data: { detail: 'Revizie veche. Reîncărcați programarea.' } } as never;
    vi.spyOn(api, 'replaceAppointmentProcedures').mockRejectedValue(conflict);
    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = render(true); });
    await waitFor(renderer!, () => renderer!.root.findAllByProps({ 'aria-label': 'Cantitate Obturație' }).length === 1);
    await act(async () => { renderer!.root.findByProps({ 'aria-label': 'Cantitate Obturație' }).props.onChange({ target: { value: '3' } }); });
    await act(async () => { renderer!.root.findAllByType('button').find(button => label(button) === 'Salvează procedurile')!.props.onClick(); });
    await waitFor(renderer!, () => renderer!.root.findAllByProps({ role: 'alert' }).length === 1);
    expect(renderer!.root.findByProps({ 'aria-label': 'Cantitate Obturație' }).props.value).toBe(3);
    expect(renderer!.root.findByProps({ role: 'alert' }).children.join('')).toContain('Reîncărcați');
    expect(renderer!.root.findAllByType('button').map(label)).toContain('Reîncarcă programarea');
    renderer!.unmount();
  });

  it('keeps a newly added procedure visible and removable when catalog search changes', async () => {
    const empty = { appointment_id: 'visit', revision: 0, procedures: [] };
    const catalogProcedure = { id: 'p3', clinic_id: 'clinic', code: 'CONS', name: 'Consultație', description: null,
      category: null, default_duration_minutes: 20, base_price: '0.00', currency: 'RON', is_active: true,
      created_at: '', updated_at: '' };
    vi.spyOn(api, 'getAppointmentProcedures').mockResolvedValue(empty);
    const listCatalog = vi.spyOn(client, 'listProcedures').mockImplementation(async search => search ? [] : [catalogProcedure]);
    const replace = vi.spyOn(api, 'replaceAppointmentProcedures').mockResolvedValue({ ...empty, revision: 1,
      procedures: [{ procedure_catalog_id: 'p3', code: 'CONS', name: 'Consultație', quantity: 1, manual_quantity: 1, plan_quantity: null, source: 'manual' }] });
    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = render(true); });
    await waitFor(renderer!, () => renderer!.root.findAllByType('option').some(option => label(option).includes('Consultație')));
    await act(async () => { renderer!.root.findByProps({ 'aria-label': 'Procedură activă' }).props.onChange({ target: { value: 'p3' } }); });
    await act(async () => { renderer!.root.findAllByType('button').find(button => label(button) === 'Adaugă')!.props.onClick(); });
    expect(renderer!.root.findAllByType('li').some(item => label(item).includes('Consultație'))).toBe(true);
    await act(async () => { renderer!.root.findByProps({ 'aria-label': 'Caută procedura' }).props.onChange({ target: { value: 'inexistent' } }); });
    await waitFor(renderer!, () => listCatalog.mock.calls.some(([search]) => search === 'inexistent'));
    expect(renderer!.root.findAllByType('li').some(item => label(item).includes('Consultație'))).toBe(true);
    expect(renderer!.root.findAllByType('button').map(label)).toContain('Elimină');
    await act(async () => { renderer!.root.findAllByType('button').find(button => label(button) === 'Salvează procedurile')!.props.onClick(); });
    await waitFor(renderer!, () => replace.mock.calls.length === 1);
    expect(replace).toHaveBeenCalledWith('visit', 0, [{ procedure_catalog_id: 'p3', quantity: 1 }]);
    renderer!.unmount();
  });
});
