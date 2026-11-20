import TestRenderer, { act } from 'react-test-renderer';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as reportApi from './api';
import { ReportsPage } from './ReportsPage';

const result = (indicator: string): reportApi.ReportResult => ({
  indicator, value: indicator === 'consumptions' ? { status: 'unavailable', reason: 'T17' } : 3,
  unit: 'înregistrări', definition: 'Numără înregistrările sursă.',
  period: { from: '2026-11-20', to: '2026-11-20', timezone: 'Europe/Bucharest', semantics: 'local calendar days, inclusive' },
  filters: { doctor_id: null, room_id: null }, source: 'Appointment', undefined_reason: null,
  records_url: `/api/reports/${indicator}/records`,
});
const text = (node: TestRenderer.ReactTestInstance): string => node.children.map(child => typeof child === 'string' ? child : text(child)).join('');
const flush = () => new Promise(resolve => setTimeout(resolve, 0));

afterEach(() => vi.restoreAllMocks());

describe('ReportsPage', () => {
  it('loads period definitions and values, then reveals source records', async () => {
    const report = vi.spyOn(reportApi, 'getReport').mockImplementation(async indicator => result(indicator));
    const getRecords = vi.spyOn(reportApi, 'getReportRecords').mockResolvedValue({
      indicator: 'appointments_total', total: 1, limit: 50, offset: 0,
      records: [{ id: 'a1', entity: 'appointment', date: '2026-11-20T23:30:00+02:00', status: 'completed', amount: null, patient_name: 'Ana Test' }],
    });
    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = TestRenderer.create(<ReportsPage />); });
    const button = renderer.root.findAllByType('button')[0];
    await act(async () => { button.props.onClick(); await flush(); });
    expect(report).toHaveBeenCalledTimes(15);
    expect(text(renderer.root)).toContain('Numără înregistrările sursă.');
    expect(text(renderer.root)).toContain('Evidență internă, nu raport fiscal.');
    const recordsButton = renderer.root.findAllByType('button').find(item => text(item) === 'Vezi înregistrările');
    await act(async () => { recordsButton?.props.onClick(); await flush(); });
    expect(getRecords).toHaveBeenCalledWith('appointments_total', expect.any(String), expect.any(String), {});
    expect(text(renderer.root)).toContain('Ana Test');
  });
});
