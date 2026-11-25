import TestRenderer, { act } from 'react-test-renderer';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AvailabilitySettings } from './AvailabilitySettings';
import { Calendar, clinicDate, clinicTime, Details, Form } from './Calendar';
import * as client from './api/client';
import type { Unavailability } from './api/client';
import { clinicLocalToUtc } from './timezone';
import * as appointmentProceduresApi from './appointment_procedures/api';

const patient = { id: 'patient-101', clinic_id: 'clinic', first_name: 'Beyond', last_name: 'Page', date_of_birth: null, sex: null, phone: null, email: 'beyond@example.com', address: null, emergency_contact: null, occupation: null, allergies: null, medications: null, chronic_diseases: null, pregnancy_status: null, smoking_status: null, previous_surgeries: null, relevant_medical_conditions: null, medical_alerts: null, notes: null, created_at: '2027-01-01T00:00:00Z', updated_at: '2027-01-01T00:00:00Z' };
const appointment = { id: 'appointment-1', clinic_id: 'clinic', patient_id: patient.id, doctor_id: 'doctor', assistant_id: 'assistant', room_id: 'room', starts_at: '2027-01-04T23:30:00Z', ends_at: '2027-01-05T00:00:00Z', local_start: '2027-01-05T01:30:00+02:00', local_end: '2027-01-05T03:00:00+02:00', timezone: 'Europe/Bucharest', duration_minutes: 90, appointment_type: 'Consultation', status: 'scheduled', notes: null, patient_name: 'Beyond Page', doctor_name: 'Doctor', assistant_name: 'Assistant', room_name: 'Room' };
function text(node: TestRenderer.ReactTestInstance): string { return node.children.filter((child): child is string => typeof child === 'string').join(''); }
function provider(child: React.ReactElement, client = new QueryClient({ defaultOptions: { queries: { retry: false } } })) { return <QueryClientProvider client={client}><MemoryRouter>{child}</MemoryRouter></QueryClientProvider>; }

afterEach(() => vi.restoreAllMocks());
describe('calendar timezone and permissions', () => {
  it('uses the clinic timezone at a browser date boundary', () => { expect(clinicDate('2027-01-04T23:30:00Z', 'Europe/Bucharest')).toBe('2027-01-05'); expect(clinicTime('2027-01-04T23:30:00Z', 'Europe/Bucharest')).toBe('01:30'); });
  it('formats clinic appointments in a non-Bucharest zone when the browser is UTC', () => {
    vi.stubEnv('TZ', 'UTC');
    expect(Intl.DateTimeFormat().resolvedOptions().timeZone).toBe('UTC');
    expect(clinicDate('2027-01-04T23:30:00Z', 'America/Los_Angeles')).toBe('2027-01-04');
    expect(clinicTime('2027-01-04T23:30:00Z', 'America/Los_Angeles')).toBe('15:30');
  });
  it('rejects nonexistent and ambiguous times and resolves both Bucharest occurrences', () => {
    expect(() => clinicLocalToUtc('2027-03-28T03:30', 'Europe/Bucharest')).toThrow(/does not exist/);
    expect(() => clinicLocalToUtc('2027-10-31T03:30', 'Europe/Bucharest')).toThrow(/ambiguous/);
    expect(clinicLocalToUtc('2027-10-31T03:30', 'Europe/Bucharest', 0)).toBe('2027-10-31T00:30:00.000Z');
    expect(clinicLocalToUtc('2027-10-31T03:30', 'Europe/Bucharest', 1)).toBe('2027-10-31T01:30:00.000Z');
    expect(clinicLocalToUtc('2027-02-10T10:15', 'Europe/Bucharest', 1)).toBe('2027-02-10T08:15:00.000Z');
  });
  it('does not render mutation controls for assistants', () => { const renderer = TestRenderer.create(provider(<Details appointment={appointment} canManage={false} onChanged={vi.fn()} />)); const buttons = renderer.root.findAllByType('button').map(button => text(button)); expect(buttons).not.toContain('Edit'); expect(buttons).not.toContain('Reschedule'); expect(buttons).not.toContain('Cancel'); expect(buttons).not.toContain('No-show'); expect(buttons).not.toContain('Reactivate'); });
  it('loads an edited patient outside the first page and searches beyond it', async () => {
    vi.spyOn(client, 'listPatients').mockImplementation(async (search) => search ? [patient] : []);
    vi.spyOn(client, 'getPatient').mockResolvedValue(patient); vi.spyOn(client, 'listDoctors').mockResolvedValue([{ id: 'doctor', full_name: 'Doctor', role: 'doctor' }]); vi.spyOn(client, 'listRooms').mockResolvedValue([{ id: 'room', clinic_id: 'clinic', name: 'Room', is_active: true }]); vi.spyOn(client, 'listStaff').mockResolvedValue([]);
    let renderer: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = TestRenderer.create(provider(<Form initial={appointment} onSaved={vi.fn()} />)); await Promise.resolve(); await new Promise(resolve => setTimeout(resolve, 10)); });
    expect(renderer!.root.findAllByType('option').some(option => text(option).includes('Beyond Page'))).toBe(true);
    const search = renderer!.root.findAllByType('input').find(input => input.props.placeholder === 'Name, phone or email');
    await act(async () => { search!.props.onChange({ target: { value: 'Beyond' } }); await Promise.resolve(); await new Promise(resolve => setTimeout(resolve, 10)); });
    expect(renderer!.root.findAllByType('option').some(option => text(option).includes('Beyond Page'))).toBe(true);
    expect(renderer!.root.findAllByProps({ 'aria-label': 'DST occurrence' }).length).toBe(1);
    renderer!.unmount();
  });
});

