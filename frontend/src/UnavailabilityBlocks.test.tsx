import TestRenderer, { act } from 'react-test-renderer';
import { describe, expect, it } from 'vitest';
import type { Unavailability } from './api/client';
import { blocksForDay, UnavailabilityBlocks } from './UnavailabilityBlocks';

const TZ = 'Europe/Bucharest';
const block = (id: string, starts_at: string, ends_at: string, extra: Partial<Unavailability> = {}): Unavailability => ({
  id, resource_type: 'doctor', resource_id: 'doctor-1', starts_at, ends_at, reason: 'Concediu', created_at: '2026-11-01T00:00:00Z', ...extra,
});
function text(node: TestRenderer.ReactTestInstance): string { return node.children.map(child => (typeof child === 'string' ? child : text(child))).join(''); }

describe('S3: doctor/room/assistant unavailability on the day agenda', () => {
  it('selects blocks by the clinic-local day, including blocks that cross midnight or end exactly at midnight', () => {
    const items = [
      block('in-day', '2026-11-21T09:00:00Z', '2026-11-21T10:00:00Z'),
      block('crosses-midnight', '2026-11-20T21:00:00Z', '2026-11-21T02:00:00Z'),
      block('ends-at-midnight', '2026-11-20T10:00:00Z', '2026-11-20T22:00:00Z'),
      block('other-day', '2026-11-23T09:00:00Z', '2026-11-23T10:00:00Z'),
    ];
    // 2026-11-21 in Bucharest = 2026-11-20T22:00Z .. 2026-11-21T22:00Z
    expect(blocksForDay(items, '2026-11-21', TZ).map(item => item.id)).toEqual(['crosses-midnight', 'in-day']);
    expect(blocksForDay(items, '2026-11-20', TZ).map(item => item.id)).toEqual(['ends-at-midnight', 'crosses-midnight']);
  });

  it('renders a distinct read-only block with the resource name, clinic-local times and reason', () => {
    let renderer!: TestRenderer.ReactTestRenderer;
    act(() => {
      renderer = TestRenderer.create(<UnavailabilityBlocks items={[block('b', '2026-11-21T09:00:00Z', '2026-11-21T10:00:00Z')]} day="2026-11-21" timezone={TZ} names={{ 'doctor-1': 'Dr. Alex Demo' }} />);
    });
    const content = text(renderer.root);
    expect(content).toContain('Doctor · Dr. Alex Demo');
    expect(content).toContain('11:00 – 12:00');
    expect(content).toContain('Concediu');
    expect(renderer.root.findAllByType('button')).toHaveLength(0);
    expect(renderer.root.findAllByProps({ 'data-testid': 'calendar-unavailability' }).length).toBeGreaterThan(0);
  });

  it('renders nothing when the day has no blocks', () => {
    let renderer!: TestRenderer.ReactTestRenderer;
    act(() => { renderer = TestRenderer.create(<UnavailabilityBlocks items={[block('b', '2026-11-23T09:00:00Z', '2026-11-23T10:00:00Z')]} day="2026-11-21" timezone={TZ} names={{}} />); });
    expect(renderer.toJSON()).toBeNull();
  });
});
