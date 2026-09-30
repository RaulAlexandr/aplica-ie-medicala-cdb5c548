import TestRenderer, { act } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { clinicDate, clinicTime, Details, Form } from './Calendar';
import * as client from './api/client';

const patient = { id: 'patient-101', clinic_id: 'clinic', first_name: 'Beyond', last_name: 'Page', date_of_birth: null, sex: null, phone: null, email: 'beyond@example.com', address: null, emergency_contact: null, occupation: null, allergies: null, medications: null, chronic_diseases: null, pregnancy_status: null, smoking_status: null, previous_surgeries: null, relevant_medical_conditions: null, medical_alerts: null, notes: null, created_at: '2027-01-01T00:00:00Z', updated_at: '2027-01-01T00:00:00Z' };
const appointment = { id: 'appointment-1', clinic_id: 'clinic', patient_id: patient.id, doctor_id: 'doctor', assistant_id: 'assistant', room_id: 'room', starts_at: '2027-01-04T23:30:00Z', ends_at: '2027-01-05T00:00:00Z', local_start: '2027-01-05T01:30:00+02:00', local_end: '2027-01-05T03:00:00+02:00', timezone: 'Europe/Bucharest', duration_minutes: 90, appointment_type: 'Consultation', status: 'scheduled', notes: null, patient_name: 'Beyond Page', doctor_name: 'Doctor', assistant_name: 'Assistant', room_name: 'Room' };
function text(node: TestRenderer.ReactTestInstance): string { return node.children.filter((child): child is string => typeof child === 'string').join(''); }
function provider(child: React.ReactElement) { return <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{child}</QueryClientProvider>; }

afterEach(() => vi.restoreAllMocks());
describe('calendar timezone and permissions', () => {
  it('uses the clinic timezone at a browser date boundary', () => { expect(clinicDate('2027-01-04T23:30:00Z', 'Europe/Bucharest')).toBe('2027-01-05'); expect(clinicTime('2027-01-04T23:30:00Z', 'Europe/Bucharest')).toBe('01:30'); });
  it('does not render mutation controls for assistants', () => { const renderer = TestRenderer.create(provider(<Details appointment={appointment} canManage={false} onChanged={vi.fn()} />)); const buttons = renderer.root.findAllByType('button').map(button => text(button)); expect(buttons).not.toContain('Edit'); expect(buttons).not.toContain('Reschedule'); expect(buttons).not.toContain('Cancel'); expect(buttons).not.toContain('No-show'); expect(buttons).not.toContain('Reactivate'); });
  it('loads an edited patient outside the first page and searches beyond it', async () => {
    vi.spyOn(client, 'listPatients').mockImplementation(async (search) => search ? [patient] : []);
    vi.spyOn(client, 'getPatient').mockResolvedValue(patient); vi.spyOn(client, 'listDoctors').mockResolvedValue([{ id: 'doctor', full_name: 'Doctor', role: 'doctor' }]); vi.spyOn(client, 'listRooms').mockResolvedValue([{ id: 'room', clinic_id: 'clinic', name: 'Room', is_active: true }]); vi.spyOn(client, 'listStaff').mockResolvedValue([]);
    let renderer: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = TestRenderer.create(provider(<Form initial={appointment} onSaved={vi.fn()} />)); await Promise.resolve(); await new Promise(resolve => setTimeout(resolve, 0)); });
    expect(renderer!.root.findAllByType('option').some(option => text(option).includes('Beyond Page'))).toBe(true);
    const search = renderer!.root.findAllByType('input').find(input => input.props.placeholder === 'Name, phone or email');
    await act(async () => { search!.props.onChange({ target: { value: 'Beyond' } }); await Promise.resolve(); await new Promise(resolve => setTimeout(resolve, 0)); });
    expect(renderer!.root.findAllByType('option').some(option => text(option).includes('Beyond Page'))).toBe(true);
    expect(renderer!.root.findAllByProps({ 'aria-label': 'DST occurrence' }).length).toBe(1);
    renderer!.unmount();
  });
});
