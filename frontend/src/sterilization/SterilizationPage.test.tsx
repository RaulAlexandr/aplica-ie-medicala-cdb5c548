import TestRenderer, { act } from 'react-test-renderer';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { User } from '../api/client';
import * as equipmentApi from '../equipment/api';
import * as api from './api';
import type { Cycle, Pack } from './api';
import { SterilizationPage } from './SterilizationPage';
import { SterilizationPatientTrace, SterilizationVisitPanel } from './TracePanels';

const user = (role: string, id = `${role}-1`): User => ({ id, clinic_id: 'c1', full_name: 'Utilizator Fictiv', email: `${role}@example.test`, role });
const cycle = (over: Partial<Cycle> = {}): Cycle => ({
  id: 'cycle-1', equipment_id: 'eq-1', equipment_name: 'Autoclav fictiv', cycle_number: 4, program: '134°C / 18 min', started_at: '2026-11-25T08:00:00Z', ended_at: '2026-11-25T08:40:00Z',
  operator_id: 'assistant-1', operator_name: 'Asistent Fictiv', temperature_c: '134.0', pressure_kpa: '210.0', hold_minutes: 18, status: 'finished', decided_by_id: null, decided_by_name: null,
  decided_at: null, decision_note: null, chemical_result: 'pass', biological_result: 'pending', compromised: false, recall_reason: null, pack_codes: ['P-1'], pack_count: 1, indicators: [], ...over,
});
const pack = (over: Partial<Pack> = {}): Pack => ({ id: 'pack-1', code: 'P-1', set_type_id: 't1', set_type_name: 'Set consultație', state: 'sterile', expires_at: '2026-12-25T08:00:00Z', cycle_id: 'cycle-1', cycle_number: 4, equipment_name: 'Autoclav fictiv', ...over });

function text(node: TestRenderer.ReactTestInstance): string { return node.children.map(child => typeof child === 'string' ? child : text(child)).join(''); }
const newClient = () => new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
async function render(element: JSX.Element) {
  let renderer!: TestRenderer.ReactTestRenderer;
  await act(async () => { renderer = TestRenderer.create(<QueryClientProvider client={newClient()}><MemoryRouter>{element}</MemoryRouter></QueryClientProvider>); await new Promise(resolve => setTimeout(resolve, 20)); });
  return renderer;
}
const buttons = (renderer: TestRenderer.ReactTestRenderer, label: string) => renderer.root.findAll(node => node.type === 'button' && text(node).includes(label));

function mockBase(overrides: { cycles?: Cycle[]; packs?: Pack[] } = {}) {
  vi.spyOn(api, 'listCycles').mockResolvedValue(overrides.cycles ?? [cycle()]);
  vi.spyOn(api, 'listPacks').mockResolvedValue(overrides.packs ?? [pack({ state: 'unsterile' })]);
  vi.spyOn(api, 'listSetTypes').mockResolvedValue([]);
  vi.spyOn(api, 'getCycle').mockImplementation(async () => (overrides.cycles ?? [cycle()])[0]);
  vi.spyOn(equipmentApi, 'listEquipment').mockResolvedValue([{ id: 'eq-1', name: 'Autoclav fictiv', equipment_type: 'sterilizator', serial_number: null, room_id: null, responsible_id: 'm', state: 'available', is_active: true }]);
}
afterEach(() => vi.restoreAllMocks());

