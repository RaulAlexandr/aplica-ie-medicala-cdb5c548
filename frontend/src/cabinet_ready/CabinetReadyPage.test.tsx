import { readFileSync } from 'node:fs';
import TestRenderer, { act } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AxiosError, AxiosHeaders } from 'axios';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as clientApi from '../api/client';
import type { User } from '../api/client';
import * as cabinetApi from './api';
import type { CabinetBoard, CabinetCard } from './api';
import { cabinetErrorText, CabinetReadyPage } from './CabinetReadyPage';

const user = (role: string, id = `${role}-1`): User => ({ id, clinic_id: 'clinic-1', full_name: 'Utilizator Fictiv', email: `${role}@example.test`, role });
const staffCard = (values: Partial<CabinetCard> = {}): CabinetCard => ({
  appointment_id: 'appointment-1', starts_at: '2026-12-02T09:00:00Z', ends_at: '2026-12-02T09:30:00Z', local_time: '11:00', local_date: '2026-12-02',
  appointment_status: 'scheduled', room_id: 'room-1', room_name: 'Cabinet fictiv', doctor_id: 'doctor-1', doctor_name: 'Dr. Fictiv', patient_name: 'Pacient Fictiv',
  state: 'in_preparation', state_label: 'În pregătire', confirmed_required: 0, total_required: 1, counter: '0 din 1', started: true,
  start_by: '2026-12-02T08:50:00Z', start_by_local: '10:50', late: false, blockers: [], warnings: [], missing: ['De confirmat: Trusă'],
  explanation: null, prep_minutes: 10, particularities: [],
  items: [{ id: 'item-1', kind: 'instrument', label: 'Trusă', required: true, prep_minutes: 10, procedure_name: 'Procedură', from_particularity: false, confirmed: false, confirmed_by: null, confirmed_at: null }],
  ...values,
});
const boardWith = (cards: CabinetCard[]): CabinetBoard => ({ day: '2026-12-02', timezone: 'Europe/Bucharest', server_now: '2026-12-02T08:00:00Z', rooms: [{ id: 'room-1', name: 'Cabinet fictiv' }], cards });
const text = (node: TestRenderer.ReactTestInstance): string => node.children.map(child => typeof child === 'string' ? child : text(child)).join('');
const flush = () => new Promise(resolve => setTimeout(resolve, 15));
const button = (renderer: TestRenderer.ReactTestRenderer, label: string) => renderer.root.findAll(node => node.type === 'button' && text(node).includes(label))[0];

async function render(account: User, board: CabinetBoard) {
  vi.spyOn(clientApi, 'getClinic').mockResolvedValue({ timezone: 'Europe/Bucharest' } as never);
  vi.spyOn(clientApi, 'listProcedures').mockResolvedValue([]);
  vi.spyOn(cabinetApi, 'getBoard').mockResolvedValue(board);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
  let renderer!: TestRenderer.ReactTestRenderer;
  await act(async () => { renderer = TestRenderer.create(<QueryClientProvider client={client}><CabinetReadyPage user={account} /></QueryClientProvider>); await flush(); });
  await act(async () => { await flush(); });
  return renderer;
}

afterEach(() => vi.restoreAllMocks());