const patientB = { ...patient, id: 'patient-202', first_name: 'Second', last_name: 'Patient' };
const appointmentB = { ...appointment, id: 'appointment-2', patient_id: patientB.id, starts_at: '2027-01-06T02:15:00Z', ends_at: '2027-01-06T02:45:00Z', local_start: '2027-01-06T04:15:00+02:00', local_end: '2027-01-06T04:45:00+02:00', appointment_type: 'Treatment', notes: 'B notes', patient_name: 'Second Patient' };

function calendarEvents(renderer: TestRenderer.ReactTestRenderer) {
  return renderer.root.findAllByType('button').filter(button => String(button.props.className).startsWith('calendar-event'));
}

async function renderCalendar(canManage = true): Promise<TestRenderer.ReactTestRenderer> {
  vi.spyOn(appointmentProceduresApi, 'getAppointmentProcedures').mockResolvedValue({ appointment_id: 'appointment-1', revision: 0, procedures: [] });
  vi.spyOn(client, 'listProcedures').mockResolvedValue([]);
  vi.spyOn(client, 'getClinic').mockResolvedValue({ timezone: 'Europe/Bucharest' } as never);
  vi.spyOn(client, 'listAppointments').mockResolvedValue([appointment, appointmentB]);
  vi.spyOn(client, 'listRooms').mockResolvedValue([{ id: 'room', clinic_id: 'clinic', name: 'Room', is_active: true }]);
  vi.spyOn(client, 'listDoctors').mockResolvedValue([{ id: 'doctor', full_name: 'Doctor', role: 'doctor' }]);
  vi.spyOn(client, 'listStaff').mockResolvedValue([]);
  vi.spyOn(client, 'listPatients').mockResolvedValue([patient, patientB]);
  vi.spyOn(client, 'getPatient').mockImplementation(async id => id === patientB.id ? patientB : patient);
  let renderer!: TestRenderer.ReactTestRenderer;
  await act(async () => {
    renderer = TestRenderer.create(provider(<Calendar canManage={canManage} />));
    await new Promise(resolve => setTimeout(resolve, 10));
  });
  // A fixed 10 ms tick is not always enough under a loaded parallel run: wait (bounded) for both fixture appointments.
  for (let attempt = 0; attempt < 50 && calendarEvents(renderer).length < 2; attempt += 1) {
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); });
  }
  return renderer;
}

