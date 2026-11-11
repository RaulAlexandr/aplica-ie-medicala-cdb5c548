import TestRenderer, { act } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AxiosError } from 'axios';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as clientApi from '../api/client';
import type { Appointment, ProcedureCatalog, User } from '../api/client';
import * as odontogramApi from './api';
import type { OdontogramFindingDto, PatientOdontogramDto } from './api';
import * as clinicalApi from '../clinical/api';
import { ADULT_TOOTH_IDS } from './types';
import { PatientOdontogram } from './PatientOdontogram';

const doctor: User = { id: 'doctor-1', clinic_id: 'clinic-1', full_name: 'Dr. Sample', email: 'doctor@example.test', role: 'doctor' };
const findingId = 'finding-1';
const observedAt = '2026-11-11T09:05:00Z';

function apiFinding(patientId: string, values: Partial<OdontogramFindingDto> = {}): OdontogramFindingDto {
  return {
    id: findingId, toothId: '17', kind: 'caries', description: `Finding for ${patientId}`, surfaces: ['occlusal'],
    status: 'draft', observedAt, createdAt: observedAt,
    authorId: doctor.id, author: doctor.full_name, revisions: [], ...values,
  };
}

function apiRecord(patientId: string, findings: OdontogramFindingDto[] = []): PatientOdontogramDto {
  return { patientId, teeth: ADULT_TOOTH_IDS.map((toothId) => ({ toothId, status: 'unevaluated', updatedAt: null, updatedBy: null })), findings };
}

function text(node: TestRenderer.ReactTestInstance): string {
  return node.children.map((child) => typeof child === 'string' ? child : text(child)).join('');
}

function tree(patientId: string, client: QueryClient, user = doctor, entry: { patientName?: string; appointmentSearchTerm?: string; appointmentId?: string } = {}) {
  return <QueryClientProvider client={client}><PatientOdontogram key={patientId} patientId={patientId} user={user} timezone="Europe/Bucharest" {...entry} /></QueryClientProvider>;
}

function newClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
}

async function flush() { await new Promise((resolve) => setTimeout(resolve, 10)); }
async function waitForProcedureForm(renderer: TestRenderer.ReactTestRenderer) {
  for (let attempt = 0; attempt < 30; attempt += 1) {
    if (renderer.root.findAllByProps({ 'aria-label': 'Procedure tooth' }).length) return;
    await act(async () => { await flush(); });
  }
  throw new Error('Shared procedure form did not load');
}
async function waitForChart(renderer: TestRenderer.ReactTestRenderer) {
  for (let attempt = 0; attempt < 20; attempt += 1) {
    if (renderer.root.findAllByProps({ 'aria-label': 'Arcada superioară' }).length) return;
    await act(async () => { await flush(); });
  }
  throw new Error('Odontogram chart did not load');
}
function click(renderer: TestRenderer.ReactTestRenderer, label: string) {
  const button = renderer.root.findAllByType('button').find((item) => item.props['aria-label'] === label || text(item) === label);
  if (!button) throw new Error(`Button not found: ${label}`);
  act(() => button.props.onClick());
}
function clickTooth(renderer: TestRenderer.ReactTestRenderer, toothId: string) {
  const button = renderer.root.findByProps({ 'data-tooth-id': toothId });
  act(() => button.props.onClick());
}
function setTextarea(renderer: TestRenderer.ReactTestRenderer, label: string, value: string) {
  const textarea = renderer.root.findByProps({ 'aria-label': label });
  act(() => textarea.props.onChange({ target: { value } }));
}

afterEach(() => vi.restoreAllMocks());

