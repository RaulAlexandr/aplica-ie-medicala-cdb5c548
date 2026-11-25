import TestRenderer, { act } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as clientApi from '../api/client';
import * as commsApi from './api';
import type { Consents, Message, Template } from './api';
import { unknownVariables, reasonLabel, statusLabel } from './labels';
import { CommsPage } from './CommsPage';
import { PatientCommsPanel } from './PatientComms';

const noConsents: Consents = { current: { sms: null, email: null }, history: [] };
const message = (values: Partial<Message> = {}): Message => ({
  id: 'm1', patient_id: 'p1', patient_name: 'Ana Fictiv', appointment_id: 'a1', message_type: 'confirmation', channel: 'sms', status: 'pending',
  scheduled_for: '2030-05-01T07:00:00Z', appointment_starts_at: '2030-05-02T07:00:00Z', attempts: 0, reason: null, last_error: null, subject: null, body: null,
  provider_ref: null, adapter: null, template_version: null, sent_at: null, created_at: '2030-05-01T06:00:00Z',
  events: [{ id: 'e1', from_status: null, to_status: 'pending', reason: 'queued', occurred_at: '2030-05-01T06:00:00Z' }], ...values,
});
const templates: Template[] = (['confirmation', 'reminder', 'cancellation', 'reschedule', 'feedback_request', 'review_invitation', 'campaign'] as const).map(t => ({ message_type: t, version: 0, subject: `Subiect ${t}`, body: `Text ${t} {prenume}`, created_at: null }));

const text = (node: TestRenderer.ReactTestInstance): string => node.children.map(c => typeof c === 'string' ? c : text(c)).join('');
const newClient = () => new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
async function waitFor(condition: () => boolean, what: string) {
  const deadline = Date.now() + 2000;
  while (Date.now() < deadline) {
    let ok = false;
    await act(async () => { ok = condition(); if (!ok) await new Promise(r => setTimeout(r, 5)); });
    if (ok) return;
  }
  throw new Error(`Timed out waiting for ${what}`);
}
async function mount(node: JSX.Element) {
  vi.spyOn(clientApi, 'getClinic').mockResolvedValue({ timezone: 'Europe/Bucharest' } as never);
  let renderer!: TestRenderer.ReactTestRenderer;
  await act(async () => { renderer = TestRenderer.create(<QueryClientProvider client={newClient()}>{node}</QueryClientProvider>); });
  return renderer;
}
const button = (r: TestRenderer.ReactTestRenderer, label: string | RegExp) => r.root.findAll(n => n.type === 'button' && (typeof label === 'string' ? text(n) === label : label.test(text(n))))[0];
const click = (b: TestRenderer.ReactTestInstance) => act(async () => { b.props.onClick(); });

afterEach(() => vi.restoreAllMocks());

describe('labels', () => {
  it('flags variables that are not on the allowed list and unpaired braces', () => {
    expect(unknownVariables('Salut {prenume}, {data} {ora}', '{medic} {clinica} {telefon_clinica}')).toEqual([]);
    expect(unknownVariables('Alergie {alergii}', 'x {diagnostic}')).toEqual(['alergii', 'diagnostic']);
    expect(unknownVariables('{prenume')).toEqual(['acoladă nepereche']);
  });
  it('marks simulated sends and translates reasons', () => {
    expect(statusLabel({ status: 'sent', adapter: 'simulated' })).toBe('Trimis (simulat)');
    expect(statusLabel({ status: 'cancelled', adapter: null })).toBe('Anulat');
    expect(reasonLabel('no_consent')).toBe('Fără acord pe acest canal');
    expect(reasonLabel('retry 1/3: provider indisponibil')).toBe('Reîncercare 1/3: provider indisponibil');
  });
});