describe('SterilizationPage', () => {
  it('states that traceability comes from approved cycles, not checklist ticks', async () => {
    mockBase();
    const renderer = await render(<SterilizationPage user={user('assistant')} />);
    expect(text(renderer.root)).toContain('nu din bife de checklist');
    expect(text(renderer.root)).toContain('Cicluri');
    expect(text(renderer.root)).toContain('Rechemări');
  });

  it('hides the approval buttons from the cycle operator and explains why', async () => {
    mockBase();
    const renderer = await render(<SterilizationPage user={user('assistant', 'assistant-1')} />);
    await act(async () => { buttons(renderer, 'Ciclul 4')[0].props.onClick(); await new Promise(resolve => setTimeout(resolve, 20)); });
    expect(text(renderer.root)).toContain('Operatorul ciclului nu îl poate aproba');
    expect(buttons(renderer, 'Aprobă ciclul')).toHaveLength(0);
  });

  it('lets another assistant approve and sends the decision', async () => {
    mockBase();
    const decide = vi.spyOn(api, 'decideCycle').mockResolvedValue(cycle({ status: 'approved' }));
    const renderer = await render(<SterilizationPage user={user('assistant', 'assistant-2')} />);
    await act(async () => { buttons(renderer, 'Ciclul 4')[0].props.onClick(); await new Promise(resolve => setTimeout(resolve, 20)); });
    await act(async () => { buttons(renderer, 'Aprobă ciclul')[0].props.onClick(); await new Promise(resolve => setTimeout(resolve, 20)); });
    expect(decide).toHaveBeenCalledWith('cycle-1', 'approved', '');
  });

  it('doctor reads cycles without any write form', async () => {
    mockBase();
    const renderer = await render(<SterilizationPage user={user('doctor')} />);
    expect(renderer.root.findAll(node => node.type === 'form')).toHaveLength(0);
    await act(async () => { buttons(renderer, 'Ciclul 4')[0].props.onClick(); await new Promise(resolve => setTimeout(resolve, 20)); });
    expect(buttons(renderer, 'Aprobă ciclul')).toHaveLength(0);
  });

  it('shows the recall list with patients and the CSV export', async () => {
    const bad = cycle({ status: 'approved', compromised: true, recall_reason: 'Spori viabili' });
    mockBase({ cycles: [bad] });
    vi.spyOn(api, 'getRecall').mockResolvedValue({ cycle: bad, compromised: true, reason: 'Spori viabili', recorded_at: null, unused_packs: ['P-2'], items: [{ pack_id: 'pack-1', pack_code: 'P-1', set_type: 'Set consultație', used_at: '2026-11-26T08:00:00Z', appointment_id: 'a1', appointment_starts_at: '2026-11-26T08:00:00Z', patient_id: 'pat-1', patient_name: 'Ana Fictivă', doctor_name: 'Dr. Fictiv', reason: 'compromis' }] });
    const renderer = await render(<SterilizationPage user={user('clinic_manager')} />);
    await act(async () => { buttons(renderer, 'Rechemări')[0].props.onClick(); await new Promise(resolve => setTimeout(resolve, 20)); });
    await act(async () => { buttons(renderer, 'Ciclul 4')[0].props.onClick(); await new Promise(resolve => setTimeout(resolve, 20)); });
    const out = text(renderer.root);
    expect(out).toContain('Ana Fictivă');
    expect(out).toContain('Spori viabili');
    expect(out).toContain('P-2');
    expect(buttons(renderer, 'Exportă CSV')).toHaveLength(1);
  });
});

describe('Sterilization traceability panels', () => {
  const row = { event_id: 'e1', pack_code: 'P-1', set_type: 'Set consultație', used_at: '2026-11-26T08:00:00Z', appointment_id: 'a1', appointment_starts_at: '2026-11-26T08:00:00Z', patient_id: 'pat-1', procedure_name: 'Detartraj', cycle_id: 'cycle-1', cycle_number: 4, cycle_status: 'approved' as const, equipment_name: 'Autoclav fictiv', approved_at: '2026-11-25T09:00:00Z', compromised: false, recall_reason: null };

  it('shows pack, cycle and device on the patient record, and nothing for reception', async () => {
    const trace = vi.spyOn(api, 'tracePatient').mockResolvedValue([row]);
    const doctor = await render(<SterilizationPatientTrace patientId="pat-1" role="doctor" />);
    expect(text(doctor.root)).toContain('Pachet P-1');
    expect(text(doctor.root)).toContain('Ciclul 4');
    expect(text(doctor.root)).toContain('Autoclav fictiv');
    trace.mockClear();
    const reception = await render(<SterilizationPatientTrace patientId="pat-1" role="reception" />);
    expect(text(reception.root)).toBe('');
    expect(trace).not.toHaveBeenCalled();
  });

  it('links a sterile pack to the visit once, with the chosen procedure', async () => {
    vi.spyOn(api, 'traceAppointment').mockResolvedValue([]);
    vi.spyOn(api, 'listPacks').mockResolvedValue([pack(), pack({ id: 'pack-2', code: 'P-2', state: 'used' })]);
    const use = vi.spyOn(api, 'usePack').mockResolvedValue({});
    const renderer = await render(<SterilizationVisitPanel appointmentId="a1" role="doctor" closed={false} procedures={[{ id: 'pp1', procedure_name: 'Detartraj' }]} />);
    const selects = renderer.root.findAll(node => node.type === 'select');
    expect(text(selects[0])).toContain('P-1');
    expect(text(selects[0])).not.toContain('P-2');
    await act(async () => { selects[0].props.onChange({ target: { value: 'pack-1' } }); selects[1].props.onChange({ target: { value: 'pp1' } }); });
    const form = renderer.root.findByProps({ 'aria-label': 'Leagă un pachet steril' });
    await act(async () => { form.props.onSubmit({ preventDefault() {} }); await new Promise(resolve => setTimeout(resolve, 20)); });
    expect(use).toHaveBeenCalledTimes(1);
    expect(use).toHaveBeenCalledWith('pack-1', { appointment_id: 'a1', performed_procedure_id: 'pp1' });
  });
});
