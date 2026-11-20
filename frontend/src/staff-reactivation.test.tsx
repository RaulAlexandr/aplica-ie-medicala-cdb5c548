import TestRenderer, { act } from 'react-test-renderer';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as client from './api/client';
import { StaffDetail } from './App';

const member = (overrides: Partial<client.StaffDetail> = {}): client.StaffDetail => ({
  id: 'staff-1', clinic_id: 'clinic-1', full_name: 'Dr. Fictiv', email: 'fictiv@example.com', role: 'doctor',
  specialization: null, phone: null, is_active: false, deactivated_at: '2030-01-01T10:00:00Z',
  created_at: '2029-12-01T10:00:00Z', updated_at: '2030-01-01T10:00:00Z', upcoming_appointment_count: 0,
  history: [{ action: 'deactivated', occurred_at: '2030-01-01T10:00:00Z', actor_id: 'manager-1', actor_name: 'Manager Fictiv' }],
  ...overrides,
});

function textOf(node: TestRenderer.ReactTestInstance): string {
  return node.children.map(child => (typeof child === 'string' ? child : textOf(child))).join('');
}

function buttons(renderer: TestRenderer.ReactTestRenderer): string[] {
  return renderer.root.findAllByType('button').map(textOf);
}

function button(renderer: TestRenderer.ReactTestRenderer, label: string) {
  const match = renderer.root.findAllByType('button').find(item => textOf(item) === label);
  if (!match) throw new Error(`button ${label} not found; have ${buttons(renderer).join(', ')}`);
  return match;
}

async function render(): Promise<TestRenderer.ReactTestRenderer> {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  let renderer: TestRenderer.ReactTestRenderer;
  await act(async () => {
    renderer = TestRenderer.create(<MemoryRouter><QueryClientProvider client={queryClient}><StaffDetail staffId="staff-1" /></QueryClientProvider></MemoryRouter>);
    await Promise.resolve();
  });
  await flush();
  return renderer!;
}

async function flush() {
  for (let i = 0; i < 5; i += 1) await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
}

describe('staff reactivation UI', () => {
  afterEach(() => vi.restoreAllMocks());

  it('asks for confirmation before reactivating and shows the traceable history', async () => {
    const getStaff = vi.spyOn(client, 'getStaff').mockResolvedValueOnce(member()).mockResolvedValue(member({
      is_active: true,
      history: [
        { action: 'reactivated', occurred_at: '2030-01-02T10:00:00Z', actor_id: 'manager-1', actor_name: 'Manager Fictiv' },
        { action: 'deactivated', occurred_at: '2030-01-01T10:00:00Z', actor_id: 'manager-1', actor_name: 'Manager Fictiv' },
      ],
    }));
    const reactivate = vi.spyOn(client, 'reactivateStaff').mockResolvedValue(member({ is_active: true }));
    const renderer = await render();
    expect(getStaff).toHaveBeenCalledWith('staff-1');
    expect(textOf(renderer.root.findByType('ul'))).toContain('Dezactivat · ');
    expect(textOf(renderer.root.findByType('ul'))).toContain('Manager Fictiv');

    await act(async () => { button(renderer, 'Reactivează').props.onClick(); });
    expect(reactivate).not.toHaveBeenCalled();
    expect(textOf(renderer.root.findByProps({ role: 'group' }))).toContain('se autentifică din nou');

    await act(async () => { button(renderer, 'Renunță').props.onClick(); });
    expect(buttons(renderer)).toEqual(['Reactivează']);
    expect(reactivate).not.toHaveBeenCalled();

    await act(async () => { button(renderer, 'Reactivează').props.onClick(); });
    await act(async () => { button(renderer, 'Confirmă reactivarea').props.onClick(); });
    await flush();
    expect(reactivate).toHaveBeenCalledTimes(1);
    expect(reactivate.mock.calls[0][0]).toBe('staff-1');
    expect(textOf(renderer.root.findByProps({ role: 'status' }))).toContain('a fost reactivat');
    expect(buttons(renderer)).not.toContain('Reactivează');
    expect(textOf(renderer.root.findByType('ul'))).toContain('Reactivat · ');
    renderer.unmount();
  });

  it('offers no reactivation for active or management accounts and reports server refusals', async () => {
    vi.spyOn(client, 'getStaff').mockResolvedValue(member({ is_active: true }));
    let renderer = await render();
    expect(buttons(renderer)).not.toContain('Reactivează');
    renderer.unmount();

    vi.spyOn(client, 'getStaff').mockResolvedValue(member({ role: 'clinic_manager' }));
    renderer = await render();
    expect(buttons(renderer)).not.toContain('Reactivează');
    renderer.unmount();

    vi.spyOn(client, 'getStaff').mockResolvedValue(member());
    vi.spyOn(client, 'reactivateStaff').mockRejectedValue(new Error('Staff member is already active'));
    vi.spyOn(client, 'apiErrorMessage').mockReturnValue('Staff member is already active');
    renderer = await render();
    await act(async () => { button(renderer, 'Reactivează').props.onClick(); });
    await act(async () => { button(renderer, 'Confirmă reactivarea').props.onClick(); });
    await flush();
    expect(textOf(renderer.root.findByProps({ role: 'status' }))).toBe('Staff member is already active');
    renderer.unmount();
  });
});
