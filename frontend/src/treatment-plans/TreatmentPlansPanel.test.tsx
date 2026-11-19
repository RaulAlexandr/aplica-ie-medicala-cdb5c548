import TestRenderer, { act } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AxiosError } from 'axios';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as clientApi from '../api/client';
import { api, type ProcedureCatalog } from '../api/client';
import * as plansApi from './api';
import type { LinkCandidates, PlanItem, TreatmentPlan } from './api';
import { TreatmentPlansPanel } from './TreatmentPlansPanel';

const catalog: ProcedureCatalog[] = [
  { id: 'cat-endo', clinic_id: 'c1', code: 'ENDO', name: 'Tratament endodontic', description: null, category: null, default_duration_minutes: 60, base_price: '300.00', currency: 'RON', is_active: true, created_at: '', updated_at: '' },
  { id: 'cat-obt', clinic_id: 'c1', code: 'OBT', name: 'Obturație', description: null, category: null, default_duration_minutes: 30, base_price: '250.00', currency: 'RON', is_active: true, created_at: '', updated_at: '' },
];
const progress = { total: 2, done: 0, cancelled: 0, in_progress: 0, scheduled: 0, planned: 2, attention: 0, remaining: 2, percent: 0 };
function item(overrides: Partial<PlanItem> = {}): PlanItem {
  return {
    id: 'item-1', stage_id: 'stage-1', position: 1, procedure_catalog_id: 'cat-endo', procedure_code: 'ENDO', procedure_name: 'Tratament endodontic',
    currency: 'RON', estimated_price: '300.00', estimated_duration_minutes: 60, tooth_fdi: '16', tooth_surface: 'occlusal', priority: 'urgent',
    notes: null, status: 'active', progress: 'planned', cancel_reason: null, cancelled_at: null, cancelled_by: null, appointment: null, performed_procedure: null,
    ...overrides,
  };
}
function plan(overrides: Partial<TreatmentPlan> = {}): TreatmentPlan {
  return {
    id: 'plan-1', clinic_id: 'c1', patient_id: 'patient-1', title: 'Reabilitare fictivă', notes: null, status: 'accepted', version: 1, accepted_version: 1,
    needs_acceptance: false, created_by: 'Dr. Fictiv', created_at: '2026-11-19T08:00:00Z', updated_at: '2026-11-19T08:00:00Z',
    completed_at: null, completed_by: null, cancelled_at: null, cancelled_by: null, cancel_reason: null,
    can_complete: false, completion_blocker: '2 item(s) are neither completed nor explicitly cancelled', estimate_label: 'Estimare; nu este factură sau sumă încasată',
    totals: [{ currency: 'RON', estimated_total: '710.50', completed_estimate: '0.00', remaining_estimate: '710.50', estimated_minutes: 90 }],
    progress,
    stages: [
      { id: 'stage-1', position: 1, title: 'Urgențe', notes: null, progress: { ...progress, total: 1, planned: 1, remaining: 1 }, items: [item()] },
      { id: 'stage-2', position: 2, title: 'Restaurări', notes: null, progress: { ...progress, total: 1, planned: 1, remaining: 1 }, items: [item({ id: 'item-2', stage_id: 'stage-2', procedure_catalog_id: 'cat-obt', procedure_code: 'OBT', procedure_name: 'Obturație', tooth_fdi: '21', tooth_surface: 'distal,mesial', estimated_price: '410.50', priority: 'normal' })] },
    ],
    acceptances: [{ id: 'acc-1', version: 1, accepted_by: 'Dr. Fictiv', accepted_by_id: 'u1', accepted_at: '2026-11-19T08:05:00Z', note: 'Acord verbal', is_current: true, snapshot: { version: 1, title: 'Reabilitare fictivă', totals: [{ currency: 'RON', estimated_total: '710.50', estimated_minutes: 90 }], stages: [] } }],
    history: [],
    ...overrides,
  };
}
const candidates: LinkCandidates = {
  appointments: [{ id: 'visit-1', starts_at: '2026-12-01T09:00:00Z', status: 'scheduled', appointment_type: 'Tratament' }],
  performed_procedures: [
    { id: 'pp-match', appointment_id: 'visit-1', procedure_catalog_id: 'cat-endo', procedure_code: 'ENDO', procedure_name: 'Tratament endodontic', tooth_fdi: '16', tooth_surface: 'occlusal', status: 'completed', created_at: '2026-12-01T09:10:00Z', linked_item_id: null },
    { id: 'pp-wrong-tooth', appointment_id: 'visit-1', procedure_catalog_id: 'cat-endo', procedure_code: 'ENDO', procedure_name: 'Tratament endodontic', tooth_fdi: '17', tooth_surface: 'occlusal', status: 'completed', created_at: '2026-12-01T09:20:00Z', linked_item_id: null },
    { id: 'pp-taken', appointment_id: 'visit-1', procedure_catalog_id: 'cat-endo', procedure_code: 'ENDO', procedure_name: 'Tratament endodontic', tooth_fdi: '16', tooth_surface: 'occlusal', status: 'completed', created_at: '2026-12-01T09:30:00Z', linked_item_id: 'other-item' },
  ],
};

