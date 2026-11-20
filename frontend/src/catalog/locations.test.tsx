import TestRenderer, { act } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as client from '../api/client';
import type { Appointment, ProcedureCatalog } from '../api/client';
import * as clinicalApi from '../clinical/api';
import { ProcedureForm } from '../clinical/ProcedureForm';
import * as plansApi from '../treatment-plans/api';
import { PlanForm } from '../treatment-plans/TreatmentPlansPanel';
import { CatalogProcedureForm } from './CatalogProcedureForm';
import { locationProblem, ruleOf } from './locations';

const service = (id: string, code: string, allowed_location?: ProcedureCatalog['allowed_location']): ProcedureCatalog => ({
  id, clinic_id: 'clinic-1', code, name: `Serviciu ${code}`, description: null, category: null, default_duration_minutes: 30,
  base_price: '100.00', currency: 'RON', is_active: true, created_at: '2030-01-01T00:00:00Z', updated_at: '2030-01-01T00:00:00Z',
  ...(allowed_location ? { allowed_location } : {}),
});
// LEGACY has no allowed_location at all (older API payload) and must behave like "any".
const catalog = [service('c-legacy', 'LEGACY'), service('c-cons', 'CONS', 'no_tooth'), service('c-ext', 'EXT', 'tooth'), service('c-obt', 'OBT', 'tooth_surface')];

const appointment: Appointment = {
  id: 'appointment-1', clinic_id: 'clinic-1', patient_id: 'patient-1', doctor_id: 'doctor-1', assistant_id: null, room_id: 'room-1',
  starts_at: '2030-10-28T08:00:00Z', ends_at: '2030-10-28T08:30:00Z', local_start: '2030-10-28T10:00:00+02:00', local_end: '2030-10-28T10:30:00+02:00',
  timezone: 'Europe/Bucharest', duration_minutes: 30, appointment_type: 'Tratament', status: 'in_progress', notes: null,
  patient_name: 'Ana Fictiv', doctor_name: 'Dr. Fictiv', assistant_name: null, room_name: 'Cabinet',
};

function textOf(node: TestRenderer.ReactTestInstance): string {
  return node.children.map(child => (typeof child === 'string' ? child : textOf(child))).join('');
}
const queryClient = () => new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
async function mount(element: JSX.Element) {
  let renderer!: TestRenderer.ReactTestRenderer;
  await act(async () => { renderer = TestRenderer.create(<QueryClientProvider client={queryClient()}>{element}</QueryClientProvider>); });
  return renderer;
}
const select = (r: TestRenderer.ReactTestRenderer, label: string) => r.root.findByProps({ 'aria-label': label });
const optionsOf = (node: TestRenderer.ReactTestInstance) => node.findAllByType('option').map(option => ({ value: option.props.value as string, disabled: Boolean(option.props.disabled) }));
const submitButton = (r: TestRenderer.ReactTestRenderer) => r.root.findAllByType('button').find(button => textOf(button) === 'Save procedure')!;
async function change(node: TestRenderer.ReactTestInstance, value: string) {
  await act(async () => { node.props.onChange({ target: { value } }); });
}

describe('T03 location rule helper', () => {
  it('mirrors the server rule and treats a missing value as "any"', () => {
    expect(ruleOf(undefined)).toBe('any');
    expect(ruleOf({ allowed_location: 'quadrant' })).toBe('any');
    expect(locationProblem('any', '', 0)).toBeNull();
    expect(locationProblem('no_tooth', '16', 0)).toContain('fără dinte');
    expect(locationProblem('tooth', '', 0)).toContain('dinte FDI');
    expect(locationProblem('tooth', '16', 0)).toBeNull();
    expect(locationProblem('tooth_surface', '16', 0)).toContain('cel puțin o suprafață');
    expect(locationProblem('tooth_surface', '16', 1)).toBeNull();
  });
});

