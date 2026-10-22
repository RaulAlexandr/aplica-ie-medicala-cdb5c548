const formatterCache = new Map<string, Intl.DateTimeFormat>();

function formatter(timezone: string): Intl.DateTimeFormat {
  let value = formatterCache.get(timezone);
  if (!value) {
    value = new Intl.DateTimeFormat('en-CA', {
      timeZone: timezone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hourCycle: 'h23',
    });
    formatterCache.set(timezone, value);
  }
  return value;
}

function wallParts(instant: number, timezone: string): string {
  const parts = formatter(timezone).formatToParts(new Date(instant));
  const value = (type: string) => parts.find((part) => part.type === type)?.value;
  return `${value('year')}-${value('month')}-${value('day')}T${value('hour')}:${value('minute')}:${value('second')}`;
}

export function clinicLocalToUtc(value: string, timezone: string, fold?: 0 | 1): string {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?$/.test(value)) {
    throw new Error('Enter a valid clinic-local date and time.');
  }
  const normalized = value.length === 16 ? `${value}:00` : value;
  const wall = Date.parse(`${normalized}Z`);
  if (!Number.isFinite(wall)) throw new Error('Enter a valid clinic-local date and time.');

  const offsets = new Set<number>();
  for (const delta of [-172800000, -86400000, 0, 86400000, 172800000]) {
    const sample = wall + delta;
    const represented = Date.parse(`${wallParts(sample, timezone)}Z`);
    offsets.add(represented - sample);
  }
  const candidates = [...offsets]
    .map((offset) => wall - offset)
    .filter((candidate) => wallParts(candidate, timezone) === normalized)
    .sort((left, right) => left - right);
  if (candidates.length === 0) {
    throw new Error('This local time does not exist because of the daylight-saving transition.');
  }
  if (candidates.length > 1 && fold === undefined) {
    throw new Error('This local time is ambiguous; select which occurrence you mean.');
  }
  // A supplied second-occurrence choice has no effect when the time occurs once.
  const selected = candidates.length === 1 ? candidates[0] : candidates[fold ?? 0];
  return new Date(selected).toISOString();
}
