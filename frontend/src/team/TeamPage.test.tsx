import TestRenderer, { act } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as client from '../api/client';
import * as team from './api';
import { addDays, attendanceHours, clinicDate, formatTime, mondayOf, shiftsForDay, TeamPage, workedLabel } from './TeamPage';

const tz = 'Europe/Bucharest';
const userWith = (role: string) => ({ id: 'me', clinic_id: 'c', full_name: 'Persoană Fictivă', email: 'x@example.com', role }) as client.User;
const member = (id: string, role: string): team.TeamMember => ({ id, full_name: `Nume ${id}`, role, is_active: true, specialization: null, job_title: null });
const shift: team.Shift = { id: 's1', user_id: 'me', user_name: 'Persoană Fictivă', room_id: 'r1', room_name: 'Cabinet 1', post: 'doctor', starts_at: '2026-11-23T07:00:00Z', ends_at: '2026-11-23T11:00:00Z', local_start: '', local_end: '', status: 'active', user_is_active: true };
const day: team.ScheduleDay = { date: '2026-11-23', start: '2026-11-22T22:00:00Z', end: '2026-11-23T22:00:00Z', hours: 24 };
const text = (node: TestRenderer.ReactTestInstance): string => node.children.map(child => (typeof child === 'string' ? child : text(child))).join('');
const flush = async () => { for (let i = 0; i < 6; i += 1) await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); }); };
const buttons = (r: TestRenderer.ReactTestRenderer, label: string) => r.root.findAllByType('button').filter(item => text(item) === label);
const section = (r: TestRenderer.ReactTestRenderer, label: string) => r.root.findAll(item => item.type === 'section' && item.props['aria-label'] === label);

async function render(role: string) {
  let renderer!: TestRenderer.ReactTestRenderer;
  const queries = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => { renderer = TestRenderer.create(<QueryClientProvider client={queries}><TeamPage user={userWith(role)} /></QueryClientProvider>); });
  await flush();
  return renderer;
}

beforeEach(() => {
  vi.spyOn(client, 'getClinic').mockResolvedValue({ id: 'c', name: 'Clinică', timezone: tz, created_at: '', updated_at: '' });
  vi.spyOn(client, 'listRooms').mockResolvedValue([{ id: 'r1', clinic_id: 'c', name: 'Cabinet 1', is_active: true }]);
  vi.spyOn(team, 'listMembers').mockResolvedValue([member('me', 'doctor'), member('o', 'reception')]);
  vi.spyOn(team, 'myAttendance').mockResolvedValue({ open: null, sessions: [] });
  vi.spyOn(team, 'getSchedule').mockResolvedValue({ timezone: tz, range_start: day.start, range_end: day.end, days: [day], shifts: [shift], leaves: [{ id: 'l1', user_id: 'o', user_name: 'Nume o', starts_on: day.date, ends_on: day.date, user_is_active: true }] });
  vi.spyOn(team, 'listLeave').mockResolvedValue([]);
  vi.spyOn(team, 'listDocuments').mockResolvedValue([]);
  vi.spyOn(team, 'listAttendance').mockResolvedValue([]);
  vi.spyOn(team, 'attendanceAlerts').mockResolvedValue({ review: [], inactive: [] });
});
afterEach(() => { vi.restoreAllMocks(); });

describe('T19 helpers', () => {
  it('computes clinic dates, Mondays and DST-safe day steps', () => {
    expect(clinicDate(tz, Date.parse('2026-11-22T22:30:00Z'))).toBe('2026-11-23');
    expect(mondayOf('2026-11-29')).toBe('2026-11-23');
    expect(addDays('2027-03-27', 1)).toBe('2027-03-28');
    expect(addDays('2027-10-31', 1)).toBe('2027-11-01');
  });
  it('shows the two occurrences of the repeated hour with their real instants', () => {
    expect(formatTime('2027-10-31T00:30:00Z', tz)).toBe('03:30');
    expect(formatTime('2027-10-31T01:30:00Z', tz)).toBe('03:30');
  });
  it('places a shift on every clinic day it touches', () => {
    const night = { ...shift, starts_at: '2026-11-23T20:00:00Z', ends_at: '2026-11-24T04:00:00Z' };
    const next = { date: '2026-11-24', start: day.end, end: '2026-11-24T22:00:00Z', hours: 24 };
    expect(shiftsForDay([night], day)).toHaveLength(1);
    expect(shiftsForDay([night], next)).toHaveLength(1);
    expect(shiftsForDay([{ ...shift, starts_at: day.end, ends_at: next.end }], day)).toHaveLength(0);
  });
  it('labels worked time and an open session', () => {
    expect(workedLabel(3900)).toBe('1 h 05 min');
    expect(workedLabel(null)).toBe('în curs');
  });
});

const session = (extra: Partial<team.Attendance>): team.Attendance => ({ id: 'a9', user_id: 'me', user_name: 'Persoană Fictivă', source: 'clock', clock_in_at: '2026-11-20T07:00:00Z', clock_out_at: null, effective_clock_in_at: '2026-11-20T07:00:00Z', effective_clock_out_at: null, is_open: true, worked_seconds: null, needs_review: false, open_over_limit: false, user_is_active: true, corrections: [], ...extra });

