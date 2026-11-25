import TestRenderer, { act } from 'react-test-renderer';
import { useState, type ReactNode } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AxiosError } from 'axios';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as client from './api/client';
import { PatientForm, Rooms, StaffOnboarding } from './App';
import { InventoryPage } from './inventory/InventoryPage';
import * as inventoryApi from './inventory/api';

const patient: client.Patient = {
  id: 'patient-1', clinic_id: 'clinic-1', first_name: 'Ana', last_name: 'Test', date_of_birth: null, sex: null,
  phone: null, email: null, address: null, emergency_contact: null, occupation: null, allergies: null,
  medications: null, chronic_diseases: null, pregnancy_status: null, smoking_status: null,
  previous_surgeries: null, relevant_medical_conditions: null, medical_alerts: null, notes: null,
  created_at: '', updated_at: '',
};
const invitation: client.Invitation = {
  id: 'invitation-1', email: 'doctor@example.test', full_name: 'Doctor Fictiv', role: 'doctor',
  expires_at: '2030-01-01T00:00:00Z', revoked_at: null, accepted_at: null, created_at: '', invitation_url: null,
};
const clinicRoom: client.Room = { id: 'room-1', clinic_id: 'clinic-1', name: 'Cabinet Fictiv', is_active: true };
const queryClient = () => new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
const provider = (node: ReactNode, clientInstance = queryClient()) =>
  <QueryClientProvider client={clientInstance}><MemoryRouter>{node}</MemoryRouter></QueryClientProvider>;
const text = (node: TestRenderer.ReactTestInstance): string => node.children.map(child => typeof child === 'string' ? child : text(child)).join('');
const form = (tree: TestRenderer.ReactTestRenderer, heading: string) => tree.root.findAllByType('form').find(row => text(row).includes(heading))!;
const setInput = async (tree: TestRenderer.ReactTestRenderer, index: number, value: string) => {
  await act(async () => { tree.root.findAllByType('input')[index].props.onChange({ target: { value } }); });
};
const submit = (row: TestRenderer.ReactTestInstance) => row.props.onSubmit({ preventDefault() {} });
const deferred = <T,>() => {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
};
const conflict = (message: string) => {
  const error = new AxiosError('Request failed with status code 409');
  error.response = { status: 409, statusText: 'Conflict', headers: {}, config: {} as never, data: { detail: message } };
  return error;
};
async function waitFor(predicate: () => boolean, message: string) {
  const deadline = Date.now() + 1000;
  while (!predicate()) {
    if (Date.now() >= deadline) throw new Error(message);
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
  }
}

function PatientFlow({ onSaved }: { onSaved: (saved: client.Patient) => void }) {
  const [show, setShow] = useState(true);
  return show ? <PatientForm onSaved={saved => { onSaved(saved); setShow(false); }} /> : <button onClick={() => setShow(true)}>New patient</button>;
}

afterEach(() => vi.restoreAllMocks());

