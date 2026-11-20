import TestRenderer, { act } from 'react-test-renderer';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as client from './api/client';
import { Home } from './App';
import { clinicHour, greetingForHour } from './timezone';

const user: client.User = { id: 'u1', clinic_id: '4b8238d5-d1f4-430b-89b6-14fc7b50e8c2', full_name: 'Radu Recepție Demo', email: 'r@example.com', role: 'reception' };
const clinic: client.Clinic = { id: user.clinic_id, name: 'Clinica Demo Fictivă', timezone: 'Europe/Bucharest', created_at: '', updated_at: '' };

function textOf(node: TestRenderer.ReactTestInstance): string {
  return node.children.map(child => (typeof child === 'string' ? child : textOf(child))).join('');
}
async function renderHome() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  let renderer!: TestRenderer.ReactTestRenderer;
  await act(async () => {
    renderer = TestRenderer.create(<MemoryRouter><QueryClientProvider client={queryClient}><Home user={user} /></QueryClientProvider></MemoryRouter>);
  });
  for (let i = 0; i < 5; i += 1) await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
  const text = textOf(renderer.root);
  renderer.unmount();
  return text;
}

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

describe('Overview greeting and clinic name', () => {
  function mockApi() {
    vi.spyOn(client, 'countPatients').mockResolvedValue(3);
    vi.spyOn(client, 'listAppointments').mockResolvedValue([]);
    vi.spyOn(client, 'getClinic').mockResolvedValue(clinic);
  }

  it('greets by the clinic wall clock, not the browser clock, and shows the clinic name', async () => {
    mockApi();
    // 13:30 UTC = 15:30 in Bucharest (EET, UTC+2).
    vi.useFakeTimers({ toFake: ['Date'], now: new Date('2026-11-20T13:30:00Z') });
    const text = await renderHome();
    expect(text).toContain('Good afternoon, Radu');
    expect(text).not.toContain('Good morning');
    expect(text).toContain('Clinica Demo Fictivă');
    expect(text).not.toContain('4b8238d5');
  });

  it('uses the clinic day boundary: 22:30 UTC is already 00:30 (night) in Bucharest', async () => {
    mockApi();
    vi.useFakeTimers({ toFake: ['Date'], now: new Date('2026-11-20T22:30:00Z') });
    expect(await renderHome()).toContain('Good evening, Radu');
  });

  it('maps hours to greetings', () => {
    expect([4, 5, 11, 12, 17, 18, 23].map(greetingForHour)).toEqual(
      ['Good evening', 'Good morning', 'Good morning', 'Good afternoon', 'Good afternoon', 'Good evening', 'Good evening'],
    );
    expect(clinicHour('Europe/Bucharest', Date.parse('2026-11-20T13:30:00Z'))).toBe(15);
  });
});
