import TestRenderer, { act } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as client from '../api/client';
import * as team from '../team/api';
import * as collab from './api';
import { CollabNavLink, CollabPage, Composer, HandoverCard, MEDICAL_NOTICE, POLL_MS, POLL_OPTIONS, TaskRow } from './CollabPage';

const tz = 'Europe/Bucharest';
const userWith = (role: string) => ({ id: 'me', clinic_id: 'c', full_name: 'Persoană Fictivă', email: 'x@example.com', role }) as client.User;
const member = (id: string, role: string): team.TeamMember => ({ id, full_name: `Nume ${id}`, role, is_active: true, specialization: null, job_title: null });
const message = (seq: number, extra: Partial<collab.Message> = {}): collab.Message => ({ id: `m${seq}`, conversation_id: 'k1', seq, sender_id: 'o', sender: 'Nume o', body: `Mesaj ${seq}`, client_request_id: 'r', created_at: '2026-11-23T07:00:00Z', recipients: [], ...extra });
const conversation = (extra: Partial<collab.Conversation> = {}): collab.Conversation => ({ id: 'k1', kind: 'direct', title: 'Nume o', room_id: null, created_by_id: 'me', last_read_seq: 0, unread: 2, can_write: true, members: [], last_message: null, activity_at: '2026-11-23T07:00:00Z', ...extra });
const task = (extra: Partial<collab.Task> = {}): collab.Task => ({ id: 't1', title: 'Sarcină fictivă', description: null, assignee_id: 'me', assignee: 'Persoană Fictivă', created_by_id: 'o', created_by: 'Nume o', due_at: null, status: 'open', version: 3, overdue: false, created_at: '', updated_at: '', events: [], ...extra });
const handover = (extra: Partial<collab.Handover> = {}): collab.Handover => ({ id: 'h1', shift_id: 's1', shift_starts_at: '2026-11-23T07:00:00Z', shift_ends_at: '2026-11-23T11:00:00Z', from_user_id: 'o', from_user: 'Nume o', to_user_id: 'me', to_user: 'Persoană Fictivă', created_by: 'Nume o', notes: 'Note fictive', created_at: '2026-11-23T11:00:00Z', acknowledged_at: null, tasks: [], ...extra });
const text = (node: TestRenderer.ReactTestInstance): string => node.children.map(child => (typeof child === 'string' ? child : text(child))).join('');
const flush = async () => { for (let i = 0; i < 6; i += 1) await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); }); };
const buttons = (r: TestRenderer.ReactTestRenderer, label: string) => r.root.findAllByType('button').filter(item => text(item).startsWith(label));
const submit = (r: TestRenderer.ReactTestRenderer) => r.root.findByType('form').props.onSubmit({ preventDefault() {} });
const type = async (r: TestRenderer.ReactTestRenderer, value: string) => { await act(async () => { r.root.findByType('textarea').props.onChange({ target: { value } }); }); };

async function render(node: ReactNode) {
  let renderer!: TestRenderer.ReactTestRenderer;
  const queries = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => { renderer = TestRenderer.create(<QueryClientProvider client={queries}><MemoryRouter>{node}</MemoryRouter></QueryClientProvider>); });
  await flush();
  return renderer;
}

beforeEach(() => {
  vi.spyOn(client, 'getClinic').mockResolvedValue({ id: 'c', name: 'Clinică', timezone: tz, created_at: '', updated_at: '' });
  vi.spyOn(client, 'listRooms').mockResolvedValue([{ id: 'r1', clinic_id: 'c', name: 'Cabinet 3', is_active: true }]);
  vi.spyOn(team, 'listMembers').mockResolvedValue([member('me', 'doctor'), member('o', 'reception')]);
  vi.spyOn(team, 'getSchedule').mockResolvedValue({ timezone: tz, range_start: '', range_end: '', days: [], shifts: [], leaves: [] });
  vi.spyOn(collab, 'getSummary').mockResolvedValue({ unread: 0, pending_handovers: 0 });
  vi.spyOn(collab, 'listConversations').mockResolvedValue([]);
  vi.spyOn(collab, 'listMessages').mockResolvedValue({ messages: [], has_more: false });
  vi.spyOn(collab, 'markRead').mockResolvedValue({ last_read_seq: 0 });
  vi.spyOn(collab, 'listTasks').mockResolvedValue([]);
  vi.spyOn(collab, 'listHandovers').mockResolvedValue([]);
});
afterEach(() => { vi.restoreAllMocks(); });

describe('T20 request ids', () => {
  it('creates valid, distinct version 4 UUIDs without a secure context', () => {
    const first = collab.newRequestId();
    expect(first).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    expect(collab.newRequestId()).not.toBe(first);
  });
});

