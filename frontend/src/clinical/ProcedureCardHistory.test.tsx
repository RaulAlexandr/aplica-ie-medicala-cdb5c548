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


function summaryOf(item: PerformedProcedure): VisitSummary {
  return {
    appointment_id: appointment.id, patient_id: appointment.patient_id, patient_name: 'Sample Patient', appointment_starts_at: appointment.starts_at,
    appointment_status: appointment.status, performed_procedures: [item], can_close_visit: false,
    unresolved_procedures: [{ id: item.id, procedure_name: item.procedure_name, status: item.status, executed_by: item.executed_by_id }],
  };
}
const runSegment: clinicalApi.ProcedureSegment = {
  id: 'segment-1', performed_procedure_id: procedure.id, segment_type: 'run', started_at: '2026-10-28T08:05:00Z',
  ended_at: null, duration_seconds: null, created_at: '2026-10-28T08:05:00Z',
};

function button(renderer: TestRenderer.ReactTestRenderer, label: string) {
  return renderer.root.findAllByType('button').find(item => text(item) === label);
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

describe('F12: history panels already open follow the server after a procedure transition', () => {
  it('refetches the open timer-segment panel after Start succeeds', async () => {
    let current = procedure;
    let segments: clinicalApi.ProcedureSegment[] = [];
    vi.spyOn(apiClient, 'getAppointment').mockResolvedValue(appointment);
    vi.spyOn(apiClient, 'listProcedures').mockResolvedValue([]);
    vi.spyOn(clinicalApi, 'getVisitSummary').mockImplementation(async () => summaryOf(current));
    const segmentsQuery = vi.spyOn(clinicalApi, 'getProcedureSegments').mockImplementation(async () => segments);
    vi.spyOn(clinicalApi, 'startPerformedProcedure').mockImplementation(async () => {
      current = { ...procedure, status: 'running' };
      segments = [runSegment];
      return current;
    });

    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = TestRenderer.create(makeTree()); await flush(); });
    await act(async () => { await flush(); });
    await act(async () => { button(renderer, 'Timer segments')?.props.onClick(); await flush(); });
    expect(text(renderer.root)).toContain('No timer segments recorded.');
    expect(segmentsQuery).toHaveBeenCalledTimes(1);

    await act(async () => { button(renderer, 'Start')?.props.onClick(); await flush(); });
    await act(async () => { await flush(); });

    expect(segmentsQuery.mock.calls.length).toBeGreaterThanOrEqual(2);
    expect(text(renderer.root)).not.toContain('No timer segments recorded.');
    expect(text(renderer.root)).toMatch(/Run · /);
  });

  it('refetches the open revisions panel after Cancel succeeds', async () => {
    let current = procedure;
    let revisions: clinicalApi.PerformedProcedureRevision[] = [];
    vi.spyOn(apiClient, 'getAppointment').mockResolvedValue(appointment);
    vi.spyOn(apiClient, 'listProcedures').mockResolvedValue([]);
    vi.spyOn(clinicalApi, 'getVisitSummary').mockImplementation(async () => summaryOf(current));
    const revisionsQuery = vi.spyOn(clinicalApi, 'getPerformedProcedureRevisions').mockImplementation(async () => revisions);
    vi.spyOn(clinicalApi, 'cancelPerformedProcedure').mockImplementation(async () => {
      current = { ...procedure, status: 'cancelled' };
      revisions = [{ id: 'rev-1', clinic_id: 'clinic-1', performed_procedure_id: procedure.id, actor_id: user.id, field: 'status', previous_value: 'pending', new_value: 'cancelled', reason: null, occurred_at: '2026-10-28T08:06:00Z' }];
      return current;
    });

    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = TestRenderer.create(makeTree()); await flush(); });
    await act(async () => { await flush(); });
    await act(async () => { button(renderer, 'Revisions')?.props.onClick(); await flush(); });
    expect(text(renderer.root)).toContain('No revisions recorded.');

    await act(async () => { button(renderer, 'Cancel')?.props.onClick(); await flush(); });
    await act(async () => { await flush(); });

    expect(revisionsQuery.mock.calls.length).toBeGreaterThanOrEqual(2);
    expect(text(renderer.root)).toContain('status: pending → cancelled');
  });
});