describe('calendar selection state', () => {
  it('does not carry unsaved edit values from appointment A into B', async () => {
    const update = vi.spyOn(client, 'updateAppointment').mockResolvedValue(appointmentB);
    const renderer = await renderCalendar();
    await act(async () => { calendarEvents(renderer)[0].props.onClick(); });
    await act(async () => { renderer.root.findAllByType('button').find(button => text(button) === 'Edit')!.props.onClick(); await new Promise(resolve => setTimeout(resolve, 10)); });
    await act(async () => { renderer.root.findByType('textarea').props.onChange({ target: { value: 'unsaved A notes' } }); });
    await act(async () => { calendarEvents(renderer)[1].props.onClick(); });
    await act(async () => { renderer.root.findAllByType('button').find(button => text(button) === 'Edit')!.props.onClick(); await new Promise(resolve => setTimeout(resolve, 10)); });

    expect(renderer.root.findByType('textarea').props.value).toBe('B notes');
    await act(async () => {
      renderer.root.findByType('form').props.onSubmit({ preventDefault() {} });
      await new Promise(resolve => setTimeout(resolve, 10));
    });
    expect(update).toHaveBeenCalledWith('appointment-2', expect.objectContaining({ patient_id: patientB.id, notes: 'B notes' }));
    renderer.unmount();
  });

  it('uses B’s reschedule time and clears A’s DST and error state', async () => {
    const reschedule = vi.spyOn(client, 'rescheduleAppointment')
      .mockRejectedValueOnce(new Error('A reschedule failed'))
      .mockResolvedValue(appointmentB);
    const renderer = await renderCalendar();
    await act(async () => { calendarEvents(renderer)[0].props.onClick(); });
    const timeInput = () => renderer.root.findAllByType('input').find(input => input.props.type === 'datetime-local')!;
    await act(async () => {
      timeInput().props.onChange({ target: { value: '2027-01-08T03:30' } });
      renderer.root.findByProps({ 'aria-label': 'DST occurrence' }).props.onChange({ target: { value: '1' } });
      renderer.root.findAllByType('button').find(button => text(button) === 'Reschedule')!.props.onClick();
      await new Promise(resolve => setTimeout(resolve, 10));
    });
    expect(renderer.root.findByProps({ role: 'alert' }).children.join('')).toContain('A reschedule failed');

    await act(async () => { calendarEvents(renderer)[1].props.onClick(); });
    expect(timeInput().props.value).toBe('2027-01-06T04:15');
    expect(renderer.root.findByProps({ 'aria-label': 'DST occurrence' }).props.value).toBe('');
    expect(renderer.root.findAllByProps({ role: 'alert' })).toHaveLength(0);
    await act(async () => {
      renderer.root.findAllByType('button').find(button => text(button) === 'Reschedule')!.props.onClick();
      await new Promise(resolve => setTimeout(resolve, 10));
    });
    expect(reschedule).toHaveBeenLastCalledWith('appointment-2', { local_start: '2027-01-06T04:15', timezone_fold: undefined });
    renderer.unmount();
  });
});

const catalogProcedure = { id: 'procedure-1', clinic_id: 'clinic', code: 'P1', name: 'Consultație', description: null, category: null, default_duration_minutes: 30, base_price: '10', currency: 'RON', allowed_location: 'any' as const, is_active: true, created_at: '', updated_at: '' };
async function renderNewForm(onSaved = vi.fn(), queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  vi.spyOn(client, 'listPatients').mockResolvedValue([patient]);
  vi.spyOn(client, 'listDoctors').mockResolvedValue([{ id: 'doctor', full_name: 'Doctor', role: 'doctor' }]);
  vi.spyOn(client, 'listRooms').mockResolvedValue([{ id: 'room', clinic_id: 'clinic', name: 'Room', is_active: true }]);
  vi.spyOn(client, 'listStaff').mockResolvedValue([]);
  vi.spyOn(client, 'listProcedures').mockResolvedValue([catalogProcedure]);
  let renderer!: TestRenderer.ReactTestRenderer;
  await act(async () => { renderer = TestRenderer.create(provider(<Form onSaved={onSaved} />, queryClient)); await new Promise(resolve => setTimeout(resolve, 10)); });
  await act(async () => {
    const selects = renderer.root.findAllByType('select');
    selects[0].props.onChange({ target: { value: patient.id } });
    selects[1].props.onChange({ target: { value: 'doctor' } });
    selects[2].props.onChange({ target: { value: 'room' } });
    const start = renderer.root.findAllByType('input').find(item => item.props.type === 'datetime-local');
    start!.props.onChange({ target: { value: '2027-02-10T10:15' } });
  });
  return { renderer, onSaved };
}
const createdAppointment = { ...appointment, id: 'appointment-created' };

