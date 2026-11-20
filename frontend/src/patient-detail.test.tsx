import TestRenderer, { act } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { AxiosError, AxiosHeaders } from 'axios';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as client from './api/client';
import { PatientDetail } from './App';
import indexHtml from '../index.html?raw';
import faviconSvg from '../public/favicon.svg?raw';

const user = { id: 'u1', role: 'doctor' } as unknown as client.User;

function httpError(status: number, detail: string) {
  const config = { headers: new AxiosHeaders() };
  return new AxiosError('failed', 'ERR_BAD_REQUEST', config, undefined, { status, statusText: String(status), headers: {}, config, data: { detail } });
}

async function render(id: string, settleMs = 50) {
  const queries = new QueryClient();
  let renderer!: TestRenderer.ReactTestRenderer;
  await act(async () => {
    renderer = TestRenderer.create(<QueryClientProvider client={queries}><MemoryRouter><PatientDetail user={user} id={id} /></MemoryRouter></QueryClientProvider>);
  });
  // Allow the query promise to settle (and any retry timer to fire if retries were wrongly enabled).
  await act(async () => { await vi.advanceTimersByTimeAsync(settleMs); });
  return JSON.stringify(renderer.toJSON());
}

describe('patient detail page for missing or foreign ids', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

  it.each([[404, 'Patient not found'], [403, 'Forbidden'], [422, 'value is not a valid uuid']])('shows one clear message for HTTP %i without leaking the backend detail', async (status, detail) => {
    const spy = vi.spyOn(client, 'getPatient').mockRejectedValue(httpError(status, detail));
    const html = await render('00000000-0000-0000-0000-000000000000');
    expect(html).toContain('Pacient inexistent sau fără acces');
    expect(html).not.toContain('Loading patient');
    expect(html).not.toContain(detail);
    expect(html).not.toContain('\\u2026');
    expect(spy).toHaveBeenCalledTimes(1); // a 4xx is final: no retry
  });

  it('keeps the generic server message for a non-client failure', async () => {
    vi.spyOn(client, 'getPatient').mockRejectedValue(new Error('network down'));
    const html = await render('x', 5000); // transient failures still retry (twice, with backoff) before surfacing
    expect(html).toContain('network down');
    expect(html).not.toContain('Pacient inexistent');
  });
});

describe('favicon', () => {
  it('is linked from index.html and shipped from public/', () => {
    expect(indexHtml).toMatch(/<link rel="icon"[^>]*href="\/favicon\.svg"/);
    expect(faviconSvg).toContain('<svg');
  });
});
