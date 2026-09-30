import TestRenderer, { act } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as client from './api/client';
import { InvitationLinkNotice, StaffOnboarding } from './App';

type Invitation = client.Invitation;

const invitation = (overrides: Partial<Invitation> = {}): Invitation => ({
  id: 'invitation-1',
  email: 'doctor@example.com',
  full_name: 'Test Doctor',
  role: 'doctor',
  expires_at: '2030-01-01T00:00:00Z',
  revoked_at: null,
  accepted_at: null,
  created_at: '2029-12-29T00:00:00Z',
  invitation_url: '/staff/invitations/accept?token=created-secret',
  ...overrides,
});

function textOf(node: TestRenderer.ReactTestInstance): string {
  return node.children.filter((child): child is string => typeof child === 'string').join('');
}

function button(renderer: TestRenderer.ReactTestRenderer, label: string): TestRenderer.ReactTestInstance {
  const match = renderer.root.findAllByType('button').find(item => textOf(item) === label);
  if (!match) throw new Error(`button ${label} not found`);
  return match;
}

describe('invitation link delivery UI', () => {
  afterEach(() => vi.restoreAllMocks());

  it('renders a read-only returned link and copy control', () => {
    const html = TestRenderer.create(<InvitationLinkNotice link="https://clinic.example/staff/invitations/accept?token=raw-secret" onCopy={vi.fn()} />).toJSON();
    expect(JSON.stringify(html)).toContain('https://clinic.example/staff/invitations/accept?token=raw-secret');
    expect(JSON.stringify(html)).toContain('Copy link');
  });

  it('shows create and reissue URLs and supports clipboard fallback', async () => {
    Object.defineProperty(globalThis, 'window', { configurable: true, value: { location: { origin: 'https://clinic.example' } } });
    const writeText = vi.fn().mockRejectedValue(new Error('clipboard unavailable'));
    Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { clipboard: { writeText } } });
    const created = invitation();
    const reissued = invitation({ id: 'invitation-2', invitation_url: '/staff/invitations/accept?token=reissued-secret' });
    vi.spyOn(client, 'listStaff').mockResolvedValue([]);
    vi.spyOn(client, 'listInvitations').mockResolvedValue([invitation()]);
    const createInvitation = vi.spyOn(client, 'createInvitation').mockResolvedValue(created);
    const reissueInvitation = vi.spyOn(client, 'reissueInvitation').mockResolvedValue(reissued);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    let renderer: TestRenderer.ReactTestRenderer;

    await act(async () => {
      renderer = TestRenderer.create(<QueryClientProvider client={queryClient}><StaffOnboarding /></QueryClientProvider>);
      await Promise.resolve();
    });
    const inputs = renderer!.root.findAllByType('input');
    await act(async () => {
      inputs[0].props.onChange({ target: { value: 'Test Doctor' } });
    });
    await act(async () => {
      renderer!.root.findAllByType('input')[1].props.onChange({ target: { value: 'doctor@example.com' } });
    });
    const form = renderer!.root.findAllByType('form')[0];
    await act(async () => {
      await form.props.onSubmit({ preventDefault: () => undefined });
      await Promise.resolve();
    });
    expect(createInvitation).toHaveBeenCalledWith({ full_name: 'Test Doctor', email: 'doctor@example.com', role: 'doctor' });
    expect(renderer!.root.findAllByType('input').some(input => input.props.value === 'https://clinic.example/staff/invitations/accept?token=created-secret')).toBe(true);

    await act(async () => {
      await button(renderer!, 'Reissue').props.onClick();
      await Promise.resolve();
    });
    expect(reissueInvitation).toHaveBeenCalledWith('invitation-1', expect.anything());
    expect(renderer!.root.findAllByType('input').some(input => input.props.value === 'https://clinic.example/staff/invitations/accept?token=reissued-secret')).toBe(true);

    await act(async () => {
      await button(renderer!, 'Copy link').props.onClick();
      await Promise.resolve();
    });
    expect(writeText).toHaveBeenCalledWith('https://clinic.example/staff/invitations/accept?token=reissued-secret');
    expect(renderer!.root.findAllByProps({ role: 'alert' }).some(node => textOf(node).includes('Clipboard unavailable. Select and copy this link manually'))).toBe(true);
    renderer!.unmount();
  });
});
