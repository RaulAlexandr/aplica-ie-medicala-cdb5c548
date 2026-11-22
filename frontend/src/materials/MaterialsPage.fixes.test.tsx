// INT4: lists refresh after a mutation, and errors appear next to the control that was used (same D1 rule as T17/T21).
import TestRenderer, { act } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { getClinic, listProcedures } from '../api/client';
import { inventoryOverview } from '../inventory/api';
import { confirmMaterials, getActuals, getRecipe, getRequirements, getReplenishment, reserveMaterial, saveRecipe } from './api';
import { MaterialsPage } from './MaterialsPage';

vi.mock('../api/client', () => ({ getClinic: vi.fn(), listProcedures: vi.fn(), apiErrorMessage: (e: unknown) => (e as { delegated?: string } | null)?.delegated ?? (e instanceof Error ? e.message : String(e)) }));
vi.mock('../inventory/api', () => ({ inventoryOverview: vi.fn() }));
vi.mock('./api', () => ({ getActuals: vi.fn(), getRecipe: vi.fn(), getRequirements: vi.fn(), getReplenishment: vi.fn(), releaseReservation: vi.fn(), reserveMaterial: vi.fn(), confirmMaterials: vi.fn(), saveRecipe: vi.fn() }));

const line = { item_id: 'item-1', item_name: 'Material test', unit: 'buc', estimated_quantity: '2.000', available: '5.000', reserved: '0.000', shortage: '0.000' };
const requirements = {
  from_date: '2026-12-01', to_date: '2026-12-14', timezone: 'Europe/Bucharest',
  appointments: [{ id: 'appt-1', starts_at: '2026-12-02T09:00:00+02:00', local_date: '2026-12-02', status: 'scheduled', patient_name: 'Ana Test',
    procedures: [{ procedure_name: 'Procedură test', recipe_version: 1, recipe_missing: false, performed_procedure_id: null, lines: [line] }] }],
  unknown: { appointments_without_procedure: { count: 0, appointments: [] }, procedures_without_recipe: { count: 0, procedures: [] } },
  shortages: [], needed_by_item: {}, performed_procedures: [{ id: 'performed-1', patient_name: 'Ana Test', procedure_name: 'Procedură test', status: 'completed', consumption_confirmed: false, recipe_version: 1, lines: [{ ...line }] }],
};
const stock = { items: [{ id: 'item-1', name: 'Material test', unit: 'buc', minimum_quantity: '0', quantity_total: '5', below_minimum: false, is_active: true }], locations: [{ id: 'loc-1', name: 'Depozit', is_active: true }], lots: [{ id: 'lot-1', item_id: 'item-1', item: 'Material test', code: 'L1', expires_on: null, expired: false }], balances: [] };

const text = (node: TestRenderer.ReactTestInstance): string => node.children.map(x => (typeof x === 'string' ? x : text(x))).join('');
const flush = async () => { for (let i = 0; i < 5; i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); }); };
const scrolled = vi.fn();
async function mount(role: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  let tree!: TestRenderer.ReactTestRenderer;
  await act(async () => { tree = TestRenderer.create(<QueryClientProvider client={client}><MaterialsPage role={role} /></QueryClientProvider>, { createNodeMock: () => ({ scrollIntoView: scrolled }) }); });
  await flush();
  return tree;
}
const alerts = (tree: TestRenderer.ReactTestRenderer) => tree.root.findAll(n => typeof n.type === 'string' && n.props.role === 'alert');
const statuses = (tree: TestRenderer.ReactTestRenderer) => tree.root.findAll(n => typeof n.type === 'string' && n.props.role === 'status');
const up = (node: TestRenderer.ReactTestInstance, type: string) => { let n: TestRenderer.ReactTestInstance | null = node; while (n && n.type !== type) n = n.parent; return n; };
const buttonNamed = (tree: TestRenderer.ReactTestRenderer, name: string) => tree.root.findAllByType('button').find(b => text(b) === name)!;

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getClinic).mockResolvedValue({ timezone: 'Europe/Bucharest' } as never);
  vi.mocked(listProcedures).mockResolvedValue([{ id: 'proc-1', name: 'Procedură test' }] as never);
  vi.mocked(inventoryOverview).mockResolvedValue(stock as never);
  vi.mocked(getRequirements).mockResolvedValue(requirements as never);
  vi.mocked(getReplenishment).mockResolvedValue({ items: [] } as never);
  vi.mocked(getActuals).mockResolvedValue({ lines: [] } as never);
  vi.mocked(getRecipe).mockResolvedValue({ procedure_catalog_id: 'proc-1', procedure_name: 'Procedură test', version: 0, lines: [] } as never);
});