describe('T20 composer', () => {
  it('shows the medical data notice', async () => {
    const r = await render(<Composer target={{ kind: 'conversation', id: 'k1' }} onSent={() => {}} />);
    expect(text(r.root)).toContain(MEDICAL_NOTICE);
    expect(MEDICAL_NOTICE).toBe('Nu scrie date medicale în chat; folosește dosarul pacientului');
    await act(async () => r.unmount());
  });
  it('keeps the text after a failed send and retries with the same request id', async () => {
    const send = vi.spyOn(collab, 'sendMessage').mockRejectedValueOnce(new Error('Rețea indisponibilă')).mockResolvedValueOnce(message(1));
    const onSent = vi.fn();
    const r = await render(<Composer target={{ kind: 'conversation', id: 'k1' }} onSent={onSent} />);
    await type(r, 'Text important');
    await act(async () => { submit(r); });
    await flush();
    expect(r.root.findByType('textarea').props.value).toBe('Text important');
    expect(text(r.root)).toContain('Rețea indisponibilă');
    expect(onSent).not.toHaveBeenCalled();
    await act(async () => { submit(r); });
    await flush();
    expect(send.mock.calls[0][2]).toBe(send.mock.calls[1][2]);
    expect(r.root.findByType('textarea').props.value).toBe('');
    expect(onSent).toHaveBeenCalledTimes(1);
    await act(async () => r.unmount());
  });
  it('uses a new request id when the text changes after a failure', async () => {
    const send = vi.spyOn(collab, 'sendMessage').mockRejectedValueOnce(new Error('Eroare')).mockResolvedValueOnce(message(1));
    const r = await render(<Composer target={{ kind: 'conversation', id: 'k1' }} onSent={() => {}} />);
    await type(r, 'Primul');
    await act(async () => { submit(r); });
    await flush();
    await type(r, 'Primul, corectat');
    await act(async () => { submit(r); });
    await flush();
    expect(send.mock.calls[0][2]).not.toBe(send.mock.calls[1][2]);
    await act(async () => r.unmount());
  });
  it('sends once on a double submit and refuses blank text', async () => {
    let finish!: (value: collab.Message) => void;
    const send = vi.spyOn(collab, 'sendMessage').mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    const r = await render(<Composer target={{ kind: 'conversation', id: 'k1' }} onSent={() => {}} />);
    await type(r, '    ');
    await act(async () => { submit(r); });
    expect(send).not.toHaveBeenCalled();
    await type(r, 'Un mesaj');
    await act(async () => { submit(r); });
    await act(async () => { submit(r); });
    expect(send).toHaveBeenCalledTimes(1);
    await act(async () => { finish(message(1)); });
    await flush();
    await act(async () => r.unmount());
  });
  it('sends a room message to the room route and shows a refusal without losing the text', async () => {
    const room = vi.spyOn(collab, 'sendToRoom').mockRejectedValue(new Error('Nimeni nu este repartizat acum la Cabinet 3. Mesajul nu a fost trimis.'));
    const r = await render(<Composer target={{ kind: 'room', id: 'r1' }} onSent={() => {}} />);
    await type(r, 'Către cabinet');
    await act(async () => { submit(r); });
    await flush();
    expect(room.mock.calls[0][0]).toBe('r1');
    expect(text(r.root)).toContain('Nimeni nu este repartizat');
    expect(r.root.findByType('textarea').props.value).toBe('Către cabinet');
    await act(async () => r.unmount());
  });
});

describe('T20 unread and polling', () => {
  it('shows the unread badge in the menu', async () => {
    vi.mocked(collab.getSummary).mockResolvedValue({ unread: 3, pending_handovers: 0 });
    const r = await render(<CollabNavLink />);
    expect(text(r.root)).toContain('Mesaje și sarcini3');
    await act(async () => r.unmount());
  });
  it('shows no badge without unread messages', async () => {
    const r = await render(<CollabNavLink />);
    expect(r.root.findAll(item => item.props.className === 'collab-badge')).toHaveLength(0);
    await act(async () => r.unmount());
  });
  it('polls every 15 seconds and not in the background (real timing is proven in the browser run)', () => {
    expect(POLL_MS).toBe(15000);
    expect(POLL_OPTIONS).toEqual({ refetchInterval: 15000, refetchIntervalInBackground: false });
  });
  it('marks a conversation read up to the last displayed message', async () => {
    vi.mocked(collab.listConversations).mockResolvedValue([conversation()]);
    vi.mocked(collab.listMessages).mockResolvedValue({ messages: [message(1), message(2)], has_more: false });
    const r = await render(<CollabPage user={userWith('doctor')} />);
    await act(async () => { r.root.findAll(item => item.type === 'button' && item.props.className === 'collab-conversation')[0].props.onClick(); });
    await flush();
    expect(collab.markRead).toHaveBeenCalledWith('k1', 2);
    expect(text(r.root)).toContain('Mesaj 2');
    await act(async () => r.unmount());
  });
  it('keeps history readable but offers no composer for an inactive person', async () => {
    vi.mocked(collab.listConversations).mockResolvedValue([conversation({ can_write: false, unread: 0 })]);
    vi.mocked(collab.listMessages).mockResolvedValue({ messages: [message(1)], has_more: false });
    const r = await render(<CollabPage user={userWith('doctor')} />);
    await act(async () => { r.root.findAll(item => item.type === 'button' && item.props.className === 'collab-conversation')[0].props.onClick(); });
    await flush();
    const thread = r.root.findAll(item => item.type === 'section' && item.props['aria-label'] === 'Conversație')[0];
    expect(text(thread)).toContain('Mesaj 1');
    expect(text(thread)).toContain('Persoana este inactivă');
    expect(thread.findAllByType('textarea')).toHaveLength(0);
    await act(async () => r.unmount());
  });
});

