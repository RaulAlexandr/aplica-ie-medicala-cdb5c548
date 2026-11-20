import TestRenderer, { act } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AxiosError } from 'axios';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as financeApi from './api';
import type { AccountEntry, PatientAccount } from './api';
import { PatientAccountPanel } from './PatientAccountPanel';

const payment: AccountEntry = {
  id: 'entry-pay', kind: 'payment', amount: '300.00', currency: 'RON', method: 'card', reason: null, appointment_id: 'visit-1',
  reverses_entry_id: null, created_at: '2026-11-19T22:30:00+00:00', created_by: { id: 'u1', name: 'Recepție Fictivă' }, reversed_by: null,
};
const adjustment: AccountEntry = {
  ...payment, id: 'entry-adj', kind: 'adjustment_credit', amount: '20.00', method: null, reason: 'Gest comercial', appointment_id: null,
};
function account(permissions: PatientAccount['permissions'], overrides: Partial<PatientAccount> = {}): PatientAccount {
  return {
    patient_id: 'patient-1', currency: 'RON', timezone: 'Europe/Bucharest',
    totals: { services_completed: '250.00', payments: '300.00', refunds: '0.00', paid_net: '300.00', adjustments_debit: '0.00', adjustments_credit: '20.00', balance: '-70.00', balance_status: 'credit' },
    services: {
      visits: [{ appointment_id: 'visit-1', starts_at: '2026-12-01T09:00:00+00:00', appointment_type: 'Tratament', appointment_status: 'completed', completed_total: '250.00', procedures: [
        { id: 'p1', code: 'OBT', name: 'Obturație', tooth_fdi: '16', status: 'completed', counted: true, amount: '250.00', price_overridden: false },
        { id: 'p2', code: 'DET', name: 'Detartraj', tooth_fdi: null, status: 'running', counted: false, amount: null, price_overridden: false },
        { id: 'p3', code: 'RX', name: 'Radiografie', tooth_fdi: null, status: 'cancelled', counted: false, amount: null, price_overridden: false },
      ] }],
      in_progress_count: 1, cancelled_count: 1,
    },
    entries: [payment, adjustment], permissions, ...overrides,
  };
}
const manager = { can_record_cash: true, can_adjust: true };
const reception = { can_record_cash: true, can_adjust: false };
const doctor = { can_record_cash: false, can_adjust: false };

function text(node: TestRenderer.ReactTestInstance): string {
  return node.children.map(child => typeof child === 'string' ? child : text(child)).join('');
}
async function flush() { await new Promise(resolve => setTimeout(resolve, 20)); }
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((ok, fail) => { resolve = ok; reject = fail; });
  return { promise, resolve, reject };
}
async function render() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  let renderer!: TestRenderer.ReactTestRenderer;
  await act(async () => { renderer = TestRenderer.create(<QueryClientProvider client={client}><PatientAccountPanel patientId="patient-1" /></QueryClientProvider>); await flush(); });
  for (let attempt = 0; attempt < 50 && renderer.root.findAllByProps({ 'data-testid': 'account-balance' }).length === 0; attempt += 1) {
    await act(async () => { await flush(); });
  }
  return renderer;
}
const byLabel = (r: TestRenderer.ReactTestRenderer, label: string) => r.root.findByProps({ 'aria-label': label });
function change(r: TestRenderer.ReactTestRenderer, label: string, value: string) {
  act(() => byLabel(r, label).props.onChange({ target: { value } }));
}
function submitForm(r: TestRenderer.ReactTestRenderer) {
  return r.root.findByProps({ 'aria-label': 'Mișcare nouă' }).props.onSubmit({ preventDefault() {} });
}
const submitButton = (r: TestRenderer.ReactTestRenderer) => r.root.findByProps({ 'aria-label': 'Mișcare nouă' }).findByProps({ type: 'submit' });
const axiosError = (status: number, detail: string) => new AxiosError(detail, String(status), undefined, undefined, { status, data: { detail }, statusText: '', headers: {}, config: {} as never });

afterEach(() => vi.restoreAllMocks());

