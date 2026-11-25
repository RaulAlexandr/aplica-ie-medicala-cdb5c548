import TestRenderer, { act } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as clientApi from '../api/client';
import type { User } from '../api/client';
import * as equipmentApi from './api';
import type { Equipment, EquipmentHistory } from './api';
import { EquipmentPage } from './EquipmentPage';

const equipment: Equipment = {
  id: 'equipment-1', name: 'Unit fictiv', equipment_type: 'unit dentar', serial_number: null,
  room_id: null, responsible_id: 'manager-1', state: 'available', is_active: true,
};
const affected = (match: 'sigur' | 'posibil', appointment_id: string) => ({
  match, appointment_id, patient: 'Pacient Fictiv', starts_at: '2026-11-25T09:00:00Z', ends_at: '2026-11-25T09:30:00Z',
  doctor: 'Dr. Fictiv', room: 'Cabinet fictiv', appointment_type: 'Detartraj complet', responsible: 'Manager Fictiv',
});
const historyWith = (values: Partial<EquipmentHistory> = {}): EquipmentHistory => ({
  checks: [], documents: [], dependencies: [], unavailabilities: [], ...values,
});

function text(node: TestRenderer.ReactTestInstance): string {
  return node.children.map(child => typeof child === 'string' ? child : text(child)).join('');
}
function tree(user: User, queryClient: QueryClient) {
  return <QueryClientProvider client={queryClient}><EquipmentPage user={user} /></QueryClientProvider>;
}
function newClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
}
const user = (role: string): User => ({ id: `${role}-1`, clinic_id: 'clinic-1', full_name: 'Utilizator Fictiv', email: `${role}@example.test`, role });
async function waitFor(condition: () => boolean, description: string, timeoutMs = 2_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    let ready = false;
    await act(async () => { ready = condition(); if (!ready) await new Promise(resolve => setTimeout(resolve, 5)); });
    if (ready) return;
  }
  throw new Error(`Timed out waiting for ${description}`);
}
async function render(userValue: User, history: EquipmentHistory = historyWith(), equipmentItems: Equipment[] = [equipment]) {
  vi.spyOn(clientApi, 'getClinic').mockResolvedValue({ timezone: 'Europe/Bucharest' } as never);
  vi.spyOn(clientApi, 'listRooms').mockResolvedValue([]);
  vi.spyOn(clientApi, 'listStaff').mockResolvedValue([]);
  vi.spyOn(clientApi, 'listProcedures').mockResolvedValue([]);
  vi.spyOn(equipmentApi, 'listEquipment').mockResolvedValue(equipmentItems);
  vi.spyOn(equipmentApi, 'listDueChecks').mockResolvedValue([]);
  const historyRequest = vi.spyOn(equipmentApi, 'equipmentHistory').mockResolvedValue(history);
  let renderer!: TestRenderer.ReactTestRenderer;
  await act(async () => { renderer = TestRenderer.create(tree(userValue, newClient())); });
  let row = renderer.root.findAllByType('button').find(button => text(button).includes(equipment.name));
  await waitFor(() => {
    row = renderer.root.findAllByType('button').find(button => text(button).includes(equipment.name));
    return Boolean(row);
  }, 'equipment row');
  await act(async () => { row!.props.onClick(); });
  await waitFor(() => historyRequest.mock.calls.length > 0 && !text(renderer.root).includes('Se încarcă istoricul…'), 'equipment history');
  return renderer;
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('T21 equipment screen', () => {
  it('lets reception read affected appointments only, lets doctors report, and lets managers administer', async () => {
    const affectedHistory = historyWith({ unavailabilities: [{ id: 'outage-1', equipment_id: equipment.id, reason: 'Defecțiune fictivă', starts_at: '2026-11-25T08:00:00Z', ends_at: '2026-11-25T10:00:00Z', closed_at: null, affected_appointments: [affected('sigur', 'appointment-1')] }] });
    const reception = await render(user('reception'), affectedHistory);
    expect(text(reception.root)).toContain('Potrivire sigur');
    expect(reception.root.findAllByType('form')).toHaveLength(0);
    expect(text(reception.root)).not.toContain('Înregistrează echipament');
    await act(async () => reception.unmount());

    const doctor = await render(user('doctor'));
    expect(doctor.root.findAllByType('form').some(form => text(form).includes('Raportează indisponibilitate'))).toBe(true);
    expect(text(doctor.root)).not.toContain('Înregistrează echipament');
    expect(doctor.root.findAllByType('form').some(form => text(form).includes('Service / verificare periodică'))).toBe(false);
    await act(async () => doctor.unmount());

    const manager = await render(user('clinic_manager'));
    expect(text(manager.root)).toContain('Înregistrează echipament');
    expect(text(manager.root)).toContain('Service / verificare periodică');
    await act(async () => manager.unmount());
  });

  it('shows both certain and possible affected-appointment matches', async () => {
    const history = historyWith({ unavailabilities: [{ id: 'outage-1', equipment_id: equipment.id, reason: 'Motiv fictiv', starts_at: '2026-11-25T08:00:00Z', ends_at: '2026-11-25T10:00:00Z', closed_at: null, affected_appointments: [affected('sigur', 'appointment-1'), affected('posibil', 'appointment-2')] }] });
    const renderer = await render(user('clinic_manager'), history);
    expect(text(renderer.root)).toContain('Potrivire sigur');
    expect(text(renderer.root)).toContain('Potrivire posibil');
    await act(async () => renderer.unmount());
  });

  it('uses singular wording for one affected appointment in the card and mutation notice', async () => {
    const oneOutage = { id: 'outage-1', equipment_id: equipment.id, reason: 'Defecțiune', starts_at: '2026-11-25T08:00:00Z', ends_at: '2026-11-25T10:00:00Z', closed_at: null, affected_appointments: [affected('sigur', 'appointment-1')] };
    const report = vi.spyOn(equipmentApi, 'reportOutage')
      .mockResolvedValueOnce({ affected_appointments: [affected('sigur', 'appointment-1')] } as never)
      .mockResolvedValueOnce({ affected_appointments: [affected('sigur', 'appointment-1'), affected('sigur', 'appointment-2')] } as never);
    vi.stubGlobal('FormData', class { get(name: string) { return ({ reason: 'Defecțiune', starts_at: '2026-11-25T11:00', ends_at: '2026-11-25T12:00' } as Record<string, string>)[name] ?? ''; } });
    const renderer = await render(user('clinic_manager'), historyWith({ unavailabilities: [oneOutage] }));
    const form = renderer.root.findAllByType('form').find(candidate => text(candidate).includes('Raportează indisponibilitate'))!;
    const target = { reset: vi.fn() };
    await act(async () => { form.props.onSubmit({ preventDefault() {}, currentTarget: target }); });
    await waitFor(() => text(renderer.root).includes('1 programare afectată'), 'singular mutation notice');
    expect(text(renderer.root)).toContain('1 programare afectată');
    expect(text(renderer.root)).not.toContain('1 programări afectate');
    await act(async () => { form.props.onSubmit({ preventDefault() {}, currentTarget: target }); });
    await waitFor(() => text(renderer.root).includes('2 programări afectate'), 'plural mutation notice');
    expect(text(renderer.root)).toContain('2 programări afectate');
    expect(report).toHaveBeenCalledTimes(2);
    await act(async () => renderer.unmount());
  });

  it('labels the doctor without adding a duplicate title', async () => {
    const appointment = { ...affected('sigur', 'appointment-1'), doctor: 'Dr. Alex Demo' };
    const history = historyWith({ unavailabilities: [{ id: 'outage-1', equipment_id: equipment.id, reason: 'Defecțiune', starts_at: '2026-11-25T08:00:00Z', ends_at: '2026-11-25T10:00:00Z', closed_at: null, affected_appointments: [appointment] }] });
    const renderer = await render(user('clinic_manager'), history);
    expect(text(renderer.root)).toContain('Medic: Dr. Alex Demo');
    expect(text(renderer.root)).not.toContain('Dr. Dr. Alex Demo');
    await act(async () => renderer.unmount());
  });

  it('shows Romanian equipment states in the registry', async () => {
    const equipmentItems = (['available', 'maintenance', 'broken', 'retired'] as const).map((state, index) => ({
      ...equipment, id: `equipment-${index}`, name: index === 0 ? equipment.name : `Unit ${index}`, state,
    }));
    const renderer = await render(user('clinic_manager'), historyWith(), equipmentItems);
    const registry = renderer.root.findAllByType('button').filter(button => button.props.className?.includes('equipment-row'));
    const labels = registry.map(button => text(button));
    expect(labels.join(' ')).toContain('Disponibil');
    expect(labels.join(' ')).toContain('Mentenanță');
    expect(labels.join(' ')).toContain('Defect');
    expect(labels.join(' ')).toContain('Retras');
    expect(labels.join(' ')).not.toMatch(/available|maintenance|broken|retired/);
    await act(async () => renderer.unmount());
  });

  it('submits a result to complete the existing verification', async () => {
    const history = historyWith({ checks: [{ id: 'check-1', check_type: 'inspection', due_at: '2026-11-24T08:00:00Z', completed_at: null, completed_by_id: null, author_id: 'manager-1', result: null }] });
    const complete = vi.spyOn(equipmentApi, 'completeCheck').mockResolvedValue({} as never);
    const renderer = await render(user('clinic_manager'), history);
    const form = renderer.root.findByProps({ 'aria-label': 'Completează verificarea check-1' });
    const input = form.findByType('input');
    act(() => input.props.onChange({ target: { value: 'Conform fictiv' } }));
    await act(async () => { form.props.onSubmit({ preventDefault() {} }); });
    expect(complete).toHaveBeenCalledWith(equipment.id, 'check-1', 'Conform fictiv');
    await act(async () => renderer.unmount());
  });

  it('warns and scrolls to a prefilled outage report when equipment becomes broken', async () => {
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { callback(0); return 1; });
    const update = vi.spyOn(equipmentApi, 'updateEquipment').mockResolvedValue({ ...equipment, state: 'broken', unavailability_required: true, open_unavailability_id: null });
    const renderer = await render(user('clinic_manager'));
    const state = renderer.root.findAllByType('select').find(select => select.props.value === 'available');
    if (!state) throw new Error('Equipment state selector was not rendered');
    await act(async () => { state.props.onChange({ target: { value: 'broken' } }); });
    await waitFor(() => text(renderer.root).includes('nu are o indisponibilitate deschisă'), 'broken equipment warning');
    expect(update.mock.calls[0]?.[0]).toEqual({ ...equipment, state: 'broken' });
    expect(text(renderer.root)).toContain('nu are o indisponibilitate deschisă');
    const reason = renderer.root.findAllByType('input').find(input => input.props.name === 'reason');
    expect(reason?.props.value).toBe('Defecțiune raportată pentru Unit fictiv');
    await act(async () => renderer.unmount());
  });

  it('offers exactly one dependency target and explains that cabinet and service are separate entries', async () => {
    const renderer = await render(user('clinic_manager'));
    expect(text(renderer.root)).toContain('Alege un singur tip: cabinet sau serviciu. Pentru ambele, adaugă înregistrări separate.');
    const target = renderer.root.findByProps({ 'aria-label': 'Tipul dependenței' });
    act(() => target.props.onChange({ target: { value: 'procedure' } }));
    const dependencyForm = renderer.root.findAllByType('form').find(form => text(form).includes('Dependență'))!;
    expect(dependencyForm.findAllByProps({ name: 'procedure_id' })).toHaveLength(1);
    expect(dependencyForm.findAllByProps({ name: 'room_id' })).toHaveLength(0);
    await act(async () => renderer.unmount());
  });

  it('keeps all five submission forms intact and shows the server error after a rejected mutation', async () => {
    const rejected = () => Promise.reject(new Error('Rezultatul este obligatoriu pentru o verificare efectuată'));
    const create = vi.spyOn(equipmentApi, 'createEquipment').mockImplementation(rejected);
    const report = vi.spyOn(equipmentApi, 'reportOutage').mockImplementation(rejected);
    const addCheck = vi.spyOn(equipmentApi, 'addCheck').mockImplementation(rejected);
    const addDocument = vi.spyOn(equipmentApi, 'addDocument').mockImplementation(rejected);
    const addDependency = vi.spyOn(equipmentApi, 'addDependency').mockImplementation(rejected);
    vi.stubGlobal('FormData', class { constructor(private form: { kind: string }) {} get(name: string) {
      const values: Record<string, Record<string, string>> = {
        equipment: { name: 'Unit păstrat', equipment_type: 'unit', responsible_id: 'manager-1' },
        outage: { reason: 'Defecțiune păstrată', starts_at: '2026-11-25T11:00', ends_at: '2026-11-25T12:00' },
        check: { check_type: 'inspection', due_at: '2026-11-25T11:00', completed_at: '', result: '' },
        document: { title: 'Fișă păstrată', document_type: 'manual', reference: 'doc://fictiv' },
        dependency: { room_id: 'room-1', procedure_id: '' },
      };
      return values[this.form.kind][name] ?? '';
    } });
    const renderer = await render(user('clinic_manager'));
    act(() => renderer.root.findByProps({ 'aria-label': 'Tipul dependenței' }).props.onChange({ target: { value: 'room' } }));
    const cases = [
      { heading: 'Nume', kind: 'equipment' },
      { heading: 'Raportează indisponibilitate', kind: 'outage' },
      { heading: 'Service / verificare periodică', kind: 'check' },
      { heading: 'Document (referință)', kind: 'document' },
      { heading: 'Dependență', kind: 'dependency' },
    ];
    for (const item of cases) {
      const form = renderer.root.findAllByType('form').find(candidate => text(candidate).includes(item.heading))!;
      const target = { kind: item.kind, reset: vi.fn() };
      await act(async () => { form.props.onSubmit({ preventDefault() {}, currentTarget: target }); });
      await waitFor(() => text(renderer.root).includes('Rezultatul este obligatoriu pentru o verificare efectuată'), `rejected ${item.heading}`);
      expect(target.reset, item.heading).not.toHaveBeenCalled();
      expect(text(renderer.root)).toContain('Rezultatul este obligatoriu pentru o verificare efectuată');
    }
    create.mockResolvedValue(equipment);
    report.mockResolvedValue({ affected_appointments: [] } as never);
    addCheck.mockResolvedValue({} as never);
    addDocument.mockResolvedValue({} as never);
    addDependency.mockResolvedValue({} as never);
    for (const item of cases) {
      const form = renderer.root.findAllByType('form').find(candidate => text(candidate).includes(item.heading))!;
      const target = { kind: item.kind, reset: vi.fn() };
      await act(async () => { form.props.onSubmit({ preventDefault() {}, currentTarget: target }); });
      await waitFor(() => target.reset.mock.calls.length === 1, `successful ${item.heading}`);
      expect(target.reset, item.heading).toHaveBeenCalledTimes(1);
    }
    expect(text(renderer.root)).not.toContain('Rezultatul este obligatoriu pentru o verificare efectuată');
    await act(async () => renderer.unmount());
  });

  it('sends only one request when each of the five forms is submitted twice synchronously', async () => {
    const wait = () => new Promise<never>(() => {});
    const create = vi.spyOn(equipmentApi, 'createEquipment').mockImplementation(wait);
    const report = vi.spyOn(equipmentApi, 'reportOutage').mockImplementation(wait);
    const addCheck = vi.spyOn(equipmentApi, 'addCheck').mockImplementation(wait);
    const addDocument = vi.spyOn(equipmentApi, 'addDocument').mockImplementation(wait);
    const addDependency = vi.spyOn(equipmentApi, 'addDependency').mockImplementation(wait);
    vi.stubGlobal('FormData', class { constructor(private form: { kind: string }) {} get(name: string) {
      const values: Record<string, Record<string, string>> = {
        equipment: { name: 'Unit dublu', equipment_type: 'unit', responsible_id: 'manager-1' },
        outage: { reason: 'Defecțiune', starts_at: '2026-11-25T11:00', ends_at: '2026-11-25T12:00' },
        check: { check_type: 'inspection', due_at: '2026-11-25T11:00', completed_at: '', result: '' },
        document: { title: 'Fișă', document_type: 'manual', reference: 'doc://fictiv' },
        dependency: { room_id: 'room-1', procedure_id: '' },
      };
      return values[this.form.kind][name] ?? '';
    } });
    const renderer = await render(user('clinic_manager'));
    const dependencyForm = renderer.root.findAllByType('form').find(form => text(form).includes('Dependență'))!;
    act(() => dependencyForm.findByProps({ 'aria-label': 'Tipul dependenței' }).props.onChange({ target: { value: 'room' } }));
    const cases = [
      { heading: 'Nume', kind: 'equipment' },
      { heading: 'Raportează indisponibilitate', kind: 'outage' },
      { heading: 'Service / verificare periodică', kind: 'check' },
      { heading: 'Document (referință)', kind: 'document' },
      { heading: 'Dependență', kind: 'dependency' },
    ];
    for (const item of cases) {
      const form = renderer.root.findAllByType('form').find(candidate => text(candidate).includes(item.heading))!;
      const target = { kind: item.kind, reset: vi.fn() };
      await act(async () => {
        form.props.onSubmit({ preventDefault() {}, currentTarget: target });
        form.props.onSubmit({ preventDefault() {}, currentTarget: target });
      });
    }
    expect(create).toHaveBeenCalledTimes(1);
    expect(report).toHaveBeenCalledTimes(1);
    expect(addCheck).toHaveBeenCalledTimes(1);
    expect(addDocument).toHaveBeenCalledTimes(1);
    expect(addDependency).toHaveBeenCalledTimes(1);
    await act(async () => renderer.unmount());
  });
});