describe('T07 procedure form respects the service location rule on both paths', () => {
  afterEach(() => vi.restoreAllMocks());

  it('procedure → tooth (visit page): a tooth-required service cannot be saved without a tooth', async () => {
    const create = vi.spyOn(clinicalApi, 'createPerformedProcedure');
    const r = await mount(<ProcedureForm appointment={appointment} patientName="Ana Fictiv" catalog={catalog} onSaved={vi.fn()} />);
    // Without a tooth chosen every service is offered.
    expect(optionsOf(r.root.findAllByType('select')[0]).filter(o => o.disabled)).toEqual([]);
    await change(r.root.findAllByType('select')[0], 'c-ext');
    expect(optionsOf(select(r, 'Procedure tooth')).find(o => o.value === '')?.disabled).toBe(true);
    expect(submitButton(r).props.disabled).toBe(true);
    expect(textOf(r.root.findByProps({ 'aria-label': 'Location rule' }))).toContain('cere un dinte FDI');
    await act(async () => { await r.root.findByType('form').props.onSubmit({ preventDefault: () => undefined }); });
    expect(create).not.toHaveBeenCalled();

    await change(select(r, 'Procedure tooth'), '16');
    expect(submitButton(r).props.disabled).toBe(false);
    expect(r.root.findAllByProps({ 'aria-label': 'Location rule' })).toHaveLength(0);

    // A service recorded without a tooth clears the tooth and locks the tooth field.
    await change(r.root.findAllByType('select')[0], 'c-legacy');
    await change(r.root.findAllByType('select')[0], 'c-cons');
    expect(select(r, 'Procedure tooth').props.value).toBe('');
    expect(select(r, 'Procedure tooth').props.disabled).toBe(true);
    expect(submitButton(r).props.disabled).toBe(false);
  });

  it('tooth → procedure (patient record): no-tooth services are not offered and surfaces are enforced', async () => {
    const create = vi.spyOn(clinicalApi, 'createPerformedProcedure').mockResolvedValue({ id: 'pp-1' } as clinicalApi.PerformedProcedure);
    const r = await mount(<ProcedureForm appointment={appointment} patientName="Ana Fictiv" catalog={catalog} initialTooth="16" onSaved={vi.fn()} />);
    const procedure = r.root.findAllByType('select')[0];
    expect(optionsOf(procedure)).toEqual([
      { value: '', disabled: false }, { value: 'c-legacy', disabled: false }, { value: 'c-cons', disabled: true },
      { value: 'c-ext', disabled: false }, { value: 'c-obt', disabled: false },
    ]);
    await change(procedure, 'c-obt');
    expect(submitButton(r).props.disabled).toBe(true);
    expect(textOf(r.root.findByProps({ 'aria-label': 'Location rule' }))).toContain('cel puțin o suprafață');
    const occlusal = r.root.findAllByType('input').find(input => input.props.type === 'checkbox' && textOf(input.parent!).includes('Occlusal'))!;
    await act(async () => { occlusal.props.onChange(); });
    expect(submitButton(r).props.disabled).toBe(false);
    await act(async () => { await r.root.findByType('form').props.onSubmit({ preventDefault: () => undefined }); });
    expect(create).toHaveBeenCalledWith({ appointment_id: 'appointment-1', procedure_catalog_id: 'c-obt', tooth_fdi: '16', tooth_surface: 'occlusal' });
  });

  it('an existing service without a rule behaves exactly as before', async () => {
    const create = vi.spyOn(clinicalApi, 'createPerformedProcedure').mockResolvedValue({ id: 'pp-2' } as clinicalApi.PerformedProcedure);
    const r = await mount(<ProcedureForm appointment={appointment} patientName="Ana Fictiv" catalog={catalog} onSaved={vi.fn()} />);
    await change(r.root.findAllByType('select')[0], 'c-legacy');
    expect(select(r, 'Procedure tooth').props.disabled).toBe(false);
    expect(optionsOf(select(r, 'Procedure tooth'))[0]).toEqual({ value: '', disabled: false });
    expect(submitButton(r).props.disabled).toBe(false);
    await act(async () => { await r.root.findByType('form').props.onSubmit({ preventDefault: () => undefined }); });
    expect(create).toHaveBeenCalledWith({ appointment_id: 'appointment-1', procedure_catalog_id: 'c-legacy' });
  });
});

