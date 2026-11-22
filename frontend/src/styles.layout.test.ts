// Guards for shared styles (jsdom cannot compute layout; the real 390 px / 1280 px proof is t18-browser-proof.mjs).
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const css = readFileSync(new URL('./styles.css', import.meta.url), 'utf8');
const rule = (selector: string) => {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = css.match(new RegExp(`(?:^|\\})\\s*${escaped}\\s*\\{([^}]*)\\}`, 'm'));
  return match ? match[1] : '';
};

describe('shared form controls on narrow screens', () => {
  it('lets selects, inputs and textareas shrink to their container (a long option must not widen the page)', () => {
    expect(rule('input,textarea,select')).toMatch(/max-width:\s*100%/);
  });
});
