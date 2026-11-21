// H3 D1: errors appear next to the submitted form, red, role="alert"; success stays a role="status" message.
import TestRenderer, { act } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { addInventoryItem, addInventoryLocation, addInventoryMovement, inventoryHistory, inventoryOverview, reverseInventoryMovement } from './api';
import { InventoryPage } from './InventoryPage';

const overview = {
  items: [{ id: 'item-1', name: 'Material fictiv', unit: 'buc', minimum_quantity: '2', quantity_total: '5', below_minimum: false, is_active: true }],
  locations: [{ id: 'loc-1', name: 'Depozit fictiv', is_active: true }],
  lots: [{ id: 'lot-1', item_id: 'item-1', item: 'Material fictiv', code: 'LOT-FICTIV', expires_on: null, expired: false }],
  balances: [],
};
const movementRow = { id: 'move-1', item_id: 'item-1', item: 'Material fictiv', lot_id: 'lot-1', lot: 'LOT-FICTIV', location_id: 'loc-1', location: 'Depozit fictiv', author: 'Manager fictiv', kind: 'receipt', quantity: '1', reason: '', transfer_id: null, reverses_movement_id: null, is_correction: false, is_corrected: false, created_at: '2026-11-21T10:00:00Z' };

vi.mock('./api', () => ({
  inventoryOverview: vi.fn(), inventoryHistory: vi.fn(), addInventoryItem: vi.fn(), addInventoryLocation: vi.fn(), addInventoryLot: vi.fn(),
  addInventoryMovement: vi.fn(), reverseInventoryMovement: vi.fn(), updateInventoryItem: vi.fn(), updateInventoryLocation: vi.fn(),
}));
vi.mock('../api/client', () => ({ apiErrorMessage: (e: unknown) => (e instanceof Error ? e.message : 'Eroare'), getClinic: async () => ({ timezone: 'Europe/Bucharest' }) }));

const textOf = (node: TestRenderer.ReactTestInstance): string => node.children.map(x => (typeof x === 'string' ? x : textOf(x))).join('');
const flush = () => new Promise(resolve => setTimeout(resolve, 0));
const scrolled = vi.fn();
async function page(role: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  let renderer!: TestRenderer.ReactTestRenderer;
  await act(async () => { renderer = TestRenderer.create(<QueryClientProvider client={client}><InventoryPage role={role} /></QueryClientProvider>, { createNodeMock: () => ({ scrollIntoView: scrolled }) }); await flush(); });
  await act(async () => { await flush(); });
  return renderer;
}
const alerts = (renderer: TestRenderer.ReactTestRenderer) => renderer.root.findAll(node => typeof node.type === 'string' && node.props.role === 'alert');
const statuses = (renderer: TestRenderer.ReactTestRenderer) => renderer.root.findAll(node => typeof node.type === 'string' && node.props.role === 'status');
const formOf = (node: TestRenderer.ReactTestInstance) => { let current: TestRenderer.ReactTestInstance | null = node; while (current && current.type !== 'form') current = current.parent; return current; };
const formWithHeading = (renderer: TestRenderer.ReactTestRenderer, heading: string) => renderer.root.findAllByType('form').find(form => form.findAllByType('h2').some(h => textOf(h) === heading))!;
const submit = (form: TestRenderer.ReactTestInstance) => form.props.onSubmit({ preventDefault() {} });

beforeEach(() => {
  vi.clearAllMocks();
  scrolled.mockClear();
  vi.mocked(inventoryOverview).mockResolvedValue(overview as never);
  vi.mocked(inventoryHistory).mockResolvedValue([]);
});
afterEach(() => vi.unstubAllGlobals());