describe('T20 tasks', () => {
  const select = (r: TestRenderer.ReactTestRenderer, index: number, value: string) => act(async () => { r.root.findAllByType('select')[index].props.onChange({ target: { value } }); });
  it('requires a reason before blocking and sends the expected version', async () => {
    const change = vi.spyOn(collab, 'changeTask').mockResolvedValue(task({ status: 'blocked', version: 4 }));
    const r = await render(<TaskRow task={task()} user={userWith('doctor')} members={[member('me', 'doctor')]} timezone={tz} />);
    await select(r, 0, 'blocked');
    expect(buttons(r, 'Aplică')[0].props.disabled).toBe(true);
    await act(async () => { r.root.findByType('input').props.onChange({ target: { value: 'Lipsește materialul' } }); });
    expect(buttons(r, 'Aplică')[0].props.disabled).toBe(false);
    await act(async () => { submit(r); });
    await flush();
    expect(change).toHaveBeenCalledWith('t1', { expected_version: 3, status: 'blocked', reason: 'Lipsește materialul' });
    await act(async () => r.unmount());
  });
  it('shows a concurrent change error', async () => {
    vi.spyOn(collab, 'changeTask').mockRejectedValue(new Error('Sarcina a fost modificată între timp. Reîncarcă și încearcă din nou.'));
    const r = await render(<TaskRow task={task()} user={userWith('doctor')} members={[member('me', 'doctor')]} timezone={tz} />);
    await select(r, 0, 'resolved');
    await act(async () => { submit(r); });
    await flush();
    expect(text(r.root)).toContain('modificată între timp');
    await act(async () => r.unmount());
  });
  it('offers reopening of a closed task only to a manager', async () => {
    const closed = task({ status: 'resolved' });
    const doctor = await render(<TaskRow task={closed} user={userWith('doctor')} members={[]} timezone={tz} />);
    expect(doctor.root.findAllByType('form')).toHaveLength(0);
    await act(async () => doctor.unmount());
    const manager = await render(<TaskRow task={closed} user={userWith('clinic_manager')} members={[]} timezone={tz} />);
    expect(text(manager.root.findAllByType('select')[0])).toContain('Redeschide');
    await act(async () => manager.unmount());
  });
  it('marks an overdue task in clinic time', async () => {
    const r = await render(<TaskRow task={task({ due_at: '2026-11-23T15:00:00Z', overdue: true })} user={userWith('doctor')} members={[]} timezone={tz} />);
    expect(text(r.root)).toContain('17:00');
    expect(text(r.root)).toContain('întârziată');
    await act(async () => r.unmount());
  });
});

describe('T20 handovers', () => {
  it('lets only the recipient confirm', async () => {
    const ack = vi.spyOn(collab, 'acknowledgeHandover').mockResolvedValue(handover({ acknowledged_at: '2026-11-23T12:00:00Z' }));
    const recipient = await render(<HandoverCard item={handover()} user={userWith('doctor')} timezone={tz} />);
    await act(async () => { buttons(recipient, 'Confirm primirea')[0].props.onClick(); });
    await flush();
    expect(ack).toHaveBeenCalledWith('h1');
    await act(async () => recipient.unmount());
    const other = await render(<HandoverCard item={handover({ to_user_id: 'x', to_user: 'Altcineva' })} user={userWith('clinic_manager')} timezone={tz} />);
    expect(buttons(other, 'Confirm primirea')).toHaveLength(0);
    expect(text(other.root)).toContain('Așteaptă confirmarea');
    await act(async () => other.unmount());
  });
  it('shows the frozen task list and the confirmation', async () => {
    const r = await render(<HandoverCard item={handover({ acknowledged_at: '2026-11-23T12:00:00Z', tasks: [{ task_id: 't1', title: 'Sarcină rămasă', status: 'blocked', due_at: null }] })} user={userWith('doctor')} timezone={tz} />);
    expect(text(r.root)).toContain('Sarcină rămasă · Blocată');
    expect(text(r.root)).toContain('Primire confirmată');
    expect(buttons(r, 'Confirm primirea')).toHaveLength(0);
    await act(async () => r.unmount());
  });
});
