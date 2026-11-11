import TestRenderer, { act } from 'react-test-renderer';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AxiosError } from 'axios';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as apiClient from '../api/client';
import type { Appointment, ProcedureCatalog, User } from '../api/client';
import * as clinicalApi from './api';
import type { PerformedProcedure, VisitSummary } from './api';
import { VisitPage } from './VisitPage';

const user: User = { id: 'doctor-1', clinic_id: 'clinic-1', full_name: 'Dr. Sample', email: 'doctor@example.test', role: 'doctor' };
const appointment: Appointment = {
  id: 'appointment-1', clinic_id: 'clinic-1', patient_id: 'patient-1', doctor_id: user.id, assistant_id: null, room_id: 'room-1',
  starts_at: '2026-10-28T08:00:00Z', ends_at: '2026-10-28T08:30:00Z', local_start: '2026-10-28T10:00:00+02:00', local_end: '2026-10-28T10:30:00+02:00',
  timezone: 'Europe/Bucharest', duration_minutes: 30, appointment_type: 'Consultation', status: 'in_progress', notes: null,
  patient_name: 'Sample Patient', doctor_name: 'Dr. Sample', assistant_name: null, room_name: 'Room 1',
};
const procedure: PerformedProcedure = {
  id: 'performed-1', clinic_id: 'clinic-1', appointment_id: appointment.id, patient_id: appointment.patient_id, procedure_catalog_id: 'catalog-1', executed_by_id: user.id,
  procedure_code: 'EX-1', procedure_name: 'Examination', procedure_base_price: '125.50', procedure_currency: 'RON', procedure_default_duration: 30,
  tooth_fdi: null, tooth_surface: null, status: 'pending', current_price: '125.50', total_duration_seconds: null, notes: null,
  created_at: '2026-10-28T08:00:00Z', updated_at: '2026-10-28T08:00:00Z',
};

function summary(item: PerformedProcedure, canClose: boolean): VisitSummary {
  return {
    appointment_id: appointment.id, patient_id: appointment.patient_id, patient_name: 'Sample Patient', appointment_starts_at: appointment.starts_at,
    appointment_status: appointment.status, performed_procedures: [item], can_close_visit: canClose,
    unresolved_procedures: canClose ? [] : [{ id: item.id, procedure_name: item.procedure_name, status: item.status, executed_by: item.executed_by_id }],
  };
}

function text(node: TestRenderer.ReactTestInstance): string { return node.children.map(child => typeof child === 'string' ? child : text(child)).join(''); }
function makeTree(currentUser: User = user) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}><MemoryRouter initialEntries={[`/appointments/${appointment.id}/visit`]}><Routes><Route path="/appointments/:appointmentId/visit" element={<VisitPage user={currentUser} />} /></Routes></MemoryRouter></QueryClientProvider>;
}
async function flush() { await new Promise(resolve => setTimeout(resolve, 50)); }
async function waitForProcedureForm(renderer: TestRenderer.ReactTestRenderer) {
  for (let index = 0; index < 20; index += 1) {
    if (renderer.root.findAllByType('form').length) return renderer.root.findByType('form');
    await act(async () => { await flush(); });
  }
  throw new Error('Shared procedure form did not load');
}

afterEach(() => vi.restoreAllMocks());