describe('PatientCommsPanel', () => {
  it('shows no consent, the reason a message was not sent, and the simulated send', async () => {
    vi.spyOn(commsApi, 'getConsents').mockResolvedValue(noConsents);
    vi.spyOn(commsApi, 'patientMessages').mockResolvedValue([
      message({ id: 'm1', status: 'cancelled', reason: 'no_consent', events: [{ id: 'e1', from_status: null, to_status: 'pending', reason: 'queued', occurred_at: '2030-05-01T06:00:00Z' }, { id: 'e2', from_status: 'pending', to_status: 'cancelled', reason: 'no_consent', occurred_at: '2030-05-01T06:01:00Z' }] }),
      message({ id: 'm2', channel: 'email', status: 'sent', adapter: 'simulated', body: 'Bună, Ana!' }),
    ]);
    const r = await mount(<PatientCommsPanel patientId="p1" canRecord />);
    await waitFor(() => text(r.root).includes('Istoric mesaje') && text(r.root).includes('Bună, Ana!'), 'history');
    const all = text(r.root);
    expect(all).toContain('Comunicare');
    expect(all.match(/Fără acord înregistrat/g)).toHaveLength(2);
    expect(all).toContain('Motiv: Fără acord pe acest canal');
    expect(all).toContain('Anulat');
    expect(all).toContain('Trimis (simulat)');
  });

  it('records consent for a channel with the chosen source, and withdrawal is offered once consent exists', async () => {
    vi.spyOn(commsApi, 'getConsents').mockResolvedValue(noConsents);
    vi.spyOn(commsApi, 'patientMessages').mockResolvedValue([]);
    const record = vi.spyOn(commsApi, 'recordConsent').mockResolvedValue({ id: 'c1', channel: 'sms', granted: true, source: 'in_person', recorded_by_id: 'u1', recorded_by: 'Recepție', recorded_at: '2030-05-01T06:00:00Z' });
    const r = await mount(<PatientCommsPanel patientId="p1" canRecord />);
    await waitFor(() => Boolean(button(r, 'Înregistrează acord SMS')), 'consent button');
    await click(button(r, 'Înregistrează acord SMS'));
    expect(record).toHaveBeenCalledWith('p1', { channel: 'sms', granted: true, source: 'in_person' });
  });

  it('offers withdrawal for a granted channel and hides the buttons for roles that cannot record', async () => {
    const granted: Consents = { current: { sms: { id: 'c1', channel: 'sms', granted: true, source: 'phone', recorded_by_id: 'u1', recorded_by: 'Recepție', recorded_at: '2030-05-01T06:00:00Z' }, email: null }, history: [] };
    vi.spyOn(commsApi, 'getConsents').mockResolvedValue(granted);
    vi.spyOn(commsApi, 'patientMessages').mockResolvedValue([]);
    const record = vi.spyOn(commsApi, 'recordConsent').mockResolvedValue({} as never);
    const r = await mount(<PatientCommsPanel patientId="p1" canRecord />);
    await waitFor(() => Boolean(button(r, 'Înregistrează dezabonare SMS')), 'withdraw button');
    await click(button(r, 'Înregistrează dezabonare SMS'));
    expect(record).toHaveBeenCalledWith('p1', { channel: 'sms', granted: false, source: 'in_person' });
    const readOnly = await mount(<PatientCommsPanel patientId="p1" canRecord={false} />);
    await waitFor(() => text(readOnly.root).includes('Acord da'), 'read-only view');
    expect(readOnly.root.findAll(n => n.type === 'button')).toHaveLength(0);
  });
});

describe('CommsPage (manager)', () => {
  it('refuses a forbidden variable without calling the server, and saves a valid template', async () => {
    vi.spyOn(commsApi, 'listTemplates').mockResolvedValue(templates);
    const save = vi.spyOn(commsApi, 'saveTemplate').mockResolvedValue({ ...templates[0], version: 1 });
    const r = await mount(<CommsPage />);
    await waitFor(() => r.root.findAll(n => n.type === 'form').length === 7, 'template forms');
    const form = r.root.findByProps({ 'aria-label': 'Șablon Confirmare programare' });
    const body = form.findAll(n => n.type === 'textarea')[0];
    await act(async () => { body.props.onChange({ target: { value: 'Alergie {alergii}' } }); });
    await act(async () => { form.props.onSubmit({ preventDefault() {} }); });
    expect(text(r.root)).toContain('Variabile nepermise: alergii');
    expect(save).not.toHaveBeenCalled();
    await act(async () => { body.props.onChange({ target: { value: 'Salut {prenume}, {data} la {ora}' } }); });
    await act(async () => { form.props.onSubmit({ preventDefault() {} }); });
    expect(save).toHaveBeenCalledWith('confirmation', { subject: 'Subiect confirmation', body: 'Salut {prenume}, {data} la {ora}' });
  });

  it('filters the queue by status and shows the result of a processing run', async () => {
    const list = vi.spyOn(commsApi, 'listOutbox').mockResolvedValue([message({ status: 'failed', reason: 'attempts_exhausted', last_error: 'provider indisponibil', attempts: 3 })]);
    vi.spyOn(commsApi, 'processOutbox').mockResolvedValue({ sent: 2, failed: 0, cancelled: 1, retry: 0, deferred: 0 });
    vi.spyOn(commsApi, 'listTemplates').mockResolvedValue(templates);
    const r = await mount(<CommsPage />);
    await click(button(r, 'Coada de mesaje'));
    await waitFor(() => text(r.root).includes('Ana Fictiv'), 'queue');
    expect(text(r.root)).toContain('Încercări epuizate');
    expect(text(r.root)).toContain('Ultima eroare: provider indisponibil');
    const select = r.root.findAll(n => n.type === 'select')[0];
    await act(async () => { select.props.onChange({ target: { value: 'failed' } }); });
    await waitFor(() => list.mock.calls.some(c => c[0] === 'failed'), 'filtered request');
    await click(button(r, 'Procesează mesajele scadente'));
    await waitFor(() => text(r.root).includes('2 trimise, 1 anulate'), 'processing summary');
  });
});
