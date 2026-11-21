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

const validation = (...items: Array<Record<string, unknown>>) => withResponse(422, { detail: items });

describe('apiErrorMessage: Pydantic validation details (D3)', () => {
  it('strips the "Value error, " prefix and keeps the Romanian text written by the server', () => {
    expect(apiErrorMessage(validation({ type: 'value_error', loc: ['body', 'reason'], msg: 'Value error, Motivul este obligatoriu', ctx: {} }))).toBe('Motivul este obligatoriu');
  });
  it('leaves a Romanian message without the prefix unchanged', () => {
    expect(apiErrorMessage(validation({ type: 'value_error', loc: ['body'], msg: 'Alege cabinetul sau serviciul, nu ambele' }))).toBe('Alege cabinetul sau serviciul, nu ambele');
  });
  it.each([
    [{ type: 'string_too_long', msg: 'String should have at most 160 characters', ctx: { max_length: 160 } }, 'Textul este prea lung (maximum 160 de caractere).'],
    [{ type: 'string_too_short', msg: 'String should have at least 3 characters', ctx: { min_length: 3 } }, 'Textul este prea scurt (minimum 3 caractere).'],
    [{ type: 'string_too_short', msg: 'String should have at least 1 character', ctx: { min_length: 1 } }, 'Câmpul nu poate fi gol.'],
    [{ type: 'missing', msg: 'Field required' }, 'Câmpul este obligatoriu.'],
    [{ type: 'greater_than', msg: 'Input should be greater than 0', ctx: { gt: 0 } }, 'Valoarea trebuie să fie mai mare decât 0.'],
    [{ type: 'greater_than_equal', msg: 'Input should be greater than or equal to 0', ctx: { ge: 0 } }, 'Valoarea trebuie să fie cel puțin 0.'],
    [{ type: 'less_than_equal', msg: 'Input should be less than or equal to 20', ctx: { le: 20 } }, 'Valoarea trebuie să fie cel mult 20.'],
    [{ type: 'decimal_max_digits', msg: 'Decimal input should have no more than 14 digits in total', ctx: { max_digits: 14 } }, 'Numărul are prea multe cifre (maximum 14).'],
    [{ type: 'decimal_max_places', msg: 'Decimal input should have no more than 3 decimal places', ctx: { decimal_places: 3 } }, 'Numărul are prea multe zecimale (maximum 3).'],
    [{ type: 'uuid_parsing', msg: 'Input should be a valid UUID' }, 'Identificatorul nu este valid.'],
  ])('translates %j', (item, expected) => {
    expect(apiErrorMessage(validation({ loc: ['body', 'field'], ...item }))).toBe(expected);
  });
  it('shows a generic Romanian text for an unknown type, never the English one', () => {
    expect(apiErrorMessage(validation({ type: 'int_parsing', loc: ['body', 'n'], msg: 'Input should be a valid integer' }))).toBe('Datele introduse nu sunt valide.');
  });
  it('falls back to the generic text when ctx is missing a limit', () => {
    expect(apiErrorMessage(validation({ type: 'string_too_long', loc: ['body'], msg: 'String should have at most 5 characters' }))).toBe('Textul este prea lung.');
  });
  it('joins several problems and drops duplicates', () => {
    expect(apiErrorMessage(validation(
      { type: 'missing', loc: ['body', 'a'], msg: 'Field required' },
      { type: 'missing', loc: ['body', 'b'], msg: 'Field required' },
      { type: 'less_than_equal', loc: ['body', 'c'], msg: 'x', ctx: { le: 20 } },
    ))).toBe('Câmpul este obligatoriu. Valoarea trebuie să fie cel mult 20.');
  });
  it('keeps a plain string detail untouched', () => {
    expect(apiErrorMessage(withResponse(409, { detail: 'Lot expirat; consumul este interzis' }))).toBe('Lot expirat; consumul este interzis');
  });
});