describe('planned procedures on appointment creation', () => {
  it('posts once and does not put when no procedure was selected', async () => {
    const create = vi.spyOn(client, 'createAppointment').mockResolvedValue(createdAppointment);
    const getSet = vi.spyOn(appointmentProceduresApi, 'getAppointmentProcedures');
    const put = vi.spyOn(appointmentProceduresApi, 'replaceAppointmentProcedures');
    const { renderer, onSaved } = await renderNewForm();
    await act(async () => { renderer.root.findByProps({ 'aria-label': 'Caută în catalog' }).props.onChange({ target: { value: 'Consultație' } }); await new Promise(resolve => setTimeout(resolve, 5)); });
    expect(renderer.root.findAllByType('option').some(option => text(option).includes('Consultație'))).toBe(true);
    expect(renderer.root.findAllByType('input').some(input => input.props['aria-label'] === 'Cantitate Consultație')).toBe(false);
    await act(async () => { renderer.root.findByType('form').props.onSubmit({ preventDefault() {} }); await new Promise(resolve => setTimeout(resolve, 5)); });
    expect(create).toHaveBeenCalledTimes(1); expect(getSet).not.toHaveBeenCalled(); expect(put).not.toHaveBeenCalled(); expect(onSaved).toHaveBeenCalledTimes(1);
    renderer.unmount();
  });

  it('adds a catalog line explicitly and creates then saves it with the fetched revision', async () => {
    const create = vi.spyOn(client, 'createAppointment').mockResolvedValue(createdAppointment);
    const getSet = vi.spyOn(appointmentProceduresApi, 'getAppointmentProcedures').mockResolvedValue({ appointment_id: createdAppointment.id, revision: 7, procedures: [] });
    const put = vi.spyOn(appointmentProceduresApi, 'replaceAppointmentProcedures').mockResolvedValue({ appointment_id: createdAppointment.id, revision: 8, procedures: [] });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } }); const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    const { renderer, onSaved } = await renderNewForm(vi.fn(), queryClient);
    const search = renderer.root.findAllByType('input').find(input => input.props['aria-label'] === 'Caută în catalog');
    expect(search).toBeDefined();
    const active = renderer.root.findAllByType('select').find(item => item.props['aria-label'] === 'Procedură activă')!;
    expect(create).not.toHaveBeenCalled();
    await act(async () => { active.props.onChange({ target: { value: catalogProcedure.id } }); });
    const add = renderer.root.findAllByType('button').find(button => text(button) === 'Adaugă')!;
    await act(async () => { add.props.onClick(); });
    expect(renderer.root.findAllByType('input').some(input => input.props['aria-label'] === 'Cantitate Consultație')).toBe(true);
    await act(async () => { renderer.root.findByType('form').props.onSubmit({ preventDefault() {} }); await new Promise(resolve => setTimeout(resolve, 5)); });
    expect(getSet).toHaveBeenCalledWith(createdAppointment.id);
    expect(put).toHaveBeenCalledWith(createdAppointment.id, 7, [{ procedure_catalog_id: catalogProcedure.id, quantity: 1 }]);
    expect(create).toHaveBeenCalledTimes(1); expect(onSaved).toHaveBeenCalledTimes(1);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['materials-requirements'] });
    renderer.unmount();
  });

  it('keeps the created appointment after PUT failure and retries only the PUT', async () => {
    const create = vi.spyOn(client, 'createAppointment').mockResolvedValue(createdAppointment);
    const getSet = vi.spyOn(appointmentProceduresApi, 'getAppointmentProcedures').mockResolvedValueOnce({ appointment_id: createdAppointment.id, revision: 3, procedures: [] }).mockResolvedValueOnce({ appointment_id: createdAppointment.id, revision: 4, procedures: [] });
    const put = vi.spyOn(appointmentProceduresApi, 'replaceAppointmentProcedures').mockRejectedValueOnce(new Error('network')).mockResolvedValue({ appointment_id: createdAppointment.id, revision: 4, procedures: [] });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } }); const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    const { renderer, onSaved } = await renderNewForm(vi.fn(), queryClient);
    const active = renderer.root.findAllByType('select').find(item => item.props['aria-label'] === 'Procedură activă')!;
    await act(async () => { active.props.onChange({ target: { value: catalogProcedure.id } }); });
    await act(async () => { renderer.root.findAllByType('button').find(button => text(button) === 'Adaugă')!.props.onClick(); });
    const submit = () => renderer.root.findByType('form').props.onSubmit({ preventDefault() {} });
    await act(async () => { submit(); await new Promise(resolve => setTimeout(resolve, 5)); });
    expect(renderer.root.findByProps({ role: 'alert' }).children.join('')).toContain('Programarea a fost creată, dar procedurile nu s-au salvat.');
    expect(create).toHaveBeenCalledTimes(1); expect(onSaved).not.toHaveBeenCalled();
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['appointments'] });
    const fields = renderer.root.findByType('form').findAll(node => ['input', 'select', 'textarea'].includes(node.type as string));
    for (const field of fields.filter(node => node.props['aria-label'] !== 'Cantitate Consultație' && node.props['aria-label'] !== 'Procedură activă' && node.props['aria-label'] !== 'Caută în catalog')) expect(field.props.disabled).toBe(true);
    expect(renderer.root.findByProps({ 'aria-label': 'Cantitate Consultație' }).props.disabled).toBeFalsy();
    expect(renderer.root.findAllByType('button').some(button => text(button) === 'Reîncearcă salvarea procedurilor')).toBe(true);
    await act(async () => { submit(); await new Promise(resolve => setTimeout(resolve, 5)); });
    expect(create).toHaveBeenCalledTimes(1); expect(put).toHaveBeenCalledTimes(2); expect(onSaved).toHaveBeenCalledTimes(1);
    expect(getSet).toHaveBeenCalledTimes(2);
    expect(put).toHaveBeenNthCalledWith(1, createdAppointment.id, 3, [{ procedure_catalog_id: catalogProcedure.id, quantity: 1 }]);
    expect(put).toHaveBeenNthCalledWith(2, createdAppointment.id, 4, [{ procedure_catalog_id: catalogProcedure.id, quantity: 1 }]);
    renderer.unmount();
  });

  it('refreshes the procedure revision after a real 409 before retrying', async () => {
    vi.spyOn(client, 'createAppointment').mockResolvedValue(createdAppointment);
    vi.spyOn(appointmentProceduresApi, 'getAppointmentProcedures').mockResolvedValueOnce({ appointment_id: createdAppointment.id, revision: 3, procedures: [] }).mockResolvedValueOnce({ appointment_id: createdAppointment.id, revision: 4, procedures: [] });
    const put = vi.spyOn(appointmentProceduresApi, 'replaceAppointmentProcedures').mockRejectedValueOnce(new Error('409 Conflict')).mockResolvedValue({ appointment_id: createdAppointment.id, revision: 5, procedures: [] });
    const { renderer } = await renderNewForm();
    await act(async () => { renderer.root.findAllByType('select').find(item => item.props['aria-label'] === 'Procedură activă')!.props.onChange({ target: { value: catalogProcedure.id } }); });
    await act(async () => { renderer.root.findAllByType('button').find(button => text(button) === 'Adaugă')!.props.onClick(); });
    const submit = () => renderer.root.findByType('form').props.onSubmit({ preventDefault() {} });
    await act(async () => { submit(); await new Promise(resolve => setTimeout(resolve, 5)); });
    await act(async () => { submit(); await new Promise(resolve => setTimeout(resolve, 5)); });
    expect(put).toHaveBeenNthCalledWith(2, createdAppointment.id, 4, [{ procedure_catalog_id: catalogProcedure.id, quantity: 1 }]);
    expect(vi.mocked(client.createAppointment)).toHaveBeenCalledTimes(1);
    renderer.unmount();
  });

  it('keeps the walkthrough button labels and includes Romanian DST guidance on POST errors', async () => {
    vi.spyOn(client, 'createAppointment').mockRejectedValue(new Error('server detail'));
    const { renderer } = await renderNewForm();
    const button = () => renderer.root.findAllByType('button').find(item => item.props.type !== 'button' && !['Adaugă'].includes(text(item)))!;
    expect(text(button())).toBe('Create appointment');
    const submit = renderer.root.findByType('form').props.onSubmit;
    await act(async () => { submit({ preventDefault() {} }); await new Promise(resolve => setTimeout(resolve, 5)); });
    expect(renderer.root.findByProps({ role: 'alert' }).children.join('')).toContain('server detail');
    expect(renderer.root.findByProps({ role: 'alert' }).children.join('')).toContain('Dacă ora locală se repetă, alege prima sau a doua apariție. Orele care nu există la schimbarea orei trebuie modificate.');
    renderer.unmount();
  });

  it('shows the walkthrough saving label while POST is pending', async () => {
    let resolveCreate!: (value: typeof createdAppointment) => void;
    vi.spyOn(client, 'createAppointment').mockImplementation(() => new Promise(resolve => { resolveCreate = resolve; }));
    const { renderer } = await renderNewForm();
    const submit = renderer.root.findByType('form').props.onSubmit;
    await act(async () => { submit({ preventDefault() {} }); await Promise.resolve(); });
    expect(renderer.root.findAllByType('button').some(button => text(button) === 'Saving…' && button.props.disabled)).toBe(true);
    await act(async () => { resolveCreate(createdAppointment); await new Promise(resolve => setTimeout(resolve, 5)); });
    renderer.unmount();
  });

  it('does not show planned procedures when creating is unauthorized or editing', async () => {
    const calendar = await renderCalendar(false);
    expect(calendar.root.findAllByProps({ 'aria-label': 'Proceduri planificate la creare' })).toHaveLength(0);
    expect(calendar.root.findAllByType('button').some(button => text(button) === 'New appointment')).toBe(false);
    calendar.unmount();
    const edit = TestRenderer.create(provider(<Form initial={appointment} onSaved={vi.fn()} />));
    expect(edit.root.findAllByProps({ 'aria-label': 'Proceduri planificate la creare' })).toHaveLength(0);
    edit.unmount();
  });

  it('preserves form values after POST failure and retries without losing selected lines', async () => {
    const create = vi.spyOn(client, 'createAppointment').mockRejectedValueOnce(new Error('offline')).mockResolvedValue(createdAppointment);
    const put = vi.spyOn(appointmentProceduresApi, 'replaceAppointmentProcedures').mockResolvedValue({ appointment_id: createdAppointment.id, revision: 1, procedures: [] });
    vi.spyOn(appointmentProceduresApi, 'getAppointmentProcedures').mockResolvedValue({ appointment_id: createdAppointment.id, revision: 0, procedures: [] });
    const { renderer, onSaved } = await renderNewForm();
    const active = renderer.root.findAllByType('select').find(item => item.props['aria-label'] === 'Procedură activă')!;
    await act(async () => { active.props.onChange({ target: { value: catalogProcedure.id } }); });
    await act(async () => { renderer.root.findAllByType('button').find(button => text(button) === 'Adaugă')!.props.onClick(); });
    const submit = () => renderer.root.findByType('form').props.onSubmit({ preventDefault() {} });
    await act(async () => { submit(); await new Promise(resolve => setTimeout(resolve, 5)); });
    expect(renderer.root.findByProps({ role: 'alert' }).children.join('')).toContain('Programarea nu a putut fi creată.');
    expect(renderer.root.findByProps({ 'aria-label': 'Cantitate Consultație' }).props.value).toBe(1);
    await act(async () => { submit(); await new Promise(resolve => setTimeout(resolve, 5)); });
    expect(create).toHaveBeenCalledTimes(2); expect(put).toHaveBeenCalledTimes(1); expect(onSaved).toHaveBeenCalledTimes(1);
    renderer.unmount();
  });

  it('blocks invalid quantities and guards against double submit', async () => {
    const create = vi.spyOn(client, 'createAppointment').mockResolvedValue(createdAppointment);
    const put = vi.spyOn(appointmentProceduresApi, 'replaceAppointmentProcedures').mockResolvedValue({ appointment_id: createdAppointment.id, revision: 1, procedures: [] });
    vi.spyOn(appointmentProceduresApi, 'getAppointmentProcedures').mockResolvedValue({ appointment_id: createdAppointment.id, revision: 0, procedures: [] });
    const { renderer } = await renderNewForm();
    const active = renderer.root.findAllByType('select').find(item => item.props['aria-label'] === 'Procedură activă')!;
    await act(async () => { active.props.onChange({ target: { value: catalogProcedure.id } }); });
    await act(async () => { renderer.root.findAllByType('button').find(button => text(button) === 'Adaugă')!.props.onClick(); });
    await act(async () => { renderer.root.findByProps({ 'aria-label': 'Cantitate Consultație' }).props.onChange({ target: { value: '33' } }); });
    expect(renderer.root.findAllByType('button').some(button => text(button) === 'Create appointment' && button.props.disabled)).toBe(true);
    await act(async () => { renderer.root.findByProps({ 'aria-label': 'Cantitate Consultație' }).props.onChange({ target: { value: '2' } }); });
    const submit = () => renderer.root.findByType('form').props.onSubmit({ preventDefault() {} });
    await act(async () => { submit(); submit(); await new Promise(resolve => setTimeout(resolve, 5)); });
    expect(create).toHaveBeenCalledTimes(1); expect(put).toHaveBeenCalledTimes(1);
    renderer.unmount();
  });

  it('lets the user abandon a failed procedure save without sending another request', async () => {
    const create = vi.spyOn(client, 'createAppointment').mockResolvedValue(createdAppointment);
    const put = vi.spyOn(appointmentProceduresApi, 'replaceAppointmentProcedures').mockRejectedValue(new Error('offline'));
    vi.spyOn(appointmentProceduresApi, 'getAppointmentProcedures').mockResolvedValue({ appointment_id: createdAppointment.id, revision: 0, procedures: [] });
    const { renderer, onSaved } = await renderNewForm();
    const active = renderer.root.findAllByType('select').find(item => item.props['aria-label'] === 'Procedură activă')!;
    await act(async () => { active.props.onChange({ target: { value: catalogProcedure.id } }); });
    await act(async () => { renderer.root.findAllByType('button').find(button => text(button) === 'Adaugă')!.props.onClick(); });
    await act(async () => { renderer.root.findByType('form').props.onSubmit({ preventDefault() {} }); await new Promise(resolve => setTimeout(resolve, 5)); });
    await act(async () => { renderer.root.findAllByType('button').find(button => text(button).startsWith('Renunță'))!.props.onClick(); });
    expect(create).toHaveBeenCalledTimes(1); expect(put).toHaveBeenCalledTimes(1); expect(onSaved).toHaveBeenCalledTimes(1);
    renderer.unmount();
  });
});

