import axios, { AxiosError, InternalAxiosRequestConfig } from 'axios';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, clearAuthenticatedSession, configureSessionHandlers, logout } from './client';

const values = new Map<string, string>();
const storage = {
  getItem: (key: string) => values.get(key) ?? null,
  setItem: (key: string, value: string) => values.set(key, value),
  removeItem: (key: string) => values.delete(key),
  clear: () => values.clear(),
};
Object.defineProperty(globalThis, 'localStorage', { value: storage, configurable: true });
Object.defineProperty(globalThis, 'window', { value: { dispatchEvent: vi.fn() }, configurable: true });

function unauthorized(config: InternalAxiosRequestConfig): AxiosError {
  return new AxiosError('expired', 'ERR_BAD_REQUEST', config, undefined, { status: 401, statusText: 'Unauthorized', headers: {}, config, data: { detail: 'expired' } });
}

function installAdapter(responses: Array<{ status: number; data: unknown }>): void {
  api.defaults.adapter = vi.fn(async (config) => {
    const response = responses.shift();
    if (!response) throw new Error('Unexpected request');
    if (response.status === 401) throw unauthorized(config);
    return { ...response, statusText: 'OK', headers: {}, config, request: {} };
  });
}

afterEach(() => {
  values.clear();
  vi.restoreAllMocks();
  configureSessionHandlers({});
});

describe('authenticated session lifecycle', () => {
  it('refreshes an expired access token for auth/me and retries the request', async () => {
    values.set('access_token', 'expired-access');
    values.set('refresh_token', 'valid-refresh');
    installAdapter([{ status: 401, data: { detail: 'expired' } }, { status: 200, data: { id: 'user-1' } }]);
    vi.spyOn(axios, 'post').mockResolvedValue({ data: { access_token: 'new-access', refresh_token: 'rotated-refresh' } } as never);

    await expect(api.get('/auth/me')).resolves.toMatchObject({ data: { id: 'user-1' } });
    expect(values.get('access_token')).toBe('new-access');
    expect(values.get('refresh_token')).toBe('rotated-refresh');
  });

  it('clears tokens and invokes the cache/session handler when refresh is rejected', async () => {
    values.set('access_token', 'expired-access');
    values.set('refresh_token', 'rejected-refresh');
    const onSessionCleared = vi.fn();
    configureSessionHandlers({ onSessionCleared });
    installAdapter([{ status: 401, data: { detail: 'expired' } }]);
    vi.spyOn(axios, 'post').mockRejectedValue(new Error('refresh rejected'));

    await expect(api.get('/patients')).rejects.toThrow('refresh rejected');
    expect(values.get('access_token')).toBeUndefined();
    expect(values.get('refresh_token')).toBeUndefined();
    expect(onSessionCleared).toHaveBeenCalledOnce();
  });

  it('clears the session when the refreshed request is still unauthorized', async () => {
    values.set('access_token', 'expired-access');
    values.set('refresh_token', 'valid-refresh');
    const onSessionCleared = vi.fn();
    configureSessionHandlers({ onSessionCleared });
    installAdapter([{ status: 401, data: { detail: 'expired' } }, { status: 401, data: { detail: 'revoked' } }]);
    const refresh = vi.spyOn(axios, 'post').mockResolvedValue({ data: { access_token: 'new-access', refresh_token: 'rotated-refresh' } } as never);

    await expect(api.get('/patients')).rejects.toMatchObject({ response: { status: 401 } });
    expect(refresh).toHaveBeenCalledOnce();
    expect(values.get('access_token')).toBeUndefined();
    expect(values.get('refresh_token')).toBeUndefined();
    expect(onSessionCleared).toHaveBeenCalledOnce();
  });

  it('does not restore tokens when logout ends an in-flight refresh', async () => {
    values.set('access_token', 'expired-access');
    values.set('refresh_token', 'old-refresh');
    installAdapter([{ status: 401, data: { detail: 'expired' } }]);
    let resolveRefresh!: (value: { data: { access_token: string; refresh_token: string } }) => void;
    const refreshPending = new Promise<{ data: { access_token: string; refresh_token: string } }>((resolve) => { resolveRefresh = resolve; });
    vi.spyOn(axios, 'post').mockImplementation((url) => url.includes('/auth/refresh') ? refreshPending as never : Promise.resolve({ data: {} }) as never);

    const pendingRequest = api.get('/patients');
    await Promise.resolve();
    await logout();
    resolveRefresh({ data: { access_token: 'late-access', refresh_token: 'late-refresh' } });

    await expect(pendingRequest).rejects.toBeTruthy();
    expect(values.get('access_token')).toBeUndefined();
    expect(values.get('refresh_token')).toBeUndefined();
  });

  it('clears the session synchronously when explicitly requested', () => {
    values.set('access_token', 'access');
    values.set('refresh_token', 'refresh');
    clearAuthenticatedSession();
    expect(values.size).toBe(0);
  });
});
