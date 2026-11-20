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

import { revealAndFocus } from './PatientProcedureEntry';

function queryClient() { return new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 60_000 } } }); }
async function flush() { await new Promise(resolve => setTimeout(resolve, 20)); }

afterEach(() => vi.restoreAllMocks());

describe('S4: the tooth-to-procedure panel reveals itself and takes focus', () => {
  it('scrolls the panel into view and focuses the first field when it opens, then the form field when the form is ready', async () => {
    vi.spyOn(clientApi, 'listAppointments').mockResolvedValue([appointment]);
    vi.spyOn(clientApi, 'getAppointment').mockResolvedValue(appointment);
    vi.spyOn(clientApi, 'listProcedures').mockResolvedValue([catalogItem]);
    const scrollIntoView = vi.fn();
    const focused: string[] = [];
    const panel = {
      scrollIntoView,
      querySelector: (selector: string) => ({ focus: () => focused.push(selector) }),
    };
    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => {
      renderer = TestRenderer.create(
        <QueryClientProvider client={queryClient()}><MemoryRouter>
          <PatientProcedureEntry patientId="patient-1" patientName="Mara Patient" appointmentSearchTerm={searchTerm} initialTooth="17" appointmentId={appointment.id} onCancel={vi.fn()} onSaved={vi.fn()} />
        </MemoryRouter></QueryClientProvider>,
        { createNodeMock: element => (element.type === 'section' ? panel : null) },
      );
      await flush();
    });
    for (let i = 0; i < 20 && focused.length < 2; i += 1) await act(async () => { await flush(); });
    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: 'smooth', block: 'start' });
    expect(scrollIntoView.mock.calls.length).toBeGreaterThanOrEqual(1);
    expect(focused.length).toBeGreaterThanOrEqual(2);
    expect(focused[0]).toBe('select, input, textarea');
    renderer.unmount();
  });

  it('tolerates a missing panel and a panel without fields', () => {
    expect(() => revealAndFocus(null)).not.toThrow();
    const scrollIntoView = vi.fn();
    expect(() => revealAndFocus({ scrollIntoView, querySelector: () => null })).not.toThrow();
    expect(scrollIntoView).toHaveBeenCalledOnce();
  });
});