describe('CabinetReadyPage', () => {
  it('shows the sterilization disclaimer and what is missing before the actions', async () => {
    const renderer = await render(user('assistant'), boardWith([staffCard()]));
    const page = text(renderer.root);
    expect(page).toContain('Bifa de pregătire nu înseamnă trasabilitate de sterilizare.');
    expect(page.indexOf('Ce lipsește')).toBeLessThan(page.indexOf('Confirm fizic'));
    expect(page).toContain('0 din 1');
  });

  it('a double click sends one confirmation', async () => {
    const renderer = await render(user('assistant'), boardWith([staffCard()]));
    let finish!: (value: unknown) => void;
    const confirm = vi.spyOn(cabinetApi, 'confirmItem').mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    const target = button(renderer, 'Confirm fizic');
    await act(async () => { target.props.onClick(); target.props.onClick(); await flush(); });
    expect(confirm).toHaveBeenCalledTimes(1);
    await act(async () => { finish({}); await flush(); });
  });

  it('reuses the request key after a failed confirmation and shows the error beside the action', async () => {
    const renderer = await render(user('assistant'), boardWith([staffCard()]));
    const failure = new AxiosError('Request failed', 'ERR_BAD_RESPONSE', undefined, undefined, { status: 500, statusText: 'Error', headers: {}, config: { headers: new AxiosHeaders() }, data: 'Internal Server Error' });
    const confirm = vi.spyOn(cabinetApi, 'confirmItem').mockRejectedValueOnce(failure).mockResolvedValueOnce({});
    await act(async () => { button(renderer, 'Confirm fizic').props.onClick(); await flush(); });
    expect(text(renderer.root.findByProps({ role: 'alert' }))).toContain('Serverul a întâmpinat o eroare');
    await act(async () => { button(renderer, 'Confirm fizic').props.onClick(); await flush(); });
    expect(confirm.mock.calls[0][1]).toBe(confirm.mock.calls[1][1]);
  });

  it('reception sees state, counter and block type without actions or details', async () => {
    const card = { ...staffCard({ state: 'blocked', state_label: 'Blocat', blockers: [{ type: 'manual', type_label: 'Blocaj manual' }] }) };
    for (const key of ['items', 'missing', 'warnings', 'particularities', 'explanation', 'prep_minutes'] as const) delete card[key];
    const renderer = await render(user('reception'), boardWith([card]));
    const page = text(renderer.root);
    expect(page).toContain('Blocat · 0 din 1');
    expect(page).toContain('Blocaj manual');
    expect(page).not.toContain('Confirm fizic');
    expect(page).not.toContain('Adaugă blocaj');
    expect(page).not.toContain('Checklisturi');
    expect(page).not.toContain('Propune');
  });

  it('only the appointment doctor sees approve and reject', async () => {
    const particularities = [{ id: 'p-1', text: 'Particularitate', status: 'pending' as const, item_label: null, item_kind: null, item_required: null, item_minutes: null }];
    const own = await render(user('doctor', 'doctor-1'), boardWith([staffCard({ particularities })]));
    expect(button(own, 'Aprobă')).toBeTruthy();
    const other = await render(user('doctor', 'doctor-2'), boardWith([staffCard({ particularities })]));
    expect(button(other, 'Aprobă')).toBeUndefined();
  });

  it('explains a change after confirmation and offers a restart', async () => {
    const explanation = 'S-a schimbat ora după începerea pregătirii.';
    const renderer = await render(user('assistant'), boardWith([staffCard({ started: false, explanation, missing: [explanation], items: [{ ...staffCard().items![0], id: null }] })]));
    expect(text(renderer.root)).toContain(explanation);
    expect(button(renderer, 'Reîncepe pregătirea')).toBeTruthy();
  });

  it('does not promise a start time when no required minutes are known', async () => {
    const renderer = await render(user('assistant'), boardWith([staffCard({ state: 'unknown', state_label: 'Necunoscut', total_required: 0, counter: '0 din 0', items: [], missing: ['Programarea nu are nicio procedură efectivă.'] })]));
    const page = text(renderer.root);
    expect(page).toContain('Timpul de pregătire nu se poate calcula');
    expect(page).not.toContain('Începe pregătirea până la');
  });

  it('keeps the page content at the top when it is shorter than the screen', () => {
    const pageCss = readFileSync(new URL('./cabinet-ready.css', import.meta.url), 'utf8');
    expect(pageCss).toMatch(/\.cabinet-page \{[^}]*align-content: start/);
  });

  it('turns a non-JSON server failure into Romanian text', () => {
    const failure = new AxiosError('Request failed with status code 500', 'ERR_BAD_RESPONSE', undefined, undefined, { status: 500, statusText: 'Error', headers: {}, config: { headers: new AxiosHeaders() }, data: '<html>' });
    expect(cabinetErrorText(failure)).toBe('Serverul a întâmpinat o eroare. Valorile rămân; încercați din nou.');
  });
});