describe('T08 plan form respects the service location rule', () => {
  afterEach(() => vi.restoreAllMocks());

  it('blocks a tooth-required item without a tooth and hides no-tooth services once a tooth is set', async () => {
    const createPlan = vi.spyOn(plansApi, 'createPlan').mockResolvedValue({ id: 'plan-1' } as plansApi.TreatmentPlan);
    const r = await mount(<PlanForm patientId="patient-1" catalog={catalog} onCreated={vi.fn()} onCancel={vi.fn()} />);
    await change(select(r, 'Titlul planului'), 'Plan fictiv');
    await change(select(r, 'Etapa 1 manopera 1 serviciu'), 'c-ext');
    await change(select(r, 'Etapa 2 manopera 1 serviciu'), 'c-legacy');
    expect(optionsOf(select(r, 'Etapa 1 manopera 1 dinte'))[0]).toEqual({ value: '', disabled: true });
    expect(textOf(r.root.findByProps({ 'aria-label': 'Etapa 1 manopera 1 regulă localizare' }))).toContain('cere un dinte FDI');
    await act(async () => { await r.root.findByType('form').props.onSubmit({ preventDefault: () => undefined }); });
    expect(createPlan).not.toHaveBeenCalled();
    expect(r.root.findAllByProps({ role: 'alert' }).map(textOf).join(' ')).toContain('EXT: Serviciul cere un dinte FDI.');

    await change(select(r, 'Etapa 1 manopera 1 dinte'), '16');
    expect(optionsOf(select(r, 'Etapa 1 manopera 1 serviciu')).find(o => o.value === 'c-cons')?.disabled).toBe(true);
    await act(async () => { await r.root.findByType('form').props.onSubmit({ preventDefault: () => undefined }); });
    expect(createPlan).toHaveBeenCalledTimes(1);
    const payload = createPlan.mock.calls[0][1];
    expect(payload.stages[0].items[0]).toMatchObject({ procedure_catalog_id: 'c-ext', tooth_fdi: '16' });
    expect(payload.stages[1].items[0]).toMatchObject({ procedure_catalog_id: 'c-legacy', tooth_fdi: null });
  });

  it('a no-tooth service clears and locks the tooth selection', async () => {
    const r = await mount(<PlanForm patientId="patient-1" catalog={catalog} onCreated={vi.fn()} onCancel={vi.fn()} />);
    await change(select(r, 'Etapa 1 manopera 1 dinte'), '21');
    await change(select(r, 'Etapa 1 manopera 1 serviciu'), 'c-legacy');
    await change(select(r, 'Etapa 1 manopera 1 dinte'), '');
    await change(select(r, 'Etapa 1 manopera 1 serviciu'), 'c-cons');
    expect(select(r, 'Etapa 1 manopera 1 dinte').props.value).toBe('');
    expect(select(r, 'Etapa 1 manopera 1 dinte').props.disabled).toBe(true);
  });
});

describe('catalog form declares the allowed location', () => {
  afterEach(() => vi.restoreAllMocks());

  it('defaults to "any" for new services and sends the chosen rule', async () => {
    const create = vi.spyOn(client, 'createProcedure').mockResolvedValue(service('c-new', 'NEW', 'tooth'));
    const r = await mount(<CatalogProcedureForm onSaved={vi.fn()} onCancel={vi.fn()} />);
    const location = select(r, 'Localizări permise');
    expect(location.props.value).toBe('any');
    expect(optionsOf(location).map(o => o.value)).toEqual(['any', 'no_tooth', 'tooth', 'tooth_surface']);
    const inputs = r.root.findAllByType('input');
    await change(inputs[0], 'NEW');
    await change(r.root.findAllByType('input')[1], 'Serviciu nou');
    await change(r.root.findAllByType('input')[5], '150.00');
    await change(select(r, 'Localizări permise'), 'tooth');
    await act(async () => { r.root.findByType('form').props.onSubmit({ preventDefault: () => undefined }); });
    expect(create).toHaveBeenCalledTimes(1);
    expect(create.mock.calls[0][0]).toMatchObject({ code: 'NEW', name: 'Serviciu nou', base_price: '150.00', allowed_location: 'tooth' });
  });

  it('edits keep the current rule and warn that a change applies only to new records', async () => {
    const update = vi.spyOn(client, 'updateProcedure').mockResolvedValue(service('c-obt', 'OBT', 'tooth'));
    const r = await mount(<CatalogProcedureForm initial={catalog[3]} onSaved={vi.fn()} onCancel={vi.fn()} />);
    expect(select(r, 'Localizări permise').props.value).toBe('tooth_surface');
    expect(r.root.findAllByProps({ className: 'muted' }).map(textOf).join(' ')).not.toContain('înregistrările existente');
    await change(select(r, 'Localizări permise'), 'tooth');
    expect(r.root.findAllByProps({ className: 'muted' }).map(textOf).join(' ')).toContain('înregistrările existente rămân neschimbate');
    await act(async () => { r.root.findByType('form').props.onSubmit({ preventDefault: () => undefined }); });
    expect(update).toHaveBeenCalledWith('c-obt', expect.objectContaining({ allowed_location: 'tooth' }));
  });
});