describe('inventory: errors next to the form (D1)', () => {
  it('shows a rejected movement as a red role="alert" inside the movement form, not as a status', async () => {
    vi.mocked(addInventoryMovement).mockRejectedValue(new Error('Lot expirat; consumul este interzis'));
    const renderer = await page('doctor');
    const form = formWithHeading(renderer, 'Înregistrează mișcare');
    const selects = form.findAllByType('select');
    await act(async () => { selects[1].props.onChange({ target: { value: 'item-1' } }); selects[2].props.onChange({ target: { value: 'lot-1' } }); selects[3].props.onChange({ target: { value: 'loc-1' } }); });
    const quantity = form.findAllByType('input').find(x => x.props.type === 'number')!;
    await act(async () => quantity.props.onChange({ target: { value: '3' } }));
    await act(async () => { submit(form); await flush(); });
    const found = alerts(renderer).filter(a => textOf(a).includes('Lot expirat; consumul este interzis'));
    expect(found).toHaveLength(1);
    expect(found[0].props.className).toContain('error');
    expect(formOf(found[0])).toBe(form);
    expect(statuses(renderer).some(s => textOf(s).includes('Lot expirat'))).toBe(false);
    expect(form.findAllByType('input').find(x => x.props.type === 'number')?.props.value).toBe('3');
    await act(async () => renderer.unmount());
  });

  it('keeps success as a role="status" message and clears the previous error', async () => {
    vi.mocked(addInventoryMovement).mockRejectedValueOnce(new Error('Stoc insuficient')).mockResolvedValue({} as never);
    const renderer = await page('doctor');
    const form = formWithHeading(renderer, 'Înregistrează mișcare');
    const selects = form.findAllByType('select');
    await act(async () => { selects[1].props.onChange({ target: { value: 'item-1' } }); selects[2].props.onChange({ target: { value: 'lot-1' } }); selects[3].props.onChange({ target: { value: 'loc-1' } }); });
    await act(async () => form.findAllByType('input').find(x => x.props.type === 'number')!.props.onChange({ target: { value: '1' } }));
    await act(async () => { submit(form); await flush(); });
    expect(alerts(renderer).some(a => textOf(a).includes('Stoc insuficient'))).toBe(true);
    await act(async () => { submit(form); await flush(); });
    expect(alerts(renderer).filter(a => textOf(a).includes('Stoc insuficient'))).toHaveLength(0);
    const ok = statuses(renderer).filter(s => textOf(s).includes('Mișcarea a fost salvată.'));
    expect(ok).toHaveLength(1);
    expect(ok[0].props.className).not.toContain('error');
    await act(async () => renderer.unmount());
  });

  it('shows a rejected new item inside the item form', async () => {
    vi.mocked(addInventoryItem).mockRejectedValue(new Error('Articolul există deja.'));
    const renderer = await page('clinic_manager');
    const form = formWithHeading(renderer, 'Articol nou');
    await act(async () => { submit(form); await flush(); });
    const found = alerts(renderer).filter(a => textOf(a).includes('Articolul există deja.'));
    expect(found).toHaveLength(1);
    expect(formOf(found[0])).toBe(form);
    await act(async () => renderer.unmount());
  });

  it('shows a rejected new location only in the location form', async () => {
    vi.mocked(addInventoryLocation).mockRejectedValue(new Error('Locația există deja.'));
    const renderer = await page('clinic_manager');
    const form = formWithHeading(renderer, 'Locație nouă');
    await act(async () => { submit(form); await flush(); });
    const found = alerts(renderer).filter(a => textOf(a).includes('Locația există deja.'));
    expect(found).toHaveLength(1);
    expect(formOf(found[0])).toBe(form);
    expect(textOf(formWithHeading(renderer, 'Articol nou'))).not.toContain('Locația există deja.');
    await act(async () => renderer.unmount());
  });

  it('shows a rejected correction next to the history', async () => {
    vi.stubGlobal('window', { prompt: () => 'Motiv fictiv' });
    vi.mocked(inventoryHistory).mockResolvedValue([movementRow] as never);
    vi.mocked(reverseInventoryMovement).mockRejectedValue(new Error('Mișcarea a fost deja corectată'));
    const renderer = await page('clinic_manager');
    const button = renderer.root.findAllByType('button').find(x => textOf(x) === 'Corectează')!;
    await act(async () => { button.props.onClick(); await flush(); await flush(); });
    const found = alerts(renderer).filter(a => textOf(a).includes('Mișcarea a fost deja corectată'));
    expect(found).toHaveLength(1);
    const section = (() => { let n: TestRenderer.ReactTestInstance | null = found[0]; while (n && !(n.type === 'section')) n = n.parent; return n; })();
    expect(section && textOf(section.findAllByType('h2')[0])).toBe('Istoricul mișcărilor');
    await act(async () => renderer.unmount());
  });

  it('brings a new error into view, because the form may sit at the bottom of the screen', async () => {
    vi.mocked(addInventoryItem).mockRejectedValue(new Error('Articolul există deja.'));
    const renderer = await page('clinic_manager');
    await act(async () => { submit(formWithHeading(renderer, 'Articol nou')); await flush(); });
    expect(scrolled).toHaveBeenCalledWith({ block: 'nearest' });
    await act(async () => renderer.unmount());
  });
});
