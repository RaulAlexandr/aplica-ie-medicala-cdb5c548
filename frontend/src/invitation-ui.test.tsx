import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { InvitationLinkNotice } from './App';

describe('invitation link delivery UI', () => {
  it('renders the transient returned link and copy control', () => {
    const html = renderToStaticMarkup(<InvitationLinkNotice link="https://clinic.example/staff/invitations/accept?token=raw-secret" onCopy={vi.fn()} />);
    expect(html).toContain('https://clinic.example/staff/invitations/accept?token=raw-secret');
    expect(html).toContain('Copy link');
    expect(html).toContain('readonly');
  });
});