describe('PatientAccountPanel', () => {
  it('shows services separately from money, an explicit credit, in-progress work without amount and clinic-time authors', async () => {
    vi.spyOn(financeApi, 'getPatientAccount').mockResolvedValue(account(manager));
    const r = await render();
    expect(text(byTestId(r, 'account-services'))).toBe('250.00 RON');
    expect(text(byTestId(r, 'account-paid'))).toBe('300.00 RON');
    expect(text(byTestId(r, 'account-balance'))).toContain('70.00 RON credit (avans al pacientului)');
    const all = text(r.root);
    expect(all).toContain('1 manopere în lucru');
    expect(all).toContain('DET · Detartraj · în lucru');
    expect(all).not.toContain('DET · Detartraj · în lucru · ');
    expect(all).toContain('anulată, fără sumă');
    const clinicTime = new Intl.DateTimeFormat('ro-RO', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'Europe/Bucharest' }).format(new Date(payment.created_at));
    expect(all).toContain(`Recepție Fictivă · ${clinicTime}`);
  });

  it('validates amount, method and reason before sending', async () => {
    vi.spyOn(financeApi, 'getPatientAccount').mockResolvedValue(account(manager));
    const create = vi.spyOn(financeApi, 'createEntry');
    const r = await render();
    for (const bad of ['', '0', '1.234', '-5', 'abc', '10,5']) {
      change(r, 'Sumă', bad);
      await act(async () => { await submitForm(r); });
      expect(text(byTestId(r, 'finance-form-error'))).toMatch(/sum/i);
    }
    change(r, 'Tip mișcare', 'refund');
    change(r, 'Sumă', '10.00');
    await act(async () => { await submitForm(r); });
    expect(text(byTestId(r, 'finance-form-error'))).toContain('Motivul este obligatoriu');
    change(r, 'Tip mișcare', 'adjustment_debit');
    expect(r.root.findAllByProps({ 'aria-label': 'Metodă' })).toHaveLength(0);
    await act(async () => { await submitForm(r); });
    expect(create).not.toHaveBeenCalled();
  });

  it('disables the button while saving, sends once, keeps the request id on error and renews it after success', async () => {
    vi.spyOn(financeApi, 'getPatientAccount').mockResolvedValue(account(manager));
    const first = deferred<AccountEntry>();
    const create = vi.spyOn(financeApi, 'createEntry').mockReturnValueOnce(first.promise).mockResolvedValueOnce(payment).mockResolvedValueOnce(payment);
    const r = await render();
    change(r, 'Sumă', '150.00');
    change(r, 'Vizită asociată', 'visit-1');
    await act(async () => { void submitForm(r); void submitForm(r); await flush(); });
    expect(create).toHaveBeenCalledTimes(1);
    expect(submitButton(r).props.disabled).toBe(true);
    const sent = create.mock.calls[0][1];
    expect(sent).toMatchObject({ kind: 'payment', amount: '150.00', method: 'cash', appointment_id: 'visit-1' });
    await act(async () => { first.reject(axiosError(503, 'Serviciu indisponibil')); await flush(); });
    expect(submitButton(r).props.disabled).toBe(false);
    expect(byLabel(r, 'Sumă').props.value).toBe('150.00');
    await act(async () => { await submitForm(r); await flush(); });
    expect(create.mock.calls[1][1].client_request_id).toBe(sent.client_request_id);
    change(r, 'Sumă', '20.00');
    await act(async () => { await submitForm(r); await flush(); });
    expect(create.mock.calls[2][1].client_request_id).not.toBe(sent.client_request_id);
  });

  it('applies roles: doctor reads only, reception records cash and reverses cash but not adjustments', async () => {
    const get = vi.spyOn(financeApi, 'getPatientAccount').mockResolvedValue(account(doctor));
    let r = await render();
    expect(r.root.findAllByProps({ 'aria-label': 'Mișcare nouă' })).toHaveLength(0);
    expect(r.root.findAllByType('button').filter(b => text(b) === 'Anulează mișcarea')).toHaveLength(0);
    expect(text(r.root)).toContain('poate consulta contul');
    r.unmount();

    get.mockResolvedValue(account(reception));
    r = await render();
    const kinds = byLabel(r, 'Tip mișcare').findAllByType('option').map(o => o.props.value);
    expect(kinds).toEqual(['payment', 'refund']);
    const payCard = r.root.findByProps({ 'data-entry-id': 'entry-pay' });
    const adjCard = r.root.findByProps({ 'data-entry-id': 'entry-adj' });
    expect(payCard.findAllByType('button').map(text)).toContain('Anulează mișcarea');
    expect(adjCard.findAllByType('button')).toHaveLength(0);
  });

  it('reverses with a mandatory reason and a single request', async () => {
    vi.spyOn(financeApi, 'getPatientAccount').mockResolvedValue(account(manager));
    const pending = deferred<AccountEntry>();
    const reverse = vi.spyOn(financeApi, 'reverseEntry').mockReturnValue(pending.promise);
    const r = await render();
    const card = () => r.root.findByProps({ 'data-entry-id': 'entry-pay' });
    act(() => card().findAllByType('button').find(b => text(b) === 'Anulează mișcarea')!.props.onClick());
    const confirm = () => card().findAllByType('button').find(b => text(b).startsWith('Confirm') || text(b).startsWith('Se anulează'))!;
    expect(confirm().props.disabled).toBe(true);
    act(() => byLabel(r, 'Motiv anulare entry-pay').props.onChange({ target: { value: 'Card dublat' } }));
    await act(async () => { confirm().props.onClick(); confirm().props.onClick(); await flush(); });
    expect(reverse).toHaveBeenCalledTimes(1);
    expect(reverse.mock.calls[0][0]).toBe('entry-pay');
    expect(reverse.mock.calls[0][1]).toBe('Card dublat');
    expect(confirm().props.disabled).toBe(true);
    await act(async () => { pending.resolve({ ...payment, id: 'rev', kind: 'reversal' }); await flush(); });
  });
});

function byTestId(r: TestRenderer.ReactTestRenderer, id: string) { return r.root.findByProps({ 'data-testid': id }); }