function setupAvailabilityMocks(unavailability: Unavailability[] = []) {
  vi.spyOn(client, 'getClinic').mockResolvedValue({ timezone: 'Europe/Bucharest' } as never);
  vi.spyOn(client, 'listWorkingHours').mockResolvedValue([]);
  vi.spyOn(client, 'listUnavailability').mockResolvedValue(unavailability);
  vi.spyOn(client, 'listRooms').mockResolvedValue([{ id: 'room', clinic_id: 'clinic', name: 'Room', is_active: true }]);
  vi.spyOn(client, 'listStaff').mockResolvedValue([]);
}

async function renderAvailability(): Promise<TestRenderer.ReactTestRenderer> {
  let renderer!: TestRenderer.ReactTestRenderer;
  await act(async () => {
    renderer = TestRenderer.create(provider(<AvailabilitySettings />));
    await new Promise(resolve => setTimeout(resolve, 10));
  });
  return renderer;
}

function availabilityField(renderer: TestRenderer.ReactTestRenderer, label: string) {
  return renderer.root.findByProps({ 'aria-label': label });
}

async function submitAvailability(renderer: TestRenderer.ReactTestRenderer) {
  await act(async () => {
    renderer.root.findAllByType('button').find(button => text(button) === 'Block availability')!.props.onClick();
    await new Promise(resolve => setTimeout(resolve, 10));
  });
}