describe('form submit guards', () => {
  it('creates a patient once while pending, closes on success, and allows a new explicit patient action', async () => {
    const pending = deferred<client.Patient>();
    const create = vi.spyOn(client, 'createPatient').mockImplementationOnce(() => pending.promise).mockResolvedValue(patient);
    const onSaved = vi.fn();
    let tree!: TestRenderer.ReactTestRenderer;
    await act(async () => { tree = TestRenderer.create(provider(<PatientFlow onSaved={onSaved} />)); });
    await setInput(tree, 0, 'Ana'); await setInput(tree, 1, 'Test');
    const patientForm = form(tree, 'New patient');
    await act(async () => { submit(patientForm); submit(patientForm); });
    expect(create).toHaveBeenCalledTimes(1);
    await waitFor(() => text(form(tree, 'New patient')).includes('Saving…'), 'Patient form did not enter busy state.');
    expect(text(form(tree, 'New patient'))).toContain('Saving…');
    expect(form(tree, 'New patient').findByType('button').props.disabled).toBe(true);
    await act(async () => { pending.resolve(patient); });
    await waitFor(() => onSaved.mock.calls.length === 1, 'Patient success did not close the form.');
    expect(onSaved).toHaveBeenCalledTimes(1);
    await act(async () => { tree.root.findByType('button').props.onClick(); });
    await setInput(tree, 0, 'Mara'); await setInput(tree, 1, 'Noua');
    await act(async () => { submit(form(tree, 'New patient')); });
    await waitFor(() => onSaved.mock.calls.length === 2, 'Second explicit patient creation did not complete.');
    expect(create).toHaveBeenCalledTimes(2);
    expect(onSaved).toHaveBeenCalledTimes(2);
    tree.unmount();
  });

  it('shows a real patient 409, preserves edit values, and releases the guard for retry', async () => {
    const update = vi.spyOn(client, 'updatePatient').mockRejectedValueOnce(conflict('E-mailul există deja.')).mockResolvedValue(patient);
    const onSaved = vi.fn();
    let tree!: TestRenderer.ReactTestRenderer;
    await act(async () => { tree = TestRenderer.create(provider(<PatientForm initial={patient} onSaved={onSaved} />)); });
    const edit = form(tree, 'Edit patient');
    await act(async () => { submit(edit); submit(edit); });
    expect(update).toHaveBeenCalledTimes(1);
    await waitFor(() => tree.root.findAllByProps({ role: 'alert' }).length === 1, 'Patient 409 was not shown.');
    expect(text(tree.root.findByProps({ role: 'alert' }))).toContain('E-mailul există deja.');
    expect(tree.root.findAllByType('input')[0].props.value).toBe('Ana');
    await act(async () => { submit(form(tree, 'Edit patient')); });
    await waitFor(() => onSaved.mock.calls.length === 1, 'Patient retry did not succeed.');
    expect(update).toHaveBeenCalledTimes(2);
    expect(onSaved).toHaveBeenCalledTimes(1);
    tree.unmount();
  });

  it('creates one invitation while pending, preserves a 409 for retry, resets on success, and permits another invitation', async () => {
    vi.spyOn(client, 'listStaff').mockResolvedValue([]);
    vi.spyOn(client, 'listInvitations').mockResolvedValue([]);
    const pending = deferred<client.Invitation>();
    const create = vi.spyOn(client, 'createInvitation').mockImplementationOnce(() => pending.promise)
      .mockResolvedValue(invitation);
    let tree!: TestRenderer.ReactTestRenderer;
    await act(async () => { tree = TestRenderer.create(provider(<StaffOnboarding />)); });
    await setInput(tree, 0, 'Doctor Fictiv'); await setInput(tree, 1, 'doctor@example.test');
    const inviteForm = form(tree, 'Full name');
    await act(async () => { submit(inviteForm); submit(inviteForm); });
    expect(create).toHaveBeenCalledTimes(1);
    await waitFor(() => inviteForm.findByType('button').props.disabled === true, 'Invitation form did not enter busy state.');
    expect(inviteForm.findByType('button').props.disabled).toBe(true);
    await act(async () => { pending.reject(conflict('Invitația există deja.')); });
    await waitFor(() => tree.root.findAllByProps({ role: 'alert' }).length === 1, 'Invitation 409 was not shown.');
    expect(text(tree.root.findByProps({ role: 'alert' }))).toContain('Invitația există deja.');
    expect(tree.root.findAllByType('input').some(input => input.props.value === 'Doctor Fictiv')).toBe(true);
    await act(async () => { submit(form(tree, 'Full name')); });
    await waitFor(() => create.mock.calls.length === 2 && tree.root.findAllByType('input').slice(0, 2).every(input => input.props.value === ''), 'Invitation retry did not reset the form.');
    expect(create).toHaveBeenCalledTimes(2);
    expect(tree.root.findAllByType('input').slice(0, 2).map(input => input.props.value)).toEqual(['', '']);
    await setInput(tree, 0, 'Al doilea Doctor'); await setInput(tree, 1, 'second@example.test');
    await act(async () => { submit(form(tree, 'Full name')); });
    await waitFor(() => create.mock.calls.length === 3, 'Second explicit invitation was not sent.');
    expect(create).toHaveBeenCalledTimes(3);
    tree.unmount();
  });

  it('creates a room once for a slow double submit and allows a later explicit room creation', async () => {
    vi.spyOn(client, 'listRooms').mockResolvedValue([]);
    const pending = deferred<client.Room>();
    const create = vi.spyOn(client, 'createRoom').mockImplementationOnce(() => pending.promise)
      .mockResolvedValueOnce({ ...clinicRoom, name: 'Cabinet Nou' }).mockResolvedValue({ ...clinicRoom, name: 'Cabinet Nou 2' });
    let tree!: TestRenderer.ReactTestRenderer;
    await act(async () => { tree = TestRenderer.create(provider(<Rooms />)); });
    await setInput(tree, 0, 'Cabinet Nou');
    const roomForm = form(tree, 'Create room');
    await act(async () => { submit(roomForm); submit(roomForm); });
    expect(create).toHaveBeenCalledTimes(1);
    await waitFor(() => roomForm.findByType('button').props.disabled === true, 'Room form did not enter busy state.');
    expect(roomForm.findByType('button').props.disabled).toBe(true);
    await act(async () => { pending.reject(conflict('Cabinetul există deja.')); });
    await waitFor(() => text(tree.root).includes('Cabinetul există deja.'), 'Room 409 was not shown.');
    expect(tree.root.findAllByType('input')[0].props.value).toBe('Cabinet Nou');
    await act(async () => { submit(form(tree, 'Create room')); });
    await waitFor(() => text(tree.root).includes('Room created.'), 'Room creation did not report success.');
    expect(text(tree.root)).toContain('Room created.');
    expect(tree.root.findAllByType('input')[0].props.value).toBe('');
    await setInput(tree, 0, 'Cabinet Nou 2');
    await act(async () => { submit(form(tree, 'Create room')); });
    await waitFor(() => create.mock.calls.length === 3, 'Second explicit room creation was not sent.');
    expect(create).toHaveBeenCalledTimes(3);
    tree.unmount();
  });

  it('shows inventory 409, preserves the article, and permits retry and a later article', async () => {
    vi.spyOn(client, 'getClinic').mockResolvedValue({ timezone: 'Europe/Bucharest' } as never);
    vi.spyOn(inventoryApi, 'inventoryOverview').mockResolvedValue({ items: [], locations: [], lots: [], balances: [] } as never);
    vi.spyOn(inventoryApi, 'inventoryHistory').mockResolvedValue([]);
    const pending = deferred<Awaited<ReturnType<typeof inventoryApi.addInventoryItem>>>();
    const add = vi.spyOn(inventoryApi, 'addInventoryItem').mockImplementationOnce(() => pending.promise)
      .mockResolvedValueOnce({} as never).mockResolvedValue({} as never);
    let tree!: TestRenderer.ReactTestRenderer;
    await act(async () => { tree = TestRenderer.create(provider(<InventoryPage role="clinic_manager" />)); });
    const itemForm = () => form(tree, 'Articol nou');
    await setInput(tree, 0, 'Material fictiv'); await setInput(tree, 1, 'buc');
    const firstForm = itemForm();
    await act(async () => { submit(firstForm); submit(firstForm); });
    expect(add).toHaveBeenCalledTimes(1);
    await waitFor(() => firstForm.findByType('button').props.disabled === true, 'Inventory form did not enter busy state.');
    expect(firstForm.findByType('button').props.disabled).toBe(true);
    await act(async () => { pending.reject(conflict('Articolul există deja.')); });
    await waitFor(() => tree.root.findAllByProps({ role: 'alert' }).length === 1, 'Inventory 409 was not shown.');
    expect(text(tree.root.findByProps({ role: 'alert' }))).toContain('Articolul există deja.');
    expect(tree.root.findAllByType('input').some(input => input.props.value === 'Material fictiv')).toBe(true);
    await act(async () => { submit(itemForm()); });
    await waitFor(() => add.mock.calls.length === 2 && tree.root.findAllByType('input').slice(0, 2).every(input => input.props.value === ''), 'Inventory retry did not reset the form.');
    expect(add).toHaveBeenCalledTimes(2);
    expect(tree.root.findAllByType('input').slice(0, 2).map(input => input.props.value)).toEqual(['', '']);
    await setInput(tree, 0, 'Material nou'); await setInput(tree, 1, 'set');
    await act(async () => { submit(itemForm()); });
    await waitFor(() => add.mock.calls.length === 3, 'Second explicit article creation was not sent.');
    expect(add).toHaveBeenCalledTimes(3);
    tree.unmount();
  });
});