function text(node: TestRenderer.ReactTestInstance): string {
  return node.children.map(child => typeof child === 'string' ? child : text(child)).join('');
}
function queryClient() { return new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 60_000 } } }); }
async function flush() { await new Promise(resolve => setTimeout(resolve, 20)); }
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((ok, fail) => { resolve = ok; reject = fail; });
  return { promise, resolve, reject };
}
function byLabel(renderer: TestRenderer.ReactTestRenderer, label: string) { return renderer.root.findByProps({ 'aria-label': label }); }
function button(renderer: TestRenderer.ReactTestRenderer, label: string) {
  const found = renderer.root.findAllByType('button').find(node => text(node).trim() === label);
  if (!found) throw new Error(`Button not found: ${label}`);
  return found;
}
function change(renderer: TestRenderer.ReactTestRenderer, label: string, value: string) {
  act(() => byLabel(renderer, label).props.onChange({ target: { value } }));
}
function mount(client: QueryClient, patientId = 'patient-1') {
  return <QueryClientProvider client={client}><TreatmentPlansPanel key={`${patientId}:treatment-plans`} patientId={patientId} timezone="Europe/Bucharest" /></QueryClientProvider>;
}
async function render(client = queryClient(), patientId = 'patient-1') {
  let renderer!: TestRenderer.ReactTestRenderer;
  await act(async () => { renderer = TestRenderer.create(mount(client, patientId)); await flush(); });
  // A fixed 20 ms flush is not always enough under a loaded parallel run: wait (bounded) for the initial list query.
  for (let attempt = 0; attempt < 50 && text(renderer.root).includes('Se încarcă planurile'); attempt += 1) {
    await act(async () => { await flush(); });
  }
  return renderer;
}
function axiosError(status: number, detail: string) {
  return new AxiosError(detail, String(status), undefined, undefined, { status, data: { detail }, statusText: '', headers: {}, config: {} as never });
}

afterEach(() => vi.restoreAllMocks());

