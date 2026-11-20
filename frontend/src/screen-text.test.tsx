import TestRenderer, { act } from 'react-test-renderer';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as client from './api/client';
import { ClinicSettings, ProcedureDetail, Procedures, Rooms, StaffOnboarding } from './App';

// Catalog, setup and staff screens used to render the literal text "·" / "…" in JSX.
const manager: client.User = { id: 'manager-1', clinic_id: 'clinic-1', full_name: 'Manager Fictiv', email: 'manager@example.com', role: 'clinic_manager' };
const never = <T,>() => new Promise<T>(() => undefined);

function textOf(node: TestRenderer.ReactTestInstance): string {
  return node.children.map(child => (typeof child === 'string' ? child : textOf(child))).join('');
}
async function render(element: JSX.Element, path = '/') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  let renderer!: TestRenderer.ReactTestRenderer;
  await act(async () => {
    renderer = TestRenderer.create(<MemoryRouter initialEntries={[path]}><QueryClientProvider client={queryClient}>{element}</QueryClientProvider></MemoryRouter>);
  });
  for (let i = 0; i < 5; i += 1) await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
  const text = textOf(renderer.root);
  renderer.unmount();
  return text;
}
function expectRealCharacters(text: string) {
  expect(text).not.toMatch(/\\u[0-9a-fA-F]{4}/);
}

describe('catalog, setup and staff screens render real characters', () => {
  afterEach(() => vi.restoreAllMocks());

  it('catalog list and loading states', async () => {
    vi.spyOn(client, 'listProcedures').mockResolvedValue([{ id: 'c-1', clinic_id: 'clinic-1', code: 'OBT', name: 'Obturație', description: null, category: 'Restaurare', default_duration_minutes: 30, base_price: '250.00', currency: 'RON', allowed_location: 'tooth_surface', is_active: true, created_at: '', updated_at: '' }]);
    vi.spyOn(client, 'countProcedures').mockResolvedValue(1);
    const list = await render(<Procedures user={manager} />);
    expect(list).toContain('Restaurare · 30 min · 250.00 RON · Active · dinte + suprafață');
    expectRealCharacters(list);

    vi.spyOn(client, 'listProcedures').mockImplementation(never);
    vi.spyOn(client, 'countProcedures').mockImplementation(never);
    const loading = await render(<Procedures user={manager} />);
    expect(loading).toContain('Loading…');
    expectRealCharacters(loading);

    vi.spyOn(client, 'getProcedure').mockImplementation(never);
    const detail = await render(<Routes><Route path="/procedures/:id" element={<ProcedureDetail user={manager} />} /></Routes>, '/procedures/c-1');
    expect(detail).toContain('Loading procedure…');
    expectRealCharacters(detail);
  });

  it('setup screens', async () => {
    vi.spyOn(client, 'getClinic').mockImplementation(never);
    const settings = await render(<ClinicSettings />);
    expect(settings).toContain('Loading clinic settings…');
    expectRealCharacters(settings);

    vi.spyOn(client, 'listRooms').mockResolvedValue([{ id: 'r-1', clinic_id: 'clinic-1', name: 'Cabinet 1', is_active: true }]);
    const rooms = await render(<Rooms />);
    expect(rooms).toContain('Cabinet 1 · ');
    expectRealCharacters(rooms);
  });

  it('staff list and invitations', async () => {
    vi.spyOn(client, 'listStaff').mockResolvedValue([{ id: 's-1', clinic_id: 'clinic-1', full_name: 'Dr. Fictiv', email: 'dr@example.com', role: 'doctor', specialization: null, phone: null, is_active: true, deactivated_at: null, created_at: '', updated_at: '' }]);
    vi.spyOn(client, 'listInvitations').mockResolvedValue([{ id: 'i-1', email: 'asist@example.com', full_name: 'Asistent Fictiv', role: 'assistant', expires_at: '2030-01-01T00:00:00Z', revoked_at: null, accepted_at: null, created_at: '2029-12-30T00:00:00Z', invitation_url: null }]);
    const staff = await render(<StaffOnboarding />);
    expect(staff).toContain('dr@example.com · doctor · Active');
    expect(staff).toContain('Asistent Fictiv · assistant');
    expectRealCharacters(staff);
  });
});