describe('persistent patient odontogram', () => {
  it('opens the shared T04 form from the selected tooth and cancelling creates no procedure or timer', async () => {
    const appointment: Appointment = {
      id: 'visit-1', clinic_id: 'clinic-1', patient_id: 'patient-A', doctor_id: doctor.id, assistant_id: null, room_id: 'room-1',
      starts_at: observedAt, ends_at: '2026-11-11T09:35:00Z', local_start: observedAt, local_end: '2026-11-11T09:35:00Z',
      timezone: 'Europe/Bucharest', duration_minutes: 30, appointment_type: 'Consultation', status: 'in_progress', notes: null,
      patient_name: 'Mara Patient', doctor_name: doctor.full_name, assistant_name: null, room_name: 'Room 1',
    };
    const catalog: ProcedureCatalog = {
      id: 'catalog-1', clinic_id: 'clinic-1', code: 'OBT-1', name: 'Composite filling', description: null, category: null,
      default_duration_minutes: 30, base_price: '250.00', currency: 'RON', is_active: true, created_at: observedAt, updated_at: observedAt,
    };
    vi.spyOn(odontogramApi, 'getPatientOdontogram').mockImplementation(async (patientId) => odontogramApi.mapPatientOdontogram(patientId, apiRecord(patientId)));
    const getAppointment = vi.spyOn(clientApi, 'getAppointment').mockResolvedValue(appointment);
    vi.spyOn(clientApi, 'listProcedures').mockResolvedValue([catalog]);
    const create = vi.spyOn(odontogramApi, 'createOdontogramFinding');
    const createProcedure = vi.spyOn(clinicalApi, 'createPerformedProcedure');
    const start = vi.spyOn(clinicalApi, 'startPerformedProcedure');
    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = TestRenderer.create(tree('patient-A', newClient(), doctor, { patientName: appointment.patient_name!, appointmentSearchTerm: 'mara@example.test', appointmentId: appointment.id })); await flush(); });
    await waitForChart(renderer);
    clickTooth(renderer, '17');
    click(renderer, 'Add performed procedure for this tooth');
    await waitForProcedureForm(renderer);
    expect(getAppointment).toHaveBeenCalledWith(appointment.id);
    expect(renderer.root.findByProps({ 'aria-label': 'Procedure tooth' }).props.value).toBe('17');
    expect(renderer.root.findAllByType('form').length).toBeGreaterThan(0);
    const cancel = renderer.root.findAllByType('button').find(button => text(button) === 'Cancel');
    act(() => cancel?.props.onClick());
    expect(renderer.root.findAllByProps({ 'aria-label': 'Add procedure for selected tooth' })).toHaveLength(0);
    expect(create).not.toHaveBeenCalled();
    expect(createProcedure).not.toHaveBeenCalled();
    expect(start).not.toHaveBeenCalled();
  });

  it('saves a finding, edits its draft, finalizes explicitly, records a reasoned correction, and reloads server state', async () => {
    let serverFinding: OdontogramFindingDto | undefined;
    const get = vi.spyOn(odontogramApi, 'getPatientOdontogram').mockImplementation(async (patientId) => odontogramApi.mapPatientOdontogram(patientId, apiRecord(patientId, serverFinding ? [serverFinding] : [])));
    const create = vi.spyOn(odontogramApi, 'createOdontogramFinding').mockImplementation(async (patientId, toothId, input, _key) => {
      serverFinding = apiFinding(patientId, { toothId, ...input, authorId: doctor.id, author: doctor.full_name });
      return serverFinding;
    });
    const update = vi.spyOn(odontogramApi, 'updateOdontogramFindingDraft').mockImplementation(async (_id, input) => {
      serverFinding = { ...serverFinding!, ...input };
      return serverFinding;
    });
    const finalize = vi.spyOn(odontogramApi, 'finalizeOdontogramFinding').mockImplementation(async () => {
      serverFinding = { ...serverFinding!, status: 'finalized' };
      return serverFinding;
    });
    const correct = vi.spyOn(odontogramApi, 'correctOdontogramFinding').mockImplementation(async (_id, input) => {
      serverFinding = {
        ...serverFinding!, ...input, status: 'finalized',
        revisions: [{
          id: 'revision-1',
          previousValue: { kind: serverFinding!.kind, description: serverFinding!.description, surfaces: serverFinding!.surfaces },
          currentValue: { kind: input.kind, description: input.description, surfaces: input.surfaces },
          reason: input.reason, occurredAt: '2026-11-11T10:00:00Z', actorId: doctor.id, author: doctor.full_name,
        }],
      };
      return serverFinding;
    });

    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = TestRenderer.create(tree('patient-A', newClient())); });
    await waitForChart(renderer);
    clickTooth(renderer, '17');
    const newDescription = renderer.root.findAllByType('textarea').find((item) => item.props.id);
    expect(newDescription).toBeDefined();
    act(() => newDescription!.props.onChange({ target: { value: 'Observație salvată pe server' } }));
    const createForm = renderer.root.findAllByType('form')[0];
    await act(async () => { createForm.props.onSubmit({ preventDefault() {} }); await flush(); });
    expect(create).toHaveBeenCalledWith('patient-A', '17', expect.objectContaining({ description: 'Observație salvată pe server' }), expect.any(String));
    expect(renderer.root.findAllByType('article').some((article) => text(article).includes('Observație salvată pe server'))).toBe(true);
    expect(renderer.root.findAllByType('article').some((article) => text(article).includes('Ciornă'))).toBe(true);
    expect(renderer.root.findAllByType('button').some((button) => text(button).includes('Editează ciorna'))).toBe(true);
    expect(renderer.root.findAllByType('button').some((button) => text(button).includes('Finalizează explicit'))).toBe(true);

    click(renderer, 'Editează ciorna');
    setTextarea(renderer, `Descriere ciornă ${findingId}`, 'Ciornă actualizată');
    await act(async () => { renderer.root.findByProps({ 'data-draft-id': findingId }).props.onSubmit({ preventDefault() {} }); await flush(); });
    expect(update).toHaveBeenCalledWith(findingId, expect.objectContaining({ description: 'Ciornă actualizată' }));

    await act(async () => { click(renderer, 'Finalizează explicit'); await flush(); });
    expect(finalize).toHaveBeenCalledWith(findingId);
    expect(renderer.root.findAll((node) => node.props['data-finding-id'] === findingId).some((node) => text(node).includes('Finalizată'))).toBe(true);

    click(renderer, 'Corectează constatarea');
    setTextarea(renderer, `Descriere corectată pentru constatarea ${findingId}`, 'Constatare corectată la reevaluare');
    setTextarea(renderer, `Motiv corecție ${findingId}`, 'Reevaluare clinică');
    await act(async () => { renderer.root.findByProps({ 'data-correction-id': findingId }).props.onSubmit({ preventDefault() {} }); await flush(); });
    expect(correct).toHaveBeenCalledWith(findingId, expect.objectContaining({ description: 'Constatare corectată la reevaluare', reason: 'Reevaluare clinică' }));
    expect(text(renderer.root).includes('Reevaluare clinică')).toBe(true);
    expect(text(renderer.root).includes(doctor.full_name)).toBe(true);
    expect(text(renderer.root).includes('Anterior: Carie: Ciornă actualizată')).toBe(true);
    expect(text(renderer.root).includes('Valoare curentă: Carie: Constatare corectată la reevaluare')).toBe(true);

    await act(async () => { renderer.unmount(); });
    let reloaded!: TestRenderer.ReactTestRenderer;
    await act(async () => { reloaded = TestRenderer.create(tree('patient-A', newClient())); });
    await waitForChart(reloaded);
    expect(get.mock.calls.length).toBeGreaterThanOrEqual(2);
    clickTooth(reloaded, '17');
    expect(reloaded.root.findAllByType('article').some((article) => text(article).includes('Constatare corectată la reevaluare'))).toBe(true);
    expect(text(reloaded.root).includes('Reevaluare clinică')).toBe(true);
  });

  it('keeps the create draft and server validation message after a failed save', async () => {
    const get = vi.spyOn(odontogramApi, 'getPatientOdontogram').mockImplementation(async (patientId) => odontogramApi.mapPatientOdontogram(patientId, apiRecord(patientId)));
    const create = vi.spyOn(odontogramApi, 'createOdontogramFinding')
      .mockRejectedValueOnce(new AxiosError('Request failed', undefined, undefined, undefined, { status: 422, statusText: 'Unprocessable Entity', headers: {}, config: { headers: {} } as never, data: { detail: 'Descrierea este obligatorie' } }))
      .mockResolvedValue(apiFinding('patient-A', { description: 'Ciornă păstrată după eroare' }));
    const client = newClient();
    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = TestRenderer.create(tree('patient-A', client)); });
    await waitForChart(renderer);
    const description = renderer.root.findAllByType('textarea').find((item) => item.props.id)!;
    act(() => description.props.onChange({ target: { value: 'Ciornă păstrată după eroare' } }));
    const form = renderer.root.findAllByType('form')[0];
    await act(async () => { form.props.onSubmit({ preventDefault() {} }); await flush(); });
    expect(renderer.root.findByProps({ id: description.props.id }).props.value).toBe('Ciornă păstrată după eroare');
    expect(renderer.root.findByProps({ role: 'alert' }).children.join('')).toContain('Descrierea este obligatorie');
    const firstKey = create.mock.calls[0]?.[3];
    await act(async () => { form.props.onSubmit({ preventDefault() {} }); await flush(); });
    expect(create.mock.calls[1]?.[3]).toBe(firstKey);
    expect(get).toHaveBeenCalled();
  });

  it('does not transfer patient A selection or draft when a delayed A response arrives after switching to B', async () => {
    let resolveDelayedA!: (value: ReturnType<typeof odontogramApi.mapPatientOdontogram>) => void;
    let callsA = 0;
    vi.spyOn(odontogramApi, 'getPatientOdontogram').mockImplementation(async (patientId) => {
      if (patientId === 'patient-A' && ++callsA > 1) return new Promise((resolve) => { resolveDelayedA = resolve; });
      const saved = patientId === 'patient-A'
        ? apiRecord(patientId, [apiFinding(patientId, { id: 'finding-A' })])
        : apiRecord(patientId, [apiFinding(patientId, { id: 'finding-B', toothId: '16', description: 'Finding B server' })]);
      return odontogramApi.mapPatientOdontogram(patientId, saved);
    });
    const client = newClient();
    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = TestRenderer.create(tree('patient-A', client)); });
    await waitForChart(renderer);
    clickTooth(renderer, '17');
    const draft = renderer.root.findAllByType('textarea').find((item) => item.props.id)!;
    act(() => draft.props.onChange({ target: { value: 'Ciornă numai pentru A' } }));
    await act(async () => { void client.refetchQueries({ queryKey: ['patient-odontogram', 'patient-A'] }); await flush(); });

    await act(async () => { renderer.update(tree('patient-B', client)); await flush(); });
    expect(renderer.root.findAllByType('article').some((article) => text(article).includes('Finding B server'))).toBe(true);
    expect(renderer.root.findAllByType('button').find((button) => button.props['aria-pressed'] === true)?.props['data-tooth-id']).toBe('16');
    expect(renderer.root.findAllByType('textarea').find((item) => item.props.id)?.props.value).toBe('');

    await act(async () => { resolveDelayedA(odontogramApi.mapPatientOdontogram('patient-A', apiRecord('patient-A', [apiFinding('patient-A', { id: 'finding-A-late', description: 'Late A result' })]))); await flush(); });
    expect(renderer.root.findAllByType('article').some((article) => text(article).includes('Late A result'))).toBe(false);
    expect(renderer.root.findAllByType('article').some((article) => text(article).includes('Finding B server'))).toBe(true);
  });

  it.each(['doctor', 'clinic_manager', 'administrator'])('%s can create findings, change tooth state, and edit/finalize their own draft', async (role) => {
    const actor: User = { ...doctor, id: `${role}-1`, role };
    const ownedDraft = apiFinding('patient-A', { authorId: actor.id, author: actor.full_name });
    vi.spyOn(odontogramApi, 'getPatientOdontogram').mockImplementation(async (patientId) => odontogramApi.mapPatientOdontogram(patientId, apiRecord(patientId, [ownedDraft])));
    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = TestRenderer.create(tree('patient-A', newClient(), actor)); });
    await waitForChart(renderer);
    clickTooth(renderer, '17');
    expect(renderer.root.findAllByType('form')).toHaveLength(1);
    expect(renderer.root.findAllByType('select').some((item) => item.props['aria-label']?.startsWith('Starea dintelui'))).toBe(true);
    expect(renderer.root.findAllByType('button').some((button) => text(button).includes('Editează ciorna'))).toBe(true);
    expect(renderer.root.findAllByType('button').some((button) => text(button).includes('Finalizează explicit'))).toBe(true);
  });

  it.each(['clinic_manager', 'administrator'])('%s cannot edit another author’s draft but can correct a finalized finding', async (role) => {
    const manager: User = { ...doctor, id: `${role}-1`, role };
    const foreignDraft = apiFinding('patient-A', { id: 'foreign-draft', authorId: 'other-doctor', author: 'Dr. Other', status: 'draft' });
    const finalized = apiFinding('patient-A', { id: 'finalized-by-other', authorId: 'other-doctor', author: 'Dr. Other', status: 'finalized' });
    vi.spyOn(odontogramApi, 'getPatientOdontogram').mockImplementation(async (patientId) => odontogramApi.mapPatientOdontogram(patientId, apiRecord(patientId, [foreignDraft, finalized])));
    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = TestRenderer.create(tree('patient-A', newClient(), manager)); });
    await waitForChart(renderer);
    clickTooth(renderer, '17');
    expect(renderer.root.findAllByType('button').some((button) => text(button).includes('Editează ciorna'))).toBe(false);
    expect(renderer.root.findAllByType('button').some((button) => text(button).includes('Finalizează explicit'))).toBe(false);
    expect(renderer.root.findAllByType('button').some((button) => text(button).includes('Corectează constatarea'))).toBe(true);
  });

  it('does not let a doctor correct another doctor’s finalized finding', async () => {
    const foreignFinalized = apiFinding('patient-A', { id: 'foreign-finalized', authorId: 'other-doctor', author: 'Dr. Other', status: 'finalized' });
    vi.spyOn(odontogramApi, 'getPatientOdontogram').mockImplementation(async (patientId) => odontogramApi.mapPatientOdontogram(patientId, apiRecord(patientId, [foreignFinalized])));
    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = TestRenderer.create(tree('patient-A', newClient())); });
    await waitForChart(renderer);
    clickTooth(renderer, '17');
    expect(renderer.root.findAllByType('form')).toHaveLength(1);
    expect(renderer.root.findAllByType('button').some((button) => text(button).includes('Corectează constatarea'))).toBe(false);
  });
});
