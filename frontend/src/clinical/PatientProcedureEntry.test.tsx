import TestRenderer, { act } from 'react-test-renderer';
import type { ComponentProps } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { AxiosError } from 'axios';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as clientApi from '../api/client';
import type { Appointment, ProcedureCatalog } from '../api/client';
import * as clinicalApi from './api';
import type { PerformedProcedure, VisitSummary } from './api';
import { PatientProcedureEntry } from './PatientProcedureEntry';

const appointment: Appointment = {
  id: 'visit-1', clinic_id: 'clinic-1', patient_id: 'patient-1', doctor_id: 'doctor-1', assistant_id: null, room_id: 'room-1',
  starts_at: '2026-11-12T08:00:00Z', ends_at: '2026-11-12T08:30:00Z', local_start: '2026-11-12T10:00:00+02:00', local_end: '2026-11-12T10:30:00+02:00',
  timezone: 'Europe/Bucharest', duration_minutes: 30, appointment_type: 'Consultation', status: 'in_progress', notes: null,
  patient_name: 'Mara Patient', doctor_name: 'Dr. Sample', assistant_name: null, room_name: 'Room 1',
};
const catalogItem: ProcedureCatalog = {
  id: 'catalog-1', clinic_id: 'clinic-1', code: 'OBT-1', name: 'Composite filling', description: null, category: 'restorative',
  default_duration_minutes: 30, base_price: '250.00', currency: 'RON', is_active: true,
  created_at: '2026-11-01T00:00:00Z', updated_at: '2026-11-01T00:00:00Z',
};
const createdProcedure: PerformedProcedure = {
  id: 'performed-1', clinic_id: 'clinic-1', appointment_id: appointment.id, patient_id: appointment.patient_id,
  procedure_catalog_id: catalogItem.id, executed_by_id: appointment.doctor_id, procedure_code: catalogItem.code,
  procedure_name: catalogItem.name, procedure_base_price: catalogItem.base_price, procedure_currency: catalogItem.currency,
  procedure_default_duration: 30, tooth_fdi: '17', tooth_surface: 'occlusal,mesial', status: 'pending', current_price: '250.00',
  total_duration_seconds: null, notes: 'Restoration', created_at: '2026-11-12T08:10:00Z', updated_at: '2026-11-12T08:10:00Z',
};
const searchTerm = 'patient@example.test';

function text(node: TestRenderer.ReactTestInstance): string {
  return node.children.map(child => typeof child === 'string' ? child : text(child)).join('');
}
function tree(client: QueryClient, props: Partial<ComponentProps<typeof PatientProcedureEntry>> = {}) {
  return <QueryClientProvider client={client}><MemoryRouter><PatientProcedureEntry key={`${props.patientId ?? 'patient-1'}:${props.appointmentId ?? 'choose'}`}
    patientId="patient-1" patientName="Mara Patient" appointmentSearchTerm={searchTerm} initialTooth="17"
    onCancel={vi.fn()} onSaved={vi.fn()} {...props}
  /></MemoryRouter></QueryClientProvider>;
}
function queryClient() { return new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 60_000 } } }); }
async function flush() { await new Promise(resolve => setTimeout(resolve, 20)); }
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => { resolve = resolvePromise; reject = rejectPromise; });
  return { promise, resolve, reject };
}
async function waitForForm(renderer: TestRenderer.ReactTestRenderer) {
  for (let i = 0; i < 30; i += 1) {
    if (renderer.root.findAllByProps({ 'aria-label': 'Procedure tooth' }).length) return;
    await act(async () => { await flush(); });
  }
  throw new Error('Shared performed-procedure form did not load');
}
async function waitForSaveEnabled(renderer: TestRenderer.ReactTestRenderer) {
  for (let i = 0; i < 30; i += 1) {
    const button = renderer.root.findByType('form').findAllByType('button')[0];
    if (!button.props.disabled) return button;
    await act(async () => { await flush(); });
  }
  throw new Error('Procedure save did not become enabled after visit verification');
}
function select(renderer: TestRenderer.ReactTestRenderer, label: string, value: string) {
  const element = renderer.root.findByProps({ 'aria-label': label });
  act(() => element.props.onChange({ target: { value } }));
}
function setFormSelect(renderer: TestRenderer.ReactTestRenderer, index: number, value: string) {
  const element = renderer.root.findByType('form').findAllByType('select')[index];
  act(() => element.props.onChange({ target: { value } }));
}
function setText(renderer: TestRenderer.ReactTestRenderer, element: TestRenderer.ReactTestInstance, value: string) {
  act(() => element.props.onChange({ target: { value } }));
}
function check(element: TestRenderer.ReactTestInstance) {
  act(() => element.props.onChange({ target: { checked: !element.props.checked } }));
}

