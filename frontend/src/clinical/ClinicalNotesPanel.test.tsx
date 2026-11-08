import TestRenderer, { act } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AxiosError } from 'axios';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { User } from '../api/client';
import * as clinicalApi from './api';
import type { ClinicalNote } from './api';
import { ClinicalNotesPanel } from './ClinicalNotesPanel';

const doctor: User = { id: 'doctor-1', clinic_id: 'clinic-1', full_name: 'Dr. Sample', email: 'doctor@example.test', role: 'doctor' };
function note(id: string, patientId: string, content: string): ClinicalNote {
  return {
    id, clinic_id: 'clinic-1', patient_id: patientId, appointment_id: 'appointment-1', author_id: doctor.id, author_name: doctor.full_name,
    note_type: 'finding', title: `Finding ${id}`, content, tooth_fdi: '26', tooth_surface: 'occlusal', status: 'draft', is_alert: false,
    created_at: '2026-10-28T08:00:00Z', updated_at: '2026-10-28T08:00:00Z', finalized_at: null,
  };
}
function text(node: TestRenderer.ReactTestInstance): string { return node.children.map(child => typeof child === 'string' ? child : text(child)).join(''); }
function tree(patientId: string, client: QueryClient) {
  return <QueryClientProvider client={client}><ClinicalNotesPanel patientId={patientId} appointmentId="appointment-1" user={doctor} timezone="Europe/Bucharest" /></QueryClientProvider>;
}
function newClient() { return new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } }); }
async function flush() { await new Promise(resolve => setTimeout(resolve, 5)); }
function detailError(message: string) {
  const error = new AxiosError('Request failed');
  Object.defineProperty(error, 'response', { value: { data: { detail: message } } });
  return error;
}

afterEach(() => vi.restoreAllMocks());

describe('clinical draft UI', () => {
  it('keeps a draft form after a 422 and saves the exact patient and appointment association', async () => {
    const saved = note('note-B', 'patient-B', 'Persisted finding');
    let records: ClinicalNote[] = [];
    vi.spyOn(clinicalApi, 'listPatientClinicalNotes').mockImplementation(async () => records);
    const create = vi.spyOn(clinicalApi, 'createClinicalNote').mockImplementationOnce(async () => { throw detailError('Content must not be empty'); }).mockImplementation(async payload => { records = [saved]; expect(payload.patient_id).toBe('patient-B'); expect(payload.appointment_id).toBe('appointment-1'); return saved; });

    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = TestRenderer.create(tree('patient-B', newClient())); await flush(); });
    const createButton = renderer.root.findAllByType('button').find(button => text(button) === 'New note or finding');
    await act(async () => { createButton?.props.onClick(); await flush(); });
    const noteType = renderer.root.findAllByType('select')[0];
    await act(async () => { noteType?.props.onChange({ target: { value: 'finding' } }); });
    const content = renderer.root.findByType('textarea');
    await act(async () => { content.props.onChange({ target: { value: 'Finding stays in the form after an error.' } }); });
    const form = renderer.root.findByType('form');
    await act(async () => { form?.props.onSubmit({ preventDefault() {} }); await flush(); });

    expect(renderer.root.findByType('textarea').props.value).toBe('Finding stays in the form after an error.');
    expect(renderer.root.findAllByProps({ role: 'alert' }).some(node => text(node).includes('Content must not be empty'))).toBe(true);
    await act(async () => { form?.props.onSubmit({ preventDefault() {} }); await flush(); });
    expect(create).toHaveBeenLastCalledWith({ patient_id: 'patient-B', appointment_id: 'appointment-1', note_type: 'finding', content: 'Finding stays in the form after an error.', is_alert: false });
    expect(renderer.root.findAllByType('article').some(article => text(article).includes('Persisted finding'))).toBe(true);
  });

  it('reopens the persisted author draft and edits its saved content', async () => {
    const saved = note('note-1', 'patient-1', 'Saved before reload');
    vi.spyOn(clinicalApi, 'listPatientClinicalNotes').mockResolvedValue([saved]);
    vi.spyOn(clinicalApi, 'getClinicalNote').mockResolvedValue(saved);
    const update = vi.spyOn(clinicalApi, 'updateClinicalNote').mockResolvedValue({ ...saved, content: 'Saved after reopening' });

    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = TestRenderer.create(tree('patient-1', newClient())); await flush(); });
    const reopen = renderer.root.findAllByType('button').find(button => text(button) === 'Reopen draft');
    await act(async () => { reopen?.props.onClick(); await flush(); });
    expect(renderer.root.findByType('textarea').props.value).toBe('Saved before reload');
    const content = renderer.root.findByType('textarea');
    await act(async () => { content.props.onChange({ target: { value: 'Saved after reopening' } }); });
    const form = renderer.root.findByType('form');
    await act(async () => { form?.props.onSubmit({ preventDefault() {} }); await flush(); });

    expect(update).toHaveBeenCalledWith('note-1', expect.objectContaining({ content: 'Saved after reopening' }));
  });

  it('does not show a delayed response for patient A after moving to patient B', async () => {
    let resolveA!: (value: ClinicalNote[]) => void;
    const patientA = note('note-A', 'patient-A', 'Old patient response');
    const patientB = note('note-B', 'patient-B', 'Current patient response');
    vi.spyOn(clinicalApi, 'listPatientClinicalNotes').mockImplementation(patientId => patientId === 'patient-A' ? new Promise(resolve => { resolveA = resolve; }) : Promise.resolve([patientB]));
    const client = newClient();
    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = TestRenderer.create(tree('patient-A', client)); await flush(); });
    await act(async () => { renderer.update(tree('patient-B', client)); await flush(); });
    expect(renderer.root.findAllByType('article').some(article => text(article).includes('Current patient response'))).toBe(true);
    await act(async () => { resolveA([patientA]); await flush(); });
    expect(renderer.root.findAllByType('article').some(article => text(article).includes('Current patient response'))).toBe(true);
    expect(renderer.root.findAllByType('article').some(article => text(article).includes('Old patient response'))).toBe(false);
  });
});