describe('treatment plans panel', () => {
  it('creates a two-stage plan once, keeps the draft after an error and retries with the same idempotency key', async () => {
    vi.spyOn(clientApi, 'listProcedures').mockResolvedValue(catalog);
    vi.spyOn(plansApi, 'listPatientPlans').mockResolvedValue([]);
    vi.spyOn(plansApi, 'getLinkCandidates').mockResolvedValue(candidates);
    const pending = deferred<TreatmentPlan>();
    const create = vi.spyOn(plansApi, 'createPlan')
      .mockRejectedValueOnce(axiosError(409, 'Inactive catalog procedures cannot be added to a treatment plan'))
      .mockReturnValueOnce(pending.promise);
    vi.spyOn(plansApi, 'getPlan').mockResolvedValue(plan({ status: 'draft', accepted_version: null, needs_acceptance: true, acceptances: [] }));
    const renderer = await render();
    expect(text(renderer.root)).toContain('Pacientul nu are planuri de tratament.');
    act(() => button(renderer, 'Plan nou').props.onClick());

    change(renderer, 'Titlul planului', 'Reabilitare fictivă');
    change(renderer, 'Etapa 1 manopera 1 serviciu', 'cat-endo');
    change(renderer, 'Etapa 1 manopera 1 dinte', '16');
    const occlusal = renderer.root.findAllByType('label').find(label => text(label).trim() === 'Ocluzală');
    act(() => occlusal!.findByType('input').props.onChange());
    change(renderer, 'Etapa 1 manopera 1 prioritate', 'urgent');
    change(renderer, 'Etapa 2 manopera 1 serviciu', 'cat-obt');
    change(renderer, 'Etapa 2 manopera 1 dinte', '21');
    change(renderer, 'Etapa 2 manopera 1 tarif estimat', '410.50');

    const form = renderer.root.findByProps({ 'aria-label': 'Plan de tratament nou' });
    await act(async () => { form.props.onSubmit({ preventDefault() {} }); await flush(); });
    expect(text(renderer.root)).toContain('Inactive catalog procedures');
    expect(byLabel(renderer, 'Titlul planului').props.value).toBe('Reabilitare fictivă');
    expect(byLabel(renderer, 'Etapa 2 manopera 1 tarif estimat').props.value).toBe('410.50');

    await act(async () => {
      form.props.onSubmit({ preventDefault() {} });
      form.props.onSubmit({ preventDefault() {} });
      await flush();
    });
    expect(create).toHaveBeenCalledTimes(2);
    const [firstCall, secondCall] = create.mock.calls;
    expect(secondCall[2]).toBe(firstCall[2]);
    expect(secondCall[0]).toBe('patient-1');
    expect(secondCall[1]).toEqual({
      title: 'Reabilitare fictivă', notes: null,
      stages: [
        { title: 'Etapa 1', items: [{ procedure_catalog_id: 'cat-endo', tooth_fdi: '16', tooth_surface: 'occlusal', priority: 'urgent', notes: null }] },
        { title: 'Etapa 2', items: [{ procedure_catalog_id: 'cat-obt', tooth_fdi: '21', tooth_surface: null, priority: 'normal', estimated_price: '410.50', notes: null }] },
      ],
    });
    await act(async () => { pending.resolve(plan({ status: 'draft', accepted_version: null, needs_acceptance: true, acceptances: [] })); await flush(); });
    expect(text(renderer.root)).toContain('Consemnează acceptarea versiunii 1');
    expect(text(renderer.root)).toContain('Nu este semnătură electronică');
  });

  it('switching patient discards the open form and loads only the new patient plans', async () => {
    vi.spyOn(clientApi, 'listProcedures').mockResolvedValue(catalog);
    const list = vi.spyOn(plansApi, 'listPatientPlans').mockResolvedValue([]);
    const client = queryClient();
    const renderer = await render(client, 'patient-1');
    act(() => button(renderer, 'Plan nou').props.onClick());
    change(renderer, 'Titlul planului', 'Ciornă pacient 1');
    await act(async () => { renderer.update(mount(client, 'patient-2')); await flush(); });
    expect(list).toHaveBeenLastCalledWith('patient-2');
    expect(renderer.root.findAllByProps({ 'aria-label': 'Titlul planului' })).toHaveLength(0);
    expect(text(renderer.root)).toContain('Pacientul nu are planuri de tratament.');
  });

  it('offers only compatible performed procedures, links once on repeated clicks and shows progress', async () => {
    vi.spyOn(clientApi, 'listProcedures').mockResolvedValue(catalog);
    vi.spyOn(plansApi, 'listPatientPlans').mockResolvedValue([{ ...plan(), id: 'plan-1' }]);
    vi.spyOn(plansApi, 'getPlan').mockResolvedValue(plan());
    vi.spyOn(plansApi, 'getLinkCandidates').mockResolvedValue(candidates);
    const pending = deferred<TreatmentPlan>();
    const link = vi.spyOn(plansApi, 'linkPerformed').mockReturnValue(pending.promise);
    const renderer = await render();
    await act(async () => { button(renderer, 'Deschide planul').props.onClick(); await flush(); });
    expect(text(byLabel(renderer, 'Estimări plan'))).toContain('710.50 RON');
    expect(text(renderer.root)).toContain('Versiunea 1 acceptată');

    const performedSelect = byLabel(renderer, 'Manoperă efectuată pentru ENDO');
    const options = performedSelect.findAllByType('option').map(option => option.props.value);
    expect(options).toEqual(['', 'pp-match']);
    change(renderer, 'Manoperă efectuată pentru ENDO', 'pp-match');
    const linkButton = byLabel(renderer, 'Manoperă planificată ENDO 16').findAllByType('button').find(node => text(node) === 'Asociază manopera')!;
    await act(async () => { linkButton.props.onClick(); linkButton.props.onClick(); await flush(); });
    expect(link).toHaveBeenCalledTimes(1);
    expect(link).toHaveBeenCalledWith('item-1', 'pp-match');

    const done = plan({
      status: 'in_progress', progress: { ...progress, done: 1, planned: 1, remaining: 1, percent: 50 },
      stages: plan().stages.map(stage => stage.id === 'stage-1' ? { ...stage, progress: { ...stage.progress, done: 1, planned: 0, remaining: 0, percent: 100 }, items: [item({ progress: 'done', performed_procedure: { id: 'pp-match', status: 'completed', procedure_code: 'ENDO', procedure_name: 'Tratament endodontic', tooth_fdi: '16', tooth_surface: 'occlusal', appointment_id: 'visit-1', linked_by: 'Dr. Fictiv', linked_at: '2026-12-01T10:00:00Z' } })] } : stage),
    });
    await act(async () => { pending.resolve(done); await flush(); });
    expect(text(renderer.root.findByProps({ 'data-testid': 'plan-status' }))).toBe('În lucru');
    expect(text(renderer.root.findByProps({ 'data-testid': 'plan-progress' }))).toContain('1 efectuate');
    expect(renderer.root.findAllByProps({ 'data-testid': 'item-progress' }).map(text)).toEqual(['Efectuat', 'Planificat']);
    expect(text(renderer.root.findByProps({ 'data-testid': 'completion-blocker' }))).toContain('neither completed');
    expect(button(renderer, 'Finalizează planul').props.disabled).toBe(true);
  });

  it('records a new acceptance for a changed version and refuses a response for another patient', async () => {
    vi.spyOn(clientApi, 'listProcedures').mockResolvedValue(catalog);
    vi.spyOn(plansApi, 'listPatientPlans').mockResolvedValue([{ ...plan(), id: 'plan-1' }]);
    const changed = plan({ status: 'draft', version: 2, accepted_version: 1, needs_acceptance: true, acceptances: [{ ...plan().acceptances[0], is_current: false }] });
    vi.spyOn(plansApi, 'getPlan').mockResolvedValue(changed);
    vi.spyOn(plansApi, 'getLinkCandidates').mockResolvedValue(candidates);
    const accept = vi.spyOn(plansApi, 'acceptPlan').mockResolvedValue({ ...changed, patient_id: 'patient-2' });
    const renderer = await render();
    await act(async () => { button(renderer, 'Deschide planul').props.onClick(); await flush(); });
    expect(text(renderer.root)).toContain('Versiunea 2 necesită o nouă acceptare');
    expect(text(renderer.root.findByProps({ 'data-testid': 'acceptance-row' }))).toContain('înlocuită');
    change(renderer, 'Observație acceptare', 'Acord pentru estimarea actualizată');
    await act(async () => { button(renderer, 'Consemnează acceptarea versiunii 2').props.onClick(); await flush(); });
    expect(accept).toHaveBeenCalledWith('plan-1', 'Acord pentru estimarea actualizată', 2);
    expect(text(renderer.root)).toContain('altui pacient');
    expect(byLabel(renderer, 'Observație acceptare').props.value).toBe('Acord pentru estimarea actualizată');
  });
});

describe('treatment plans API client', () => {
  it('sends the idempotency key, decimal strings and unlink reason', async () => {
    const post = vi.spyOn(api, 'post').mockResolvedValue({ data: plan() });
    const del = vi.spyOn(api, 'delete').mockResolvedValue({ data: plan() });
    await plansApi.createPlan('patient-1', { title: 'T', stages: [{ title: 'E', items: [{ procedure_catalog_id: 'cat-endo', estimated_price: '10.10' }] }] }, 'key-1');
    expect(post).toHaveBeenCalledWith('/treatment-plans/patients/patient-1', expect.objectContaining({ title: 'T' }), { headers: { 'Idempotency-Key': 'key-1' } });
    await plansApi.unlinkPerformed('item-1', 'Greșeală');
    expect(del).toHaveBeenCalledWith('/treatment-plans/items/item-1/performed-procedure', { data: { reason: 'Greșeală' } });
    await plansApi.acceptPlan('plan-1', 'Acord', 3);
    expect(post).toHaveBeenLastCalledWith('/treatment-plans/plan-1/accept', { note: 'Acord', expected_version: 3 });
  });
});