describe('MaterialsPage: lists refresh after a mutation', () => {
  it('reloads requirements, replenishment and actuals after a reservation', async () => {
    vi.mocked(reserveMaterial).mockResolvedValue({} as never);
    const tree = await mount('doctor');
    const before = [vi.mocked(getRequirements).mock.calls.length, vi.mocked(getReplenishment).mock.calls.length, vi.mocked(getActuals).mock.calls.length];
    await act(async () => { buttonNamed(tree, 'Rezervă necesarul').props.onClick(); });
    await flush();
    expect(vi.mocked(getRequirements).mock.calls.length).toBe(before[0] + 1);
    expect(vi.mocked(getReplenishment).mock.calls.length).toBe(before[1] + 1);
    expect(vi.mocked(getActuals).mock.calls.length).toBe(before[2] + 1);
    await act(async () => tree.unmount());
  });

  it('reloads the lists after a confirmed consumption', async () => {
    vi.mocked(confirmMaterials).mockResolvedValue({} as never);
    const tree = await mount('doctor');
    const select = tree.root.findAllByType('select').find(s => s.parent?.children[0] === 'Manoperă')!;
    await act(async () => select.props.onChange({ target: { value: 'performed-1' } }));
    const selects = tree.root.findAllByType('form')[0].findAllByType('select');
    await act(async () => { selects[1].props.onChange({ target: { value: 'lot-1' } }); selects[2].props.onChange({ target: { value: 'loc-1' } }); });
    const before = vi.mocked(getActuals).mock.calls.length;
    await act(async () => { tree.root.findAllByType('form')[0].props.onSubmit({ preventDefault() {} }); });
    await flush();
    expect(confirmMaterials).toHaveBeenCalledTimes(1);
    expect(vi.mocked(getActuals).mock.calls.length).toBe(before + 1);
    await act(async () => tree.unmount());
  });

  it('reloads the lists after saving a recipe', async () => {
    vi.mocked(saveRecipe).mockResolvedValue({} as never);
    vi.mocked(getRecipe).mockResolvedValue({ procedure_catalog_id: 'proc-1', procedure_name: 'Procedură test', version: 1, lines: [{ item_id: 'item-1', estimated_quantity: '1.000' }] } as never);
    const tree = await mount('clinic_manager');
    await act(async () => tree.root.findAllByType('select')[0].props.onChange({ target: { value: 'proc-1' } }));
    await flush();
    const before = vi.mocked(getRequirements).mock.calls.length;
    const form = tree.root.findAllByType('form').find(f => text(f).includes('Salvează versiunea'))!;
    await act(async () => { form.props.onSubmit({ preventDefault() {} }); });
    await flush();
    expect(saveRecipe).toHaveBeenCalledTimes(1);
    expect(vi.mocked(getRequirements).mock.calls.length).toBeGreaterThan(before);
    await act(async () => tree.unmount());
  });
});

