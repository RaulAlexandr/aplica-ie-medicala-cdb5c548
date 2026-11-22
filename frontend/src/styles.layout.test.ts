// Guards for shared styles (jsdom cannot compute layout; the real 390 px / 1280 px proof is t18-browser-proof.mjs).
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const css = readFileSync(new URL('./styles.css', import.meta.url), 'utf8');
// Declarations of every rule whose selector list is exactly `selector`, or contains it when `selector` is a single selector.
const bodies = (selector: string): string[] => {
  const found: string[] = [];
  for (const match of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const list = match[1].split(',').map(part => part.trim());
    if (list.join(',') === selector || (!selector.includes(',') && list.includes(selector))) found.push(match[2]);
  }
  return found;
};
const colorOf = (selector: string): string => {
  for (const body of bodies(selector)) {
    const color = body.match(/(?:^|;)\s*color:\s*(#[0-9a-fA-F]{6})/);
    if (color) return color[1];
  }
  return '';
};

const luminance = (hex: string) => {
  const channel = (offset: number) => { const v = parseInt(hex.slice(offset, offset + 2), 16) / 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
  return 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5);
};
const contrast = (a: string, b: string) => { const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x); return (hi + 0.05) / (lo + 0.05); };

describe('shared form controls on narrow screens', () => {
  it('lets selects, inputs and textareas shrink to their container (a long option must not widen the page)', () => {
    expect(bodies('input,textarea,select').some(body => /max-width:\s*100%/.test(body))).toBe(true);
  });
});

describe('shared text colours keep at least 4.5:1 contrast on their backgrounds (accessibility)', () => {
  it.each([['page', '#f5f7f9'], ['card', '#ffffff']])('.eyebrow on the %s background %s', (_name, background) => {
    expect(colorOf('.eyebrow')).not.toBe('');
    expect(contrast(colorOf('.eyebrow'), background)).toBeGreaterThanOrEqual(4.5);
  });
  it.each([['page', '#f5f7f9'], ['card', '#ffffff']])('.muted on the %s background %s', (_name, background) => {
    expect(colorOf('.muted')).not.toBe('');
    expect(contrast(colorOf('.muted'), background)).toBeGreaterThanOrEqual(4.5);
  });
  it('the eyebrow label in the dark sidebar (aside .eyebrow) on #12343b', () => {
    expect(colorOf('aside .eyebrow')).not.toBe('');
    expect(contrast(colorOf('aside .eyebrow'), '#12343b')).toBeGreaterThanOrEqual(4.5);
  });
});
