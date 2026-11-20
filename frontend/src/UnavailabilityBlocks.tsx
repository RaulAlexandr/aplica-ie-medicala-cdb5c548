import type { Unavailability } from './api/client';

const dayFormat = (timezone: string) => new Intl.DateTimeFormat('en-CA', { timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit' });
const timeFormat = (timezone: string) => new Intl.DateTimeFormat('en-GB', { timeZone: timezone, hour: '2-digit', minute: '2-digit' });
const RESOURCE_LABEL: Record<string, string> = { doctor: 'Doctor', assistant: 'Asistent', room: 'Cabinet' };

/** Blocks that touch the given clinic-local day (inclusive of the last minute before the end instant). */
export function blocksForDay(items: Unavailability[], day: string, timezone: string): Unavailability[] {
  const fmt = dayFormat(timezone);
  return items
    .filter(item => fmt.format(new Date(item.starts_at)) <= day && fmt.format(new Date(new Date(item.ends_at).getTime() - 1)) >= day)
    .sort((a, b) => a.starts_at.localeCompare(b.starts_at));
}

/** Read-only strip on the day agenda: unavailability is managed in Clinic setup, here it only prevents surprises. */
export function UnavailabilityBlocks({ items, day, timezone, names }: { items: Unavailability[]; day: string; timezone: string; names: Record<string, string> }) {
  const blocks = blocksForDay(items, day, timezone);
  if (blocks.length === 0) return null;
  const time = timeFormat(timezone); const date = dayFormat(timezone);
  const stamp = (instant: string) => { const value = new Date(instant); return date.format(value) === day ? time.format(value) : `${date.format(value)} ${time.format(value)}`; };
  return <section className="card calendar-unavailability" aria-label="Indisponibilități în această zi" data-testid="calendar-unavailability">
    <h2>Indisponibilități <span className="muted">· doar citire, se gestionează în Clinic setup</span></h2>
    {blocks.map(item => <p key={item.id} className="calendar-block">
      <strong>{RESOURCE_LABEL[item.resource_type] ?? item.resource_type} · {names[item.resource_id] ?? 'necunoscut'}</strong> · {stamp(item.starts_at)} – {stamp(item.ends_at)}{item.reason ? ` · ${item.reason}` : ''}
    </p>)}
  </section>;
}
