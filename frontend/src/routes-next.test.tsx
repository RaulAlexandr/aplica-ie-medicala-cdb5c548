import TestRenderer, { act } from 'react-test-renderer';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as client from './api/client';
import App, { PatientDetail } from './App';

// Page components have their own tests; here only the mounting and the role gates of App.tsx are checked.
vi.mock('./waitlist/Waitlist', () => ({ WaitlistPanel: () => <section data-page="waitlist">Pagina listei de așteptare</section> }));
vi.mock('./reports/ReportsPage', () => ({ ReportsPage: () => <section data-page="reports">Pagina rapoartelor</section> }));
vi.mock('./forms/Forms', () => ({
  FormTemplateEditor: () => <section data-page="template-editor">Editor de șabloane</section>,
  PatientFormsPanel: ({ canReview }: { patientId: string; canReview?: boolean }) => <section data-page="patient-forms" data-can-review={String(canReview)}>Formulare pacient</section>,
}));
vi.mock('./AvailabilitySettings', () => ({ AvailabilitySettings: () => <section>Disponibilitate</section> }));
vi.mock('./clinical/ClinicalPatientRecord', () => ({ ClinicalPatientRecord: () => <section>Dosar clinic</section> }));
vi.mock('./finance/PatientAccountPanel', () => ({ PatientAccountPanel: () => <section>Cont pacient</section> }));

const clinicId = '4b8238d5-d1f4-430b-89b6-14fc7b50e8c2';
const userWithRole = (role: string): client.User => ({ id: `u-${role}`, clinic_id: clinicId, full_name: 'Persoană Demo', email: 'demo@example.com', role } as client.User);
const clinic = { id: clinicId, name: 'Clinica Demo Fictivă', timezone: 'Europe/Bucharest', created_at: '', updated_at: '' } as client.Clinic;
const patient = { id: 'p1', first_name: 'Ana', last_name: 'Test' } as client.Patient;

const textOf = (node: TestRenderer.ReactTestInstance): string => node.children.map(child => (typeof child === 'string' ? child : textOf(child))).join('');
const flush = async () => { for (let i = 0; i < 6; i += 1) await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); }); };
const newClient = () => new QueryClient({ defaultOptions: { queries: { retry: false } } });

async function renderApp(role: string, path: string) {
  vi.spyOn(client, 'me').mockResolvedValue(userWithRole(role));
  let renderer!: TestRenderer.ReactTestRenderer;
  await act(async () => { renderer = TestRenderer.create(<MemoryRouter initialEntries={[path]}><QueryClientProvider client={newClient()}><App /></QueryClientProvider></MemoryRouter>); });
  await flush();
  return renderer;
}
const pages = (r: TestRenderer.ReactTestRenderer) => r.root.findAll(n => typeof n.type === 'string' && n.props['data-page'] !== undefined).map(n => n.props['data-page'] as string);
const navLinks = (r: TestRenderer.ReactTestRenderer) => r.root.findAll(n => n.type === 'a' && typeof n.props.href === 'string').map(n => n.props.href as string);

beforeEach(() => {
  const store: Record<string, string> = { access_token: 'fictive-token' };
  vi.stubGlobal('window', new EventTarget());
  vi.stubGlobal('localStorage', { getItem: (k: string) => store[k] ?? null, setItem: (k: string, v: string) => { store[k] = v; }, removeItem: (k: string) => { delete store[k]; } });
  vi.spyOn(client, 'countPatients').mockResolvedValue(0);
  vi.spyOn(client, 'listAppointments').mockResolvedValue([]);
  vi.spyOn(client, 'getClinic').mockResolvedValue(clinic);
  vi.spyOn(client, 'listRooms').mockResolvedValue([]);
  vi.spyOn(client, 'getPatient').mockResolvedValue(patient);
});
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('T16 waitlist route', () => {
  for (const role of ['clinic_manager', 'administrator', 'doctor', 'assistant', 'reception']) {
    it(`${role} sees /waitlist and the navigation link`, async () => {
      const r = await renderApp(role, '/waitlist');
      expect(pages(r)).toEqual(['waitlist']);
      expect(navLinks(r)).toContain('/waitlist');
      await act(async () => r.unmount());
    });
  }
});

describe('T24 reports route', () => {
  for (const role of ['clinic_manager', 'administrator']) {
    it(`${role} sees /reports and the navigation link`, async () => {
      const r = await renderApp(role, '/reports');
      expect(pages(r)).toEqual(['reports']);
      expect(navLinks(r)).toContain('/reports');
      await act(async () => r.unmount());
    });
  }
  for (const role of ['doctor', 'assistant', 'reception']) {
    it(`${role} is redirected from /reports and has no link`, async () => {
      const r = await renderApp(role, '/reports');
      expect(pages(r)).toEqual([]);
      expect(navLinks(r)).not.toContain('/reports');
      expect(textOf(r.root)).toContain('Overview');
      await act(async () => r.unmount());
    });
  }
});

describe('T12 forms mounting', () => {
  for (const [role, review] of [['reception', 'false'], ['assistant', 'false'], ['doctor', 'true'], ['clinic_manager', 'true']] as const) {
    it(`${role} gets the patient forms panel with canReview=${review}`, async () => {
      let renderer!: TestRenderer.ReactTestRenderer;
      await act(async () => { renderer = TestRenderer.create(<MemoryRouter><QueryClientProvider client={newClient()}><PatientDetail user={userWithRole(role)} id="p1" /></QueryClientProvider></MemoryRouter>); });
      await flush();
      const panel = renderer.root.findAll(n => n.props['data-page'] === 'patient-forms');
      expect(panel).toHaveLength(1);
      expect(panel[0].props['data-can-review']).toBe(review);
      await act(async () => renderer.unmount());
    });
  }

  it('the manager finds the template editor in clinic setup', async () => {
    const r = await renderApp('clinic_manager', '/setup');
    expect(pages(r)).toEqual(['template-editor']);
    await act(async () => r.unmount());
  });
  for (const role of ['doctor', 'reception']) {
    it(`${role} is redirected from /setup and never sees the template editor`, async () => {
      const r = await renderApp(role, '/setup');
      expect(pages(r)).toEqual([]);
      await act(async () => r.unmount());
    });
  }
});
