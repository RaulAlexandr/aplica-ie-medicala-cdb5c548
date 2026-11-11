import TestRenderer, { act } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AxiosError } from 'axios';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { User } from '../api/client';
import * as odontogramApi from './api';
import type { OdontogramFindingDto, PatientOdontogramDto } from './api';
import { ADULT_TOOTH_IDS } from './types';
import { PatientOdontogram } from './PatientOdontogram';

const doctor: User = { id: 'doctor-1', clinic_id: 'clinic-1', full_name: 'Dr. Sample', email: 'doctor@example.test', role: 'doctor' };
const findingId = 'finding-1';
const observedAt = '2026-11-11T09:05:00Z';

function apiFinding(patientId: string, values: Partial<OdontogramFindingDto> = {}): OdontogramFindingDto {
  return {
    id: findingId, toothId: '17', kind: 'caries', description: `Finding for ${patientId}`, surfaces: ['occlusal'],
    status: 'draft', observedAt, createdAt: observedAt,
    author: { id: doctor.id, name: doctor.full_name }, revisions: [], ...values,
  };
}

function apiRecord(patientId: string, findings: OdontogramFindingDto[] = []): PatientOdontogramDto {
  return { patientId, teeth: ADULT_TOOTH_IDS.map((toothId) => ({ toothId, status: 'unevaluated', updatedAt: null, updatedBy: null })), findings };
}

function text(node: TestRenderer.ReactTestInstance): string {
  return node.children.map((child) => typeof child === 'string' ? child : text(child)).join('');
}

function tree(patientId: string, client: QueryClient, user = doctor) {
  return <QueryClientProvider client={client}><PatientOdontogram key={patientId} patientId={patientId} user={user} timezone="Europe/Bucharest" /></QueryClientProvider>;
}

function newClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
}

async function flush() { await new Promise((resolve) => setTimeout(resolve, 10)); }
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
  it('saves a finding, edits its draft, finalizes explicitly, records a reasoned correction, and reloads server state', async () => {
    let serverFinding: OdontogramFindingDto | undefined;
    const get = vi.spyOn(odontogramApi, 'getPatientOdontogram').mockImplementation(async (patientId) => odontogramApi.mapPatientOdontogram(patientId, apiRecord(patientId, serverFinding ? [serverFinding] : [])));
    const create = vi.spyOn(odontogramApi, 'createOdontogramFinding').mockImplementation(async (patientId, toothId, input, _key) => {
      serverFinding = apiFinding(patientId, { toothId, ...input });
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
          reason: input.reason, occurredAt: '2026-11-11T10:00:00Z', actor: { id: doctor.id, name: doctor.full_name },
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

  it('does not expose clinical write controls to a clinic manager', async () => {
    vi.spyOn(odontogramApi, 'getPatientOdontogram').mockImplementation(async (patientId) => odontogramApi.mapPatientOdontogram(patientId, apiRecord(patientId)));
    const manager: User = { ...doctor, id: 'manager-1', role: 'clinic_manager' };
    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = TestRenderer.create(tree('patient-A', newClient(), manager)); });
    await waitForChart(renderer);
    expect(renderer.root.findAllByType('form')).toHaveLength(0);
    expect(renderer.root.findAllByType('select').some((item) => item.props['aria-label']?.startsWith('Starea dintelui'))).toBe(false);
    expect(renderer.root.findAllByType('button').some((button) => text(button).includes('Salvează'))).toBe(false);
  });
});