describe('T19 forgotten attendance', () => {
  it('replaces hours with a review label only for flagged sessions', () => {
    expect(attendanceHours(session({ is_open: false, worked_seconds: 259200, needs_review: true }))).toBe('De verificat');
    expect(attendanceHours(session({ open_over_limit: true }))).toBe('De verificat');
    expect(attendanceHours(session({ is_open: false, worked_seconds: 3600 }))).toBe('1 h 00 min');
    expect(attendanceHours(session({}))).toBe('în curs');
  });
  it('tells the employee to close a forgotten session and hides its hours', async () => {
    const forgotten = session({ open_over_limit: true });
    vi.mocked(team.myAttendance).mockResolvedValue({ open: forgotten, sessions: [forgotten] });
    const r = await render('assistant');
    const own = text(section(r, 'Prezența mea')[0]);
    expect(own).toContain('deschis de peste 24 de ore');
    expect(own).toContain('De verificat');
    expect(buttons(r, 'Înregistrează ieșirea')).toHaveLength(1);
    await act(async () => r.unmount());
  });
  it('shows the manager sessions to review, with a correction action', async () => {
    vi.mocked(team.attendanceAlerts).mockResolvedValue({ review: [session({ user_name: 'Nume uitat', open_over_limit: true })], inactive: [] });
    const r = await render('clinic_manager');
    const alerts = section(r, 'Pontaje de verificat');
    expect(alerts).toHaveLength(1);
    expect(text(alerts[0])).toContain('Nume uitat');
    expect(text(alerts[0])).toContain('De verificat');
    expect(alerts[0].findAllByType('button').filter(item => text(item) === 'Corectează')).toHaveLength(1);
    await act(async () => r.unmount());
  });
  it('does not ask a non-manager for the manager alerts', async () => {
    const r = await render('doctor');
    expect(team.attendanceAlerts).not.toHaveBeenCalled();
    expect(section(r, 'Pontaje de verificat')).toHaveLength(0);
    await act(async () => r.unmount());
  });
});

describe('T19 inactive people', () => {
  it('marks an inactive person in the schedule for every role', async () => {
    vi.mocked(team.getSchedule).mockResolvedValue({ timezone: tz, range_start: day.start, range_end: day.end, days: [day], shifts: [{ ...shift, user_id: 'x', user_name: 'Nume plecat', user_is_active: false }], leaves: [{ id: 'l2', user_id: 'y', user_name: 'Nume absent', starts_on: day.date, ends_on: day.date, user_is_active: false }] });
    const r = await render('reception');
    const plan = text(section(r, 'Cine lucrează unde și când')[0]);
    expect(plan).toContain('Nume plecat · inactiv');
    expect(plan).toContain('Nume absent · inactiv');
    await act(async () => r.unmount());
  });
  it('marks an inactive person in the team attendance list', async () => {
    vi.mocked(team.listAttendance).mockResolvedValue([session({ user_name: 'Nume plecat', user_is_active: false })]);
    const r = await render('clinic_manager');
    expect(text(section(r, 'Prezența echipei')[0])).toContain('Nume plecat · inactiv');
    await act(async () => r.unmount());
  });
  it('warns the manager about future shifts and open attendance of inactive people', async () => {
    vi.mocked(team.attendanceAlerts).mockResolvedValue({ review: [], inactive: [{ user_id: 'x', user_name: 'Nume plecat', future_shifts: 2, open_attendance: true }] });
    const r = await render('clinic_manager');
    const warning = text(section(r, 'Persoane inactive')[0]);
    expect(warning).toContain('Nume plecat');
    expect(warning).toContain('2 ture viitoare active');
    expect(warning).toContain('pontaj deschis');
    await act(async () => r.unmount());
  });
});

describe('T19 screen by role', () => {
  it('doctor sees own attendance, schedule, leave and documents but no manager actions', async () => {
    const r = await render('doctor');
    const page = text(r.root);
    expect(page).toContain('Prezența mea');
    expect(page).toContain('Cabinet 1');
    expect(page).toContain('în concediu');
    expect(section(r, 'Repartizare ture')).toHaveLength(0);
    expect(section(r, 'Prezența echipei')).toHaveLength(0);
    expect(buttons(r, 'Încarcă documentul')).toHaveLength(0);
    expect(team.listDocuments).toHaveBeenCalledWith('me');
    await act(async () => r.unmount());
  });
  it('manager sees shift assignment and team attendance', async () => {
    const r = await render('clinic_manager');
    expect(section(r, 'Repartizare ture')).toHaveLength(1);
    expect(section(r, 'Prezența echipei')).toHaveLength(1);
    expect(buttons(r, 'Încarcă documentul')).toHaveLength(1);
    await act(async () => r.unmount());
  });
  it('clock-in calls the API once and then offers clock-out', async () => {
    const open: team.Attendance = { id: 'a1', user_id: 'me', user_name: 'x', source: 'clock', clock_in_at: '2026-11-23T07:00:00Z', clock_out_at: null, effective_clock_in_at: '2026-11-23T07:00:00Z', effective_clock_out_at: null, is_open: true, worked_seconds: null, needs_review: false, open_over_limit: false, user_is_active: true, corrections: [] };
    const clockIn = vi.spyOn(team, 'clockIn').mockResolvedValue(open);
    const r = await render('assistant');
    vi.mocked(team.myAttendance).mockResolvedValue({ open, sessions: [open] });
    await act(async () => { buttons(r, 'Înregistrează intrarea')[0].props.onClick(); });
    await flush();
    expect(clockIn).toHaveBeenCalledTimes(1);
    expect(buttons(r, 'Înregistrează ieșirea')).toHaveLength(1);
    expect(text(r.root)).toContain('Prezent de la 09:00');
    await act(async () => r.unmount());
  });
  it('shows a server refusal for documents instead of an empty list', async () => {
    vi.mocked(team.listDocuments).mockRejectedValue(new Error('Documentele sunt vizibile doar managerului și persoanei în cauză'));
    const r = await render('reception');
    expect(text(section(r, 'Documente administrative')[0])).toContain('doar managerului');
    expect(text(section(r, 'Documente administrative')[0])).not.toContain('Niciun document');
    await act(async () => r.unmount());
  });
});
