import { AxiosError, type InternalAxiosRequestConfig } from 'axios';
import { describe, expect, it } from 'vitest';
import { apiErrorMessage } from './client';

const config = { headers: {} } as InternalAxiosRequestConfig;
const withResponse = (status: number, data: unknown) => new AxiosError('failed', 'ERR_BAD_REQUEST', config, undefined, { status, statusText: '', headers: {}, config, data });

describe('apiErrorMessage: request without a server response (D2)', () => {
  const offline = 'Nu s-a putut contacta serverul. Verifică conexiunea și încearcă din nou.';
  it('replaces "Network Error" with a Romanian message', () => {
    expect(apiErrorMessage(new AxiosError('Network Error', 'ERR_NETWORK', config))).toBe(offline);
  });
  it('does the same for a timeout without a response', () => {
    expect(apiErrorMessage(new AxiosError('timeout of 5000ms exceeded', 'ECONNABORTED', config))).toBe(offline);
  });
  it('keeps the server text when the server did answer', () => {
    expect(apiErrorMessage(withResponse(409, { detail: 'Stoc insuficient' }))).toBe('Stoc insuficient');
  });
  it('keeps the message of a non-HTTP error raised by the app itself', () => {
    expect(apiErrorMessage(new Error('Ora introdusă nu este validă.'))).toBe('Ora introdusă nu este validă.');
  });
});