describe('visit UI', () => {
  it('uses the shared T04 form for procedure-to-tooth and saves a pending record without starting its timer', async () => {
    const catalogItem: ProcedureCatalog = {
      id: 'catalog-1', clinic_id: 'clinic-1', code: 'OBT-1', name: 'Composite filling', description: null, category: 'restorative',
      default_duration_minutes: 30, base_price: '250.00', currency: 'RON', is_active: true,
      created_at: '2026-11-01T00:00:00Z', updated_at: '2026-11-01T00:00:00Z',
    };
    const saved = { ...procedure, id: 'performed-from-visit', procedure_catalog_id: catalogItem.id, procedure_code: catalogItem.code, procedure_name: catalogItem.name, tooth_fdi: '17', tooth_surface: 'occlusal,mesial' };
    let currentSummary: VisitSummary = { ...summary(procedure, false), performed_procedures: [], unresolved_procedures: [] };
    vi.spyOn(apiClient, 'getAppointment').mockResolvedValue(appointment);
    vi.spyOn(apiClient, 'listProcedures').mockResolvedValue([catalogItem]);
    vi.spyOn(clinicalApi, 'getVisitSummary').mockImplementation(async () => currentSummary);
    const create = vi.spyOn(clinicalApi, 'createPerformedProcedure').mockImplementation(async payload => {
      expect(payload).toEqual({ appointment_id: appointment.id, procedure_catalog_id: catalogItem.id, tooth_fdi: '17', tooth_surface: 'occlusal,mesial', notes: 'Restoration' });
      currentSummary = summary(saved, false);
      return saved;
    });
    const start = vi.spyOn(clinicalApi, 'startPerformedProcedure');

    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = TestRenderer.create(makeTree()); await flush(); });
    const form = await waitForProcedureForm(renderer);
    const selectors = form.findAllByType('select');
    expect(text(form).includes('Sample Patient')).toBe(true);
    expect(text(form).includes('Selected visit')).toBe(true);
    await act(async () => { selectors[0]?.props.onChange({ target: { value: catalogItem.id } }); });
    await act(async () => { selectors[1]?.props.onChange({ target: { value: '17' } }); });
    for (const surface of ['Occlusal', 'Mesial']) {
      const label = renderer.root.findAllByType('label').find(item => text(item).trim() === surface);
      expect(label).toBeDefined();
      await act(async () => { label!.findByType('input').props.onChange({ target: { checked: true } }); });
    }
    await act(async () => { form.findByType('textarea').props.onChange({ target: { value: 'Restoration' } }); });
    await act(async () => { form.props.onSubmit({ preventDefault() {} }); await flush(); });

    expect(create).toHaveBeenCalledTimes(1);
    for (let attempt = 0; attempt < 20 && !renderer.root.findAllByType('article').some(item => text(item).includes('Composite filling')); attempt += 1) await act(async () => { await flush(); });
    expect(renderer.root.findAllByType('article').some(item => text(item).includes('Composite filling'))).toBe(true);
    expect(renderer.root.findAllByType('button').some(button => text(button) === 'Start')).toBe(true);
    expect(start).not.toHaveBeenCalled();
  });

  it('shows server 409 on a procedure action and keeps visit closure disabled while unresolved', async () => {
    const conflict = new AxiosError('Request failed');
    Object.defineProperty(conflict, 'response', { value: { data: { detail: 'Doctor already has a running procedure. Only one timer per doctor is allowed.' } } });
    vi.spyOn(apiClient, 'getAppointment').mockResolvedValue(appointment);
    vi.spyOn(apiClient, 'listProcedures').mockResolvedValue([]);
    const summaryQuery = vi.spyOn(clinicalApi, 'getVisitSummary').mockResolvedValue(summary(procedure, false));
    const startMutation = vi.spyOn(clinicalApi, 'startPerformedProcedure').mockRejectedValue(conflict);

    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = TestRenderer.create(makeTree()); await flush(); });
    expect(summaryQuery).toHaveBeenCalledWith(appointment.id);
    await act(async () => { await summaryQuery.mock.results[0]?.value; await flush(); });
    const close = renderer.root.findAllByType('button').find(button => text(button) === 'Close visit');
    expect(close?.props.disabled).toBe(true);
    const start = renderer.root.findAllByType('button').find(button => text(button) === 'Start');
    expect(start).toBeDefined();
    await act(async () => { start?.props.onClick(); await flush(); });

    expect(startMutation).toHaveBeenCalledWith('performed-1');
    expect(renderer.root.findAllByProps({ role: 'alert' }).map(node => text(node)).join(' ')).toContain('Doctor already has a running procedure');
    expect(renderer.root.findAllByType('textarea')).toHaveLength(1);
  });

  it('keeps appointment open after completing a procedure and enables separate closure', async () => {
    const paused = { ...procedure, status: 'paused' as const, total_duration_seconds: 45 };
    const completed = { ...paused, status: 'completed' as const };
    let currentSummary = summary(paused, false);
    const updateStatus = vi.spyOn(apiClient, 'updateAppointmentStatus').mockResolvedValue({ ...appointment, status: 'completed' });
    vi.spyOn(apiClient, 'getAppointment').mockResolvedValue(appointment);
    vi.spyOn(apiClient, 'listProcedures').mockResolvedValue([]);
    const summaryQuery = vi.spyOn(clinicalApi, 'getVisitSummary').mockImplementation(async () => currentSummary);
    const completeMutation = vi.spyOn(clinicalApi, 'completePerformedProcedure').mockImplementation(async () => {
      currentSummary = summary(completed, true);
      return completed;
    });

    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = TestRenderer.create(makeTree()); await flush(); });
    expect(summaryQuery).toHaveBeenCalledWith(appointment.id);
    await act(async () => { await summaryQuery.mock.results[0]?.value; await flush(); });
    const complete = renderer.root.findAllByType('button').find(button => text(button) === 'Complete');
    expect(complete).toBeDefined();
    await act(async () => { complete?.props.onClick(); await flush(); });

    expect(completeMutation).toHaveBeenCalledWith('performed-1');
    const close = renderer.root.findAllByType('button').find(button => text(button) === 'Close visit');
    expect(close?.props.disabled).toBe(false);
    expect(renderer.root.findAllByType('h2').some(node => text(node) === 'in progress')).toBe(true);
    expect(updateStatus).not.toHaveBeenCalled();
  });

  it('shows a server 409 when a close request races with a newly unresolved procedure', async () => {
    const completed = { ...procedure, status: 'completed' as const };
    const conflict = new AxiosError('Request failed');
    Object.defineProperty(conflict, 'response', { value: { data: { detail: 'Cannot close appointment: unresolved procedures exist.' } } });
    vi.spyOn(apiClient, 'getAppointment').mockResolvedValue(appointment);
    vi.spyOn(apiClient, 'listProcedures').mockResolvedValue([]);
    vi.spyOn(clinicalApi, 'getVisitSummary').mockResolvedValue(summary(completed, true));
    const closeRequest = vi.spyOn(apiClient, 'updateAppointmentStatus').mockRejectedValue(conflict);

    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = TestRenderer.create(makeTree()); await flush(); });
    const summaryRequest = vi.mocked(clinicalApi.getVisitSummary).mock.results[0]?.value;
    await act(async () => { await summaryRequest; await flush(); });
    const close = renderer.root.findAllByType('button').find(button => text(button) === 'Close visit');
    expect(close?.props.disabled).toBe(false);
    await act(async () => { close?.props.onClick(); await flush(); });

    expect(closeRequest).toHaveBeenCalledWith(appointment.id, 'completed');
    expect(renderer.root.findAllByProps({ role: 'alert' }).map(node => text(node)).join(' ')).toContain('Cannot close appointment: unresolved procedures exist.');
    expect(renderer.root.findAllByType('h2').some(node => text(node) === 'in progress')).toBe(true);
  });

  it('keeps procedure details out of the reception visit view', async () => {
    const receptionist = { ...user, id: 'reception-1', role: 'reception' };
    vi.spyOn(apiClient, 'getAppointment').mockResolvedValue(appointment);
    const catalogRequest = vi.spyOn(apiClient, 'listProcedures').mockResolvedValue([]);
    const summaryRequest = vi.spyOn(clinicalApi, 'getVisitSummary').mockResolvedValue(summary(procedure, false));

    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = TestRenderer.create(makeTree(receptionist)); await flush(); });
    await act(async () => { await summaryRequest.mock.results[0]?.value; await flush(); });

    expect(catalogRequest).not.toHaveBeenCalled();
    expect(renderer.root.findAllByType('h3').some(node => text(node).includes('Examination'))).toBe(false);
    expect(renderer.root.findAllByType('button').some(button => text(button) === 'Start')).toBe(false);
    expect(renderer.root.findAllByType('button').find(button => text(button) === 'Close visit')?.props.disabled).toBe(true);
  });
});