afterEach(() => vi.restoreAllMocks());

describe('tooth-to-procedure entry', () => {
  it('requires an explicit eligible visit choice and creates through the same T04 API payload without starting the timer', async () => {
    vi.spyOn(clientApi, 'listAppointments').mockResolvedValue([appointment]);
    vi.spyOn(clientApi, 'getAppointment').mockResolvedValue(appointment);
    vi.spyOn(clientApi, 'listProcedures').mockResolvedValue([catalogItem]);
    const create = vi.spyOn(clinicalApi, 'createPerformedProcedure').mockResolvedValue(createdProcedure);
    const start = vi.spyOn(clinicalApi, 'startPerformedProcedure');
    const onSaved = vi.fn();
    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = TestRenderer.create(tree(queryClient(), { onSaved })); await flush(); });
    expect(clientApi.listAppointments).toHaveBeenCalledWith({ patient_search: searchTerm });
    expect(renderer.root.findAllByType('form')).toHaveLength(0);
    expect(create).not.toHaveBeenCalled();
    select(renderer, 'Procedure visit', appointment.id);
    await waitForForm(renderer);

    expect(clientApi.getAppointment).toHaveBeenCalledWith(appointment.id);
    expect(text(renderer.root).includes('Mara Patient')).toBe(true);
    expect(text(renderer.root).includes('Consultation')).toBe(true);
    expect(renderer.root.findByProps({ 'aria-label': 'Procedure tooth' }).props.value).toBe('17');
    expect(renderer.root.findByType('form').findAllByType('select')[0].props.value).toBe('');
    setFormSelect(renderer, 0, catalogItem.id);
    const occlusal = renderer.root.findAllByType('label').find(label => text(label).trim() === 'Occlusal');
    const mesial = renderer.root.findAllByType('label').find(label => text(label).trim() === 'Mesial');
    expect(occlusal).toBeDefined();
    expect(mesial).toBeDefined();
    check(occlusal!.findByType('input'));
    check(mesial!.findByType('input'));
    const notes = renderer.root.findAllByType('textarea')[0];
    setText(renderer, notes, 'Restoration');
    const form = renderer.root.findByType('form');
    await act(async () => { form.props.onSubmit({ preventDefault() {} }); await flush(); });

    expect(create).toHaveBeenCalledTimes(1);
    expect(create).toHaveBeenCalledWith({
      appointment_id: appointment.id,
      procedure_catalog_id: catalogItem.id,
      tooth_fdi: '17',
      tooth_surface: 'occlusal,mesial',
      notes: 'Restoration',
    });
    expect(start).not.toHaveBeenCalled();
    expect(onSaved).toHaveBeenCalledWith(createdProcedure);
  });

  it('validates a supplied visit against the patient and blocks closed or foreign visits', async () => {
    const foreignAppointment = { ...appointment, id: 'foreign-visit', patient_id: 'patient-other' };
    vi.spyOn(clientApi, 'getAppointment').mockResolvedValue(foreignAppointment);
    vi.spyOn(clientApi, 'listAppointments').mockResolvedValue([appointment]);
    const listProcedures = vi.spyOn(clientApi, 'listProcedures').mockResolvedValue([catalogItem]);
    const create = vi.spyOn(clinicalApi, 'createPerformedProcedure').mockResolvedValue(createdProcedure);
    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = TestRenderer.create(tree(queryClient(), { appointmentId: foreignAppointment.id })); await flush(); });
    expect(text(renderer.root).includes('belongs to another patient')).toBe(true);
    expect(renderer.root.findAllByType('form')).toHaveLength(0);
    expect(listProcedures).not.toHaveBeenCalled();
    expect(create).not.toHaveBeenCalled();

    await act(async () => { renderer.unmount(); });
    vi.restoreAllMocks();
    vi.spyOn(clientApi, 'getAppointment').mockResolvedValue({ ...appointment, status: 'completed' });
    vi.spyOn(clientApi, 'listAppointments').mockResolvedValue([]);
    vi.spyOn(clientApi, 'listProcedures').mockResolvedValue([catalogItem]);
    const closedCreate = vi.spyOn(clinicalApi, 'createPerformedProcedure').mockResolvedValue(createdProcedure);
    const closedClient = queryClient();
    await act(async () => { renderer = TestRenderer.create(tree(closedClient, { appointmentId: appointment.id })); await flush(); });
    expect(text(renderer.root).includes('cannot receive a new procedure')).toBe(true);
    expect(renderer.root.findAllByType('form')).toHaveLength(0);
    expect(closedCreate).not.toHaveBeenCalled();
  });

  it('preserves the draft during appointment refetch and enables save only after the same visit is revalidated', async () => {
    const client = queryClient();
    const nextAppointment = deferred<Appointment>();
    let fetchCount = 0;
    vi.spyOn(clientApi, 'getAppointment').mockImplementation(() => ++fetchCount === 1 ? Promise.resolve(appointment) : nextAppointment.promise);
    vi.spyOn(clientApi, 'listProcedures').mockResolvedValue([catalogItem]);
    const create = vi.spyOn(clinicalApi, 'createPerformedProcedure').mockResolvedValue(createdProcedure);
    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = TestRenderer.create(tree(client, { appointmentId: appointment.id })); await flush(); });
    await waitForForm(renderer);
    setFormSelect(renderer, 0, catalogItem.id);
    check(renderer.root.findAllByType('label').find(label => text(label).trim() === 'Occlusal')!.findByType('input'));
    setText(renderer, renderer.root.findByType('textarea'), 'Unsaved clinical draft');
    const refetch = client.refetchQueries({ queryKey: ['appointment', appointment.id], type: 'active' });
    await act(async () => { await flush(); });

    const pendingForm = renderer.root.findByType('form');
    expect(renderer.root.findByProps({ 'aria-label': 'Procedure tooth' }).props.value).toBe('17');
    expect(pendingForm.findAllByType('select')[0].props.value).toBe(catalogItem.id);
    expect(pendingForm.findByType('textarea').props.value).toBe('Unsaved clinical draft');
    expect(pendingForm.findAllByType('button')[0].props.disabled).toBe(true);
    await act(async () => { pendingForm.props.onSubmit({ preventDefault() {} }); });
    expect(create).not.toHaveBeenCalled();

    await act(async () => { nextAppointment.resolve(appointment); await refetch; await flush(); });
    const save = await waitForSaveEnabled(renderer);
    expect(renderer.root.findByType('form').findByType('textarea').props.value).toBe('Unsaved clinical draft');
    expect(save.props.disabled).toBe(false);
    await act(async () => { renderer.root.findByType('form').props.onSubmit({ preventDefault() {} }); await flush(); });
    expect(create).toHaveBeenCalledTimes(1);
    expect(create).toHaveBeenCalledWith({
      appointment_id: appointment.id,
      procedure_catalog_id: catalogItem.id,
      tooth_fdi: '17',
      tooth_surface: 'occlusal',
      notes: 'Unsaved clinical draft',
    });
  });

  it.each([
    ['closed', { ...appointment, status: 'completed' }, 'cannot receive a new procedure'],
    ['foreign', { ...appointment, patient_id: 'patient-other', appointment_type: 'Foreign visit' }, 'belongs to another patient'],
  ] as const)('keeps the draft but blocks saving after refetch reports a %s visit', async (_kind, changedAppointment, errorText) => {
    const client = queryClient();
    const nextAppointment = deferred<Appointment>();
    let fetchCount = 0;
    vi.spyOn(clientApi, 'getAppointment').mockImplementation(() => ++fetchCount === 1 ? Promise.resolve(appointment) : nextAppointment.promise);
    vi.spyOn(clientApi, 'listAppointments').mockResolvedValue([]);
    vi.spyOn(clientApi, 'listProcedures').mockResolvedValue([catalogItem]);
    const create = vi.spyOn(clinicalApi, 'createPerformedProcedure').mockResolvedValue(createdProcedure);
    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = TestRenderer.create(tree(client, { appointmentId: appointment.id })); await flush(); });
    await waitForForm(renderer);
    setFormSelect(renderer, 0, catalogItem.id);
    setText(renderer, renderer.root.findByType('textarea'), 'Keep while context is invalid');
    const refetch = client.refetchQueries({ queryKey: ['appointment', appointment.id], type: 'active' });
    await act(async () => { await flush(); });
    await act(async () => { nextAppointment.resolve(changedAppointment); await refetch; await flush(); });

    expect(text(renderer.root).includes(errorText)).toBe(true);
    expect(renderer.root.findByType('form').findByType('textarea').props.value).toBe('Keep while context is invalid');
    if (_kind === 'foreign') expect(text(renderer.root.findByType('form')).includes('Foreign visit')).toBe(false);
    expect(renderer.root.findByType('form').findAllByType('button')[0].props.disabled).toBe(true);
    await act(async () => { renderer.root.findByType('form').props.onSubmit({ preventDefault() {} }); });
    expect(create).not.toHaveBeenCalled();
  });

  it('keeps the draft and blocks save after refetch failure, then restores save after explicit retry', async () => {
    const client = queryClient();
    let fetchCount = 0;
    vi.spyOn(clientApi, 'getAppointment').mockImplementation(() => {
      fetchCount += 1;
      return fetchCount === 2 ? Promise.reject(new Error('Temporary appointment lookup failure')) : Promise.resolve(appointment);
    });
    vi.spyOn(clientApi, 'listProcedures').mockResolvedValue([catalogItem]);
    const create = vi.spyOn(clinicalApi, 'createPerformedProcedure').mockResolvedValue(createdProcedure);
    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = TestRenderer.create(tree(client, { appointmentId: appointment.id })); await flush(); });
    await waitForForm(renderer);
    setFormSelect(renderer, 0, catalogItem.id);
    setText(renderer, renderer.root.findByType('textarea'), 'Preserve through failed verification');
    const failedRefetch = client.refetchQueries({ queryKey: ['appointment', appointment.id], type: 'active' });
    await act(async () => { await failedRefetch; await flush(); });

    expect(text(renderer.root).includes('Temporary appointment lookup failure')).toBe(true);
    expect(renderer.root.findByType('form').findByType('textarea').props.value).toBe('Preserve through failed verification');
    expect(renderer.root.findByType('form').findAllByType('button')[0].props.disabled).toBe(true);
    await act(async () => { renderer.root.findByType('form').props.onSubmit({ preventDefault() {} }); });
    expect(create).not.toHaveBeenCalled();
    const retry = renderer.root.findAllByType('button').find(button => text(button) === 'Retry visit verification');
    expect(retry).toBeDefined();
    await act(async () => { retry!.props.onClick(); await flush(); });
    await waitForSaveEnabled(renderer);
    expect(renderer.root.findByType('form').findByType('textarea').props.value).toBe('Preserve through failed verification');
    await act(async () => { renderer.root.findByType('form').props.onSubmit({ preventDefault() {} }); await flush(); });
    expect(create).toHaveBeenCalledTimes(1);
    expect(create).toHaveBeenCalledWith({
      appointment_id: appointment.id,
      procedure_catalog_id: catalogItem.id,
      tooth_fdi: '17',
      notes: 'Preserve through failed verification',
    });
  });

  it.each([409, 422])('keeps tooth, catalog, surfaces and notes after API %i without retrying', async status => {
    vi.spyOn(clientApi, 'getAppointment').mockResolvedValue(appointment);
    vi.spyOn(clientApi, 'listProcedures').mockResolvedValue([catalogItem]);
    const failure = new AxiosError('Request failed');
    Object.defineProperty(failure, 'response', { value: { status, data: { detail: `Server rejected procedure (${status})` } } });
    const create = vi.spyOn(clinicalApi, 'createPerformedProcedure').mockRejectedValue(failure);
    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = TestRenderer.create(tree(queryClient(), { appointmentId: appointment.id })); await flush(); });
    await waitForForm(renderer);
    setFormSelect(renderer, 0, catalogItem.id);
    const occlusal = renderer.root.findAllByType('label').find(label => text(label).trim() === 'Occlusal');
    check(occlusal!.findByType('input'));
    setText(renderer, renderer.root.findByType('textarea'), 'Keep this note');
    await act(async () => { renderer.root.findByType('form').props.onSubmit({ preventDefault() {} }); await flush(); });

    expect(create).toHaveBeenCalledTimes(1);
    expect(renderer.root.findByProps({ 'aria-label': 'Procedure tooth' }).props.value).toBe('17');
    expect(renderer.root.findByType('form').findAllByType('select')[0].props.value).toBe(catalogItem.id);
    expect(renderer.root.findAllByType('input').some(input => input.props.checked)).toBe(true);
    expect(renderer.root.findByType('textarea').props.value).toBe('Keep this note');
    expect(text(renderer.root).includes(`Server rejected procedure (${status})`)).toBe(true);
  });

  it('keeps A and B visit caches isolated when A save resolves after switching patient context', async () => {
    let resolveCreate!: (value: PerformedProcedure) => void;
    const appointmentB = { ...appointment, id: 'visit-B', patient_id: 'patient-B' };
    vi.spyOn(clientApi, 'getAppointment').mockImplementation(async id => id === appointmentB.id ? appointmentB : appointment);
    vi.spyOn(clientApi, 'listProcedures').mockResolvedValue([catalogItem]);
    const create = vi.spyOn(clinicalApi, 'createPerformedProcedure').mockImplementation(() => new Promise(resolve => { resolveCreate = resolve; }));
    const start = vi.spyOn(clinicalApi, 'startPerformedProcedure');
    const client = queryClient();
    const emptySummary = (visit: Appointment): VisitSummary => ({ appointment_id: visit.id, patient_id: visit.patient_id, patient_name: visit.patient_name ?? '', appointment_starts_at: visit.starts_at, appointment_status: visit.status, performed_procedures: [], can_close_visit: true, unresolved_procedures: [] });
    client.setQueryData(['visit-summary', appointment.id], emptySummary(appointment));
    client.setQueryData(['visit-summary', appointmentB.id], emptySummary(appointmentB));
    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = TestRenderer.create(tree(client, { appointmentId: appointment.id })); await flush(); });
    await waitForForm(renderer);
    setFormSelect(renderer, 0, catalogItem.id);
    const form = renderer.root.findByType('form');
    await act(async () => { form.props.onSubmit({ preventDefault() {} }); form.props.onSubmit({ preventDefault() {} }); await Promise.resolve(); });
    expect(create).toHaveBeenCalledTimes(1);
    expect(start).not.toHaveBeenCalled();

    await act(async () => {
      renderer.update(tree(client, {
        patientId: 'patient-B', patientName: 'Patient B', appointmentSearchTerm: 'patient-b@example.test',
        appointmentId: appointmentB.id, initialTooth: '16',
      }));
      await flush();
    });
    expect(text(renderer.root).includes('Patient B')).toBe(true);
    await waitForForm(renderer);
    expect(renderer.root.findByProps({ 'aria-label': 'Procedure tooth' }).props.value).toBe('16');
    const savedForA = { ...createdProcedure, appointment_id: appointment.id, patient_id: appointment.patient_id };
    await act(async () => { resolveCreate(savedForA); await flush(); });
    expect(client.getQueryData<VisitSummary>(['visit-summary', appointment.id])?.performed_procedures.map(item => item.id)).toEqual(['performed-1']);
    expect(client.getQueryData<VisitSummary>(['visit-summary', appointmentB.id])?.performed_procedures).toEqual([]);
  });
});