describe('unavailability daylight-saving choices', () => {
  it('sends separate first and second occurrence choices as UTC boundaries and resets them', async () => {
    setupAvailabilityMocks();
    const create = vi.spyOn(client, 'createUnavailability').mockResolvedValue({ affected_appointments: [], unavailability: {} } as never);
    const renderer = await renderAvailability();
    await act(async () => {
      renderer.root.findAllByType('select')[0].props.onChange({ target: { value: 'room' } });
      availabilityField(renderer, 'Unavailability start').props.onChange({ target: { value: '2027-10-31T03:30' } });
      availabilityField(renderer, 'Start DST occurrence').props.onChange({ target: { value: '1' } });
      availabilityField(renderer, 'Unavailability end').props.onChange({ target: { value: '2027-10-31T03:45' } });
      availabilityField(renderer, 'End DST occurrence').props.onChange({ target: { value: '1' } });
      await Promise.resolve();
    });
    // Changing a boundary clears only that boundary’s earlier occurrence choice.
    await act(async () => { availabilityField(renderer, 'Unavailability start').props.onChange({ target: { value: '2027-10-31T03:35' } }); });
    expect(availabilityField(renderer, 'Start DST occurrence').props.value).toBe('');
    await act(async () => { availabilityField(renderer, 'Start DST occurrence').props.onChange({ target: { value: '0' } }); });
    expect(availabilityField(renderer, 'Start DST occurrence').props.value).toBe('0');
    await submitAvailability(renderer);
    expect(create).toHaveBeenCalledWith(expect.objectContaining({ starts_at: '2027-10-31T00:35:00.000Z', ends_at: '2027-10-31T01:45:00.000Z' }));
    expect(availabilityField(renderer, 'Start DST occurrence').props.value).toBe('');
    expect(availabilityField(renderer, 'End DST occurrence').props.value).toBe('');
    renderer.unmount();
  });

  it('shows clear errors for ambiguous and nonexistent times without sending a request', async () => {
    setupAvailabilityMocks();
    const create = vi.spyOn(client, 'createUnavailability');
    const renderer = await renderAvailability();
    await act(async () => {
      renderer.root.findAllByType('select')[0].props.onChange({ target: { value: 'room' } });
      availabilityField(renderer, 'Unavailability start').props.onChange({ target: { value: '2027-10-31T03:30' } });
      availabilityField(renderer, 'Unavailability end').props.onChange({ target: { value: '2027-10-31T04:30' } });
    });
    await submitAvailability(renderer);
    expect(renderer.root.findByProps({ role: 'alert' }).children.join('')).toContain('ambiguous');
    expect(create).not.toHaveBeenCalled();

    await act(async () => { availabilityField(renderer, 'Unavailability start').props.onChange({ target: { value: '2027-03-28T03:30' } }); });
    await submitAvailability(renderer);
    expect(renderer.root.findByProps({ role: 'alert' }).children.join('')).toContain('does not exist');
    expect(create).not.toHaveBeenCalled();
    renderer.unmount();
  });

  it('sends ordinary clinic times correctly even if fold one was selected', async () => {
    setupAvailabilityMocks();
    const create = vi.spyOn(client, 'createUnavailability').mockResolvedValue({ affected_appointments: [], unavailability: {} } as never);
    const renderer = await renderAvailability();
    await act(async () => {
      renderer.root.findAllByType('select')[0].props.onChange({ target: { value: 'room' } });
      availabilityField(renderer, 'Unavailability start').props.onChange({ target: { value: '2027-02-10T10:15' } });
      availabilityField(renderer, 'Start DST occurrence').props.onChange({ target: { value: '1' } });
      availabilityField(renderer, 'Unavailability end').props.onChange({ target: { value: '2027-02-10T11:00' } });
    });
    await submitAvailability(renderer);
    expect(create).toHaveBeenCalledWith(expect.objectContaining({ starts_at: '2027-02-10T08:15:00.000Z', ends_at: '2027-02-10T09:00:00.000Z' }));
    renderer.unmount();
  });

  it('displays stored unavailability in the clinic timezone', async () => {
    setupAvailabilityMocks([{
      id: 'unavailability-1',
      resource_type: 'room',
      resource_id: 'room',
      starts_at: '2027-01-06T08:15:00Z',
      ends_at: '2027-01-06T08:45:00Z',
      reason: 'Maintenance',
      created_at: '2027-01-01T00:00:00Z',
    }]);
    const renderer = await renderAvailability();
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 50)); });
    expect(client.getClinic).toHaveBeenCalled();
    expect(client.listUnavailability).toHaveBeenCalled();
    const interval = renderer.root.findAllByType('small').find(node => text(node).includes(' – '));
    expect(interval).toBeDefined();
    expect(text(interval!)).toContain('10:15');
    expect(text(interval!)).toContain('10:45');
    expect(text(interval!)).not.toContain('08:15');
    renderer.unmount();
  });
});