describe('MaterialsPage: errors next to the control (D1 rule)', () => {
  it('shows a refused reservation as a red alert next to its button, not as a top status', async () => {
    vi.mocked(reserveMaterial).mockRejectedValue(new Error('Stoc insuficient pentru rezervare.'));
    const tree = await mount('doctor');
    const button = buttonNamed(tree, 'Rezervă necesarul');
    await act(async () => { button.props.onClick(); });
    await flush();
    const found = alerts(tree).filter(a => text(a).includes('Stoc insuficient pentru rezervare.'));
    expect(found).toHaveLength(1);
    expect(found[0].props.className).toContain('error');
    expect(up(found[0], 'article')).toBe(up(button, 'article'));
    expect(statuses(tree).some(s => text(s).includes('Stoc insuficient'))).toBe(false);
    expect(scrolled).toHaveBeenCalledWith({ block: 'nearest' });
    await act(async () => tree.unmount());
  });

  it('shows a refused consumption inside the consumption form and keeps the entered values', async () => {
    vi.mocked(confirmMaterials).mockRejectedValue(new Error('Stoc insuficient'));
    const tree = await mount('doctor');
    const select = tree.root.findAllByType('select').find(s => s.parent?.children[0] === 'Manoperă')!;
    await act(async () => select.props.onChange({ target: { value: 'performed-1' } }));
    const form = tree.root.findAllByType('form')[0];
    const selects = form.findAllByType('select');
    await act(async () => { selects[1].props.onChange({ target: { value: 'lot-1' } }); selects[2].props.onChange({ target: { value: 'loc-1' } }); });
    await act(async () => form.findAllByType('input').find(i => i.props.type === 'number')!.props.onChange({ target: { value: '7' } }));
    await act(async () => { form.props.onSubmit({ preventDefault() {} }); });
    await flush();
    const found = alerts(tree).filter(a => text(a).includes('Stoc insuficient'));
    expect(found).toHaveLength(1);
    expect(up(found[0], 'form')).toBe(tree.root.findAllByType('form')[0]);
    expect(statuses(tree).some(s => text(s).includes('Stoc insuficient'))).toBe(false);
    expect(tree.root.findAllByType('form')[0].findAllByType('input').find(i => i.props.type === 'number')!.props.value).toBe('7');
    await act(async () => tree.unmount());
  });

  it('shows a refused recipe save inside the recipe form', async () => {
    vi.mocked(saveRecipe).mockRejectedValue(new Error('Rețeta s-a schimbat simultan.'));
    const tree = await mount('clinic_manager');
    await act(async () => tree.root.findAllByType('select')[0].props.onChange({ target: { value: 'proc-1' } }));
    await flush();
    const form = tree.root.findAllByType('form').find(f => text(f).includes('Salvează versiunea'))!;
    await act(async () => { form.props.onSubmit({ preventDefault() {} }); });
    await flush();
    const found = alerts(tree).filter(a => text(a).includes('Rețeta s-a schimbat simultan.'));
    expect(found).toHaveLength(1);
    expect(up(found[0], 'form')).toBe(tree.root.findAllByType('form').find(f => text(f).includes('Salvează versiunea')));
    await act(async () => tree.unmount());
  });

  it('delegates validation details to apiErrorMessage (no raw English text)', async () => {
    const detail = [{ type: 'string_too_long', msg: 'String should have at most 5 characters', ctx: { max_length: 5 } }];
    vi.mocked(saveRecipe).mockRejectedValue({ response: { data: { detail } }, delegated: 'Textul este prea lung (maximum 5 caractere).' });
    const tree = await mount('clinic_manager');
    await act(async () => tree.root.findAllByType('select')[0].props.onChange({ target: { value: 'proc-1' } }));
    await flush();
    const form = tree.root.findAllByType('form').find(f => text(f).includes('Salvează versiunea'))!;
    await act(async () => { form.props.onSubmit({ preventDefault() {} }); });
    await flush();
    expect(alerts(tree).map(text).join(' ')).toContain('Textul este prea lung (maximum 5 caractere).');
    expect(alerts(tree).map(text).join(' ')).not.toContain('String should have');
    await act(async () => tree.unmount());
  });

  it('clears the error when the next attempt succeeds and shows the success as a status', async () => {
    vi.mocked(reserveMaterial).mockRejectedValueOnce(new Error('Stoc insuficient pentru rezervare.')).mockResolvedValue({} as never);
    const tree = await mount('doctor');
    await act(async () => { buttonNamed(tree, 'Rezervă necesarul').props.onClick(); });
    await flush();
    expect(alerts(tree)).toHaveLength(1);
    await act(async () => { buttonNamed(tree, 'Rezervă necesarul').props.onClick(); });
    await flush();
    expect(alerts(tree)).toHaveLength(0);
    expect(statuses(tree).some(s => text(s).includes('Rezervarea a fost înregistrată.'))).toBe(true);
    await act(async () => tree.unmount());
  });
});
