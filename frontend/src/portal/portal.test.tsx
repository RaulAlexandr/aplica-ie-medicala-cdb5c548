import TestRenderer, { act } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { AxiosError, AxiosHeaders, InternalAxiosRequestConfig } from 'axios';
import { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as portal from './api';
import * as staff from './staffApi';
import * as documentsApi from '../documents/api';
import * as formsApi from '../forms/api';
import * as plansApi from '../treatment-plans/api';
import { PortalApp } from './PortalApp';
import { PatientPortalSection } from './PatientPortalSection';
import { RescheduleRequestsPage } from './RescheduleRequestsPage';
import type { Patient } from '../api/client';

let store: Record<string, string>;
beforeEach(() => {
  store = {};
  vi.stubGlobal('localStorage', { getItem: (k: string) => store[k] ?? null, setItem: (k: string, v: string) => { store[k] = v; }, removeItem: (k: string) => { delete store[k]; } });
  vi.stubGlobal('window', { addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn(), location: { origin: 'https://clinica.test' } });
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

function wrap(node: ReactNode, path = '/portal') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}>{node}</MemoryRouter></QueryClientProvider>;
}
const flush = () => new Promise(resolve => setTimeout(resolve, 0));
type Node = ReturnType<TestRenderer.ReactTestRenderer['toJSON']>;
const strings = (node: Node | string): string => (node === null ? '' : typeof node === 'string' ? node : Array.isArray(node) ? node.map(strings).join(' ') : (node.children ?? []).map(child => strings(child as Node | string)).join(''));
const text = (renderer: TestRenderer.ReactTestRenderer) => strings(renderer.toJSON());
const buttons = (renderer: TestRenderer.ReactTestRenderer) => renderer.root.findAllByType('button');
const button = (renderer: TestRenderer.ReactTestRenderer, label: string) => buttons(renderer).find(item => item.children.join('').includes(label))!;
async function render(node: ReactNode, path?: string) {
  let renderer!: TestRenderer.ReactTestRenderer;
  await act(async () => { renderer = TestRenderer.create(wrap(node, path)); await flush(); });
  for (let round = 0; round < 4; round += 1) await act(async () => { await flush(); });
  return renderer;
}

describe('T14 portal client', () => {
  it('sends only the portal token, never the staff token', async () => {
    store.access_token = 'STAFF-TOKEN';
    const handler = (portal.portalApi.interceptors.request as unknown as { handlers: Array<{ fulfilled: (c: InternalAxiosRequestConfig) => InternalAxiosRequestConfig }> }).handlers[0];
    const without = handler.fulfilled({ headers: new AxiosHeaders() } as InternalAxiosRequestConfig);
    expect(without.headers.Authorization).toBeUndefined();
    store.portal_access_token = 'PORTAL-TOKEN';
    const withPortal = handler.fulfilled({ headers: new AxiosHeaders() } as InternalAxiosRequestConfig);
    expect(withPortal.headers.Authorization).toBe('Bearer PORTAL-TOKEN');
  });

  it('turns server errors into Romanian messages', () => {
    const error = new AxiosError('x', '401', undefined, undefined, { status: 401, data: { detail: 'Email sau parolă incorectă.' } } as never);
    expect(portal.portalError(error)).toBe('Email sau parolă incorectă.');
    expect(portal.portalError(new AxiosError('network'))).toContain('Nu s-a putut contacta clinica');
  });
});

describe('T14 patient screens', () => {
  it('logs in to the clinic named in the link and shows the generic error', async () => {
    vi.spyOn(portal, 'clinicIdentity').mockResolvedValue({ id: 'clinic-1', name: 'Clinica Zâmbet' });
    const login = vi.spyOn(portal, 'portalLogin').mockRejectedValue(new AxiosError('x', '401', undefined, undefined, { status: 401, data: { detail: 'Email sau parolă incorectă.' } } as never));
    const renderer = await render(<PortalApp />, '/portal?clinic=clinic-1');
    expect(text(renderer)).toContain('Clinica Zâmbet');
    expect(text(renderer)).not.toContain('Patients');
    const [email, password] = renderer.root.findAllByType('input');
    await act(async () => { email.props.onChange({ target: { value: 'ana@example.com' } }); });
    await act(async () => { password.props.onChange({ target: { value: 'parola-fictiva-1' } }); });
    await act(async () => { renderer.root.findByType('form').props.onSubmit({ preventDefault() {} }); await flush(); });
    expect(login).toHaveBeenCalledWith('clinic-1', 'ana@example.com', 'parola-fictiva-1');
    expect(text(renderer)).toContain('Email sau parolă incorectă.');
  });

  it('asks for the clinic link when no clinic is known', async () => {
    const renderer = await render(<PortalApp />, '/portal');
    expect(text(renderer)).toContain('Deschide linkul primit de la clinică');
  });

  it('refuses mismatched passwords before accepting an invitation', async () => {
    vi.spyOn(portal, 'invitationInfo').mockResolvedValue({ clinic_id: 'c1', clinic_name: 'Clinica Zâmbet', full_name: 'Ana Fictivă', email: 'ana@example.com' });
    const accept = vi.spyOn(portal, 'acceptPortalInvitation').mockResolvedValue({ clinic_id: 'c1', clinic_name: 'Clinica Zâmbet', email: 'ana@example.com' });
    const renderer = await render(<PortalApp />, `/portal/invitatie?token=${'t'.repeat(40)}`);
    expect(text(renderer)).toContain('Setează parola');
    const [first, second] = renderer.root.findAllByType('input');
    expect(first.props.minLength).toBe(12);
    await act(async () => { first.props.onChange({ target: { value: 'parola-fictiva-1' } }); });
    await act(async () => { second.props.onChange({ target: { value: 'alta-parola-99' } }); });
    await act(async () => { renderer.root.findByType('form').props.onSubmit({ preventDefault() {} }); await flush(); });
    expect(text(renderer)).toContain('Parolele nu coincid.');
    expect(accept).not.toHaveBeenCalled();
  });

  async function home() {
    store.portal_access_token = 'PORTAL-TOKEN';
    vi.spyOn(portal, 'portalMe').mockResolvedValue({ account: { full_name: 'Elena Părinte', email: 'p@example.com' }, clinic: { id: 'c1', name: 'Clinica Zâmbet', timezone: 'Europe/Bucharest' },
      patients: [{ id: 'kid-1', name: 'Maria Fictivă', relationship: 'parent' }, { id: 'kid-2', name: 'Mihai Fictiv', relationship: 'parent' }] });
    vi.spyOn(portal, 'portalAppointments').mockImplementation(async id => ({ upcoming: [{
      id: `a-${id}`, starts_at: '2026-12-02T07:00:00+00:00', local_date: '2026-12-02', local_time: '09:00', duration_minutes: 30, doctor_name: 'Dr. Ionescu',
      clinic_name: 'Clinica Zâmbet', status: 'scheduled', status_label: 'Programată', confirmed_by_patient_at: null, reschedule_request: null, can_confirm: true, can_request_reschedule: true,
    }], past: [] }));
    return render(<PortalApp />);
  }

  it('lets a parent choose the child and see that child’s appointments', async () => {
    const renderer = await home();
    expect(text(renderer)).toContain('Elena Părinte');
    expect(text(renderer)).toContain('Maria Fictivă (Copil)');
    expect(text(renderer)).toContain('miercuri, 2 decembrie 2026');
    expect(text(renderer)).toContain('Ora 09:00');
    expect(portal.portalAppointments).toHaveBeenLastCalledWith('kid-1');
    await act(async () => { renderer.root.findByType('select').props.onChange({ target: { value: 'kid-2' } }); await flush(); });
    await act(async () => { await flush(); });
    expect(portal.portalAppointments).toHaveBeenLastCalledWith('kid-2');
  });

  it('confirms and asks for a reschedule with a message', async () => {
    const confirm = vi.spyOn(portal, 'confirmAppointment').mockResolvedValue({});
    const ask = vi.spyOn(portal, 'requestReschedule').mockResolvedValue({});
    const renderer = await home();
    await act(async () => { button(renderer, 'Confirm prezența').props.onClick(); await flush(); });
    expect(confirm).toHaveBeenCalledWith('kid-1', 'a-kid-1');
    await act(async () => { button(renderer, 'Cer reprogramare').props.onClick(); });
    expect(text(renderer)).toContain('Programarea rămâne neschimbată');
    await act(async () => { renderer.root.findByType('textarea').props.onChange({ target: { value: 'Pot doar după ora 16.' } }); });
    await act(async () => { renderer.root.findAllByType('form').slice(-1)[0].props.onSubmit({ preventDefault() {} }); await flush(); });
    expect(ask).toHaveBeenCalledWith('kid-1', 'a-kid-1', 'Pot doar după ora 16.', expect.stringMatching(/^[0-9a-f-]{36}$/));
  });

  it('shows a published plan with estimates', async () => {
    vi.spyOn(portal, 'portalPlans').mockResolvedValue([{ id: 'pub-1', published_at: '2026-11-25T10:00:00+00:00', title: 'Plan fictiv', version: 2,
      stages: [{ position: 1, title: 'Etapa A', items: [{ procedure_name: 'Obturație', tooth_fdi: '16', tooth_surface: null, estimated_price: '150.00', currency: 'RON', estimated_duration_minutes: 40 }] }],
      totals: [{ currency: 'RON', estimated_total: '150.00', estimated_minutes: 40 }] }]);
    const renderer = await home();
    await act(async () => { button(renderer, 'Planuri').props.onClick(); await flush(); });
    await act(async () => { await flush(); });
    expect(text(renderer)).toContain('Obturație · dinte 16');
    expect(text(renderer)).toContain('150.00 RON');
  });

  it('submits a form with only the visible answers', async () => {
    vi.spyOn(portal, 'portalForms').mockResolvedValue([{ id: 'f1', name: 'Anamneză', status: 'draft', status_label: 'De completat', editable: true, submitted_at: null }]);
    vi.spyOn(portal, 'portalForm').mockResolvedValue({ id: 'f1', name: 'Anamneză', status: 'draft', status_label: 'De completat', editable: true, submitted_at: null, answers: { detalii: 'veche' },
      definition: { sections: [{ id: 's', title: 'Sănătate', questions: [
        { id: 'alergii', label: 'Aveți alergii?', type: 'yes_no', required: true },
        { id: 'detalii', label: 'Care?', type: 'short_text', required: true, condition: { question_id: 'alergii', operator: 'equals', value: 'yes' } }] }] } });
    const submit = vi.spyOn(portal, 'submitPortalForm').mockResolvedValue({ id: 'f1', name: 'Anamneză', status: 'submitted', status_label: 'Trimis', editable: false, submitted_at: null });
    const renderer = await home();
    await act(async () => { button(renderer, 'Formulare').props.onClick(); await flush(); });
    await act(async () => { await flush(); });
    await act(async () => { button(renderer, 'Completează').props.onClick(); await flush(); });
    await act(async () => { await flush(); });
    const no = renderer.root.findAllByType('input').find(input => input.props.type === 'radio' && input.props.checked === false && input.parent?.children.includes('Nu'))!;
    await act(async () => { no.props.onChange(); });
    await act(async () => { renderer.root.findAllByType('form').slice(-1)[0].props.onSubmit({ preventDefault() {} }); await flush(); });
    expect(submit).toHaveBeenCalledWith('kid-1', 'f1', { alergii: false });
  });
});

const patient = { id: 'p1', first_name: 'Ana', last_name: 'Fictivă', email: 'ana@example.com' } as Patient;
const section: staff.PortalSection = {
  patient_id: 'p1', login_path: '/portal?clinic=c1', can: { manage_access: true, publish: true, send_forms: true },
  accounts: [{ link_id: 'l1', account_id: 'acc1', full_name: 'Ana Fictivă', email: 'ana@example.com', relationship: 'self', relationship_label: 'Pacientul', link_active: true, status: 'invited', status_label: 'Invitat, fără parolă', activated_at: null, invitation_expires_at: '2026-11-28T10:00:00+00:00' }],
  documents: [], plans: [],
  forms: [{ id: 'f1', name: 'Anamneză', status: 'submitted', source: 'patient', sent_at: '2026-11-25T10:00:00+00:00', submissions: [{ revision_number: 1, submitted_at: '2026-11-25T11:00:00+00:00', completed_by: 'Ana Fictivă', label: 'Completat de pacient', answers: { alergii: false } }] }],
};

describe('T14 staff screens', () => {
  function stubCandidates() {
    vi.spyOn(documentsApi, 'listPatientDocuments').mockResolvedValue([{ id: 'd1', original_filename: 'radiografie.png', status: 'active' }] as never);
    vi.spyOn(plansApi, 'listPatientPlans').mockResolvedValue([{ id: 'plan1', title: 'Plan fictiv', status: 'accepted' }] as never);
    vi.spyOn(formsApi, 'listPublishedForms').mockResolvedValue([{ id: 't1', name: 'Anamneză', version_id: 'v1', version_number: 1 }]);
  }

  it('creates an invitation and shows a link to copy, without sending email', async () => {
    stubCandidates();
    vi.spyOn(staff, 'getPortalSection').mockResolvedValue(section);
    const grant = vi.spyOn(staff, 'grantPortalAccess').mockResolvedValue({ account_id: 'acc1', link_id: 'l1', status: 'invited', invitation_url: '/portal/invitatie?token=abc', invitation_expires_at: '2026-11-28T10:00:00+00:00' });
    const renderer = await render(<PatientPortalSection patient={patient} canClinical />, '/patients/p1');
    expect(text(renderer)).toContain('Invitat, fără parolă');
    expect(text(renderer)).toContain('Completat de pacient');
    const form = renderer.root.findAllByType('form').find(item => item.findAllByType('h3').some(h => h.children.join('') === 'Invită în portal'))!;
    await act(async () => { form.props.onSubmit({ preventDefault() {} }); await flush(); });
    expect(grant).toHaveBeenCalledWith('p1', { email: 'ana@example.com', full_name: 'Ana Fictivă', relationship: 'self' });
    expect(renderer.root.findAllByType('input').filter(input => input.props.readOnly).map(input => input.props.value)).toContain('https://clinica.test/portal/invitatie?token=abc');
    expect(text(renderer)).toContain('Nu se trimite niciun email');
  });

  it('publishes a document and hides publishing from non-clinical roles', async () => {
    stubCandidates();
    vi.spyOn(staff, 'getPortalSection').mockResolvedValue(section);
    const publish = vi.spyOn(staff, 'publishPortalDocument').mockResolvedValue({});
    const renderer = await render(<PatientPortalSection patient={patient} canClinical />, '/patients/p1');
    await act(async () => { button(renderer, 'Publică în portal').props.onClick(); await flush(); });
    expect(publish).toHaveBeenCalledWith('p1', 'd1');
    vi.spyOn(staff, 'getPortalSection').mockResolvedValue({ ...section, documents: null, plans: null, can: { manage_access: false, publish: false, send_forms: true } });
    const reception = await render(<PatientPortalSection patient={patient} canClinical={false} />, '/patients/p1');
    expect(text(reception)).not.toContain('Documente în portal');
    expect(text(reception)).not.toContain('Invită în portal');
    expect(text(reception)).not.toContain('alergii');
  });

  it('lists reschedule requests and resolves one', async () => {
    vi.spyOn(staff, 'listRescheduleRequests').mockResolvedValue([{ id: 'r1', status: 'open', message: 'Pot doar după ora 16.', created_at: '2026-11-25T10:00:00+00:00', patient_id: 'p1', patient_name: 'Ana Fictivă', requested_by: 'Ana Fictivă', appointment_id: 'a1', appointment_starts_at: '2026-12-02T07:00:00+00:00', appointment_local: '2026-12-02 09:00', appointment_status: 'scheduled', doctor_name: 'Dr. Ionescu', resolved_at: null, resolution_note: null }]);
    const resolve = vi.spyOn(staff, 'resolveRescheduleRequest').mockResolvedValue({});
    const renderer = await render(<RescheduleRequestsPage />, '/portal-requests');
    expect(text(renderer)).toContain('Pot doar după ora 16.');
    expect(text(renderer)).toContain('Cererea nu mută programarea');
    await act(async () => { button(renderer, 'Reprogramată').props.onClick(); await flush(); });
    expect(resolve).toHaveBeenCalledWith('r1', 'rescheduled', '');
  });
});
