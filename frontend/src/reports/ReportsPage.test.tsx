import TestRenderer, { act } from 'react-test-renderer';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as reportApi from './api';
import { ReportsPage } from './ReportsPage';

const result = (indicator: string): reportApi.ReportResult => ({
  indicator, value: indicator === 'consumptions' ? [] : 3,
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
    act(() => { renderer.root.findByProps({ 'aria-label': 'Cabinet' }).props.onChange({ target: { value: 'room-1' } }); });
    const button = renderer.root.findAllByType('button')[0];
    await act(async () => { button.props.onClick(); await flush(); });
    expect(report).toHaveBeenCalledTimes(15);
    expect(text(renderer.root)).toContain('Numără înregistrările sursă.');
    expect(text(renderer.root)).toContain('Evidență internă, nu raport fiscal.');
    const recordsLink = renderer.root.findAllByType('a').find(item => text(item) === 'Vezi înregistrările');
    await act(async () => { recordsLink?.props.onClick({ preventDefault: vi.fn() }); await flush(); });
    expect(getRecords).toHaveBeenCalledWith('appointments_total', expect.any(String), expect.any(String), {});
    expect(text(renderer.root)).toContain('Ana Test');
  });

  it('validates the selected inclusive period before requesting reports', async () => {
    const report = vi.spyOn(reportApi, 'getReport').mockImplementation(async indicator => result(indicator));
    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = TestRenderer.create(<ReportsPage />); });
    act(() => { renderer.root.findByProps({ 'aria-label': 'Cabinet' }).props.onChange({ target: { value: 'room-1' } }); });
    const from = renderer.root.findByProps({ 'aria-label': 'De la' });
    const to = renderer.root.findByProps({ 'aria-label': 'Până la' });
    act(() => {
      from.props.onChange({ target: { value: '2025-01-01' } });
      to.props.onChange({ target: { value: '2026-01-01' } });
    });
    const generate = renderer.root.findAllByType('button').find(button => text(button) === 'Generează raportul')!;
    await act(async () => { await generate.props.onClick(); await flush(); });
    expect(report).toHaveBeenCalledTimes(15); // 366 inclusive calendar days is valid.

    act(() => { to.props.onChange({ target: { value: '2026-01-02' } }); });
    await act(async () => { await generate.props.onClick(); });
    expect(report).toHaveBeenCalledTimes(15);
    expect(renderer.root.findByProps({ role: 'alert' }).children.join('')).toContain('366');

    act(() => {
      from.props.onChange({ target: { value: '2026-10-26' } });
      to.props.onChange({ target: { value: '2026-10-25' } });
    });
    await act(async () => { await generate.props.onClick(); });
    expect(report).toHaveBeenCalledTimes(15);
    expect(renderer.root.findByProps({ role: 'alert' }).children.join('')).toContain('după');
    await act(async () => { renderer.unmount(); });
  });

  it('renders undefined with its reason and links to the card’s exact source records', async () => {
    const report = vi.spyOn(reportApi, 'getReport').mockImplementation(async indicator => indicator === 'room_occupancy'
      ? { ...result(indicator), value: { status: 'undefined', reason: 'Programul nu este configurat.' }, undefined_reason: 'Programul nu este configurat.' }
      : { ...result(indicator), period: { from: '2026-10-25', to: '2026-10-25', timezone: 'Europe/Bucharest', semantics: 'local calendar days, inclusive' }, filters: { doctor_id: 'doctor-1', room_id: 'room-1' }, records_url: `/api/reports/${indicator}/records?from=2026-10-25&to=2026-10-25&doctor_id=doctor-1&room_id=room-1` });
    const getRecords = vi.spyOn(reportApi, 'getReportRecords').mockResolvedValue({
      indicator: 'appointments_total', total: 1, limit: 50, offset: 0,
      records: [{ id: 'a1', entity: 'appointment', date: '2026-10-25T23:59:59+02:00', status: 'completed', amount: null, patient_name: 'Ana Test' }],
    });
    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = TestRenderer.create(<ReportsPage />); });
    act(() => { renderer.root.findByProps({ 'aria-label': 'Cabinet' }).props.onChange({ target: { value: 'room-1' } }); });
    const generate = renderer.root.findAllByType('button').find(button => text(button) === 'Generează raportul')!;
    await act(async () => { await generate.props.onClick(); await flush(); });
    expect(text(renderer.root)).toContain('Nedefinit');
    expect(text(renderer.root)).toContain('Programul nu este configurat.');

    const link = renderer.root.findAllByType('a').find(anchor => text(anchor) === 'Vezi înregistrările')!;
    expect(link.props.href).toContain('/api/reports/appointments_total/records?from=2026-10-25&to=2026-10-25');
    act(() => {
      renderer.root.findByProps({ 'aria-label': 'De la' }).props.onChange({ target: { value: '2024-01-01' } });
      renderer.root.findByProps({ 'aria-label': 'Până la' }).props.onChange({ target: { value: '2024-01-02' } });
    });
    const preventDefault = vi.fn();
    await act(async () => { await link.props.onClick({ preventDefault }); await flush(); });
    expect(preventDefault).toHaveBeenCalledOnce();
    expect(getRecords).toHaveBeenCalledWith('appointments_total', '2026-10-25', '2026-10-25', { doctor_id: 'doctor-1', room_id: 'room-1' });
    expect(text(renderer.root)).toContain('Ana Test');
    expect(report).toHaveBeenCalledTimes(15);
    await act(async () => { renderer.unmount(); });
  });

  it('keeps clinic-wide indicators available and explains room occupancy without a room', async () => {
    const report = vi.spyOn(reportApi, 'getReport').mockImplementation(async indicator => result(indicator));
    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = TestRenderer.create(<ReportsPage />); });
    const generate = renderer.root.findAllByType('button').find(button => text(button) === 'Generează raportul')!;
    await act(async () => { await generate.props.onClick(); });
    expect(report).toHaveBeenCalledTimes(14);
    expect(report).not.toHaveBeenCalledWith('room_occupancy', expect.any(String), expect.any(String), expect.anything());
    expect(text(renderer.root)).toContain('Selectați un cabinet pentru a calcula ocuparea.');
    const occupancyCard = renderer.root.findAllByType('article').find(article => text(article).includes('Ocupare cabinet'))!;
    expect(occupancyCard.findAllByType('a')).toHaveLength(0);
    await act(async () => { renderer.unmount(); });
  });

  it('shows consumption totals by source and reconciles T18 records without hiding filter limits', async () => {
    const report = vi.spyOn(reportApi, 'getReport').mockImplementation(async indicator => indicator === 'consumptions'
      ? { ...result(indicator), filters: { doctor_id: 'doctor-1', room_id: 'room-1' }, value: [
        { item_id: 'item-1', item_name: 'Material fictiv', unit: 'buc', source_type: 't18', quantity: '1.234', estimated_quantity: '0.750', actual_quantity: '1.234' },
        { item_id: 'item-1', item_name: 'Material fictiv', unit: 'buc', source_type: 'direct', quantity: '2.500', estimated_quantity: null, actual_quantity: '2.500' },
      ] }
      : result(indicator));
    const records = vi.spyOn(reportApi, 'getReportRecords').mockResolvedValue({
      indicator: 'consumptions', total: 1, limit: 50, offset: 0, records: [{
        id: 'movement-1', entity: 'inventory_movement', date: '2026-11-20T10:00:00+02:00',
        status: 'Consum confirmat (T18)', amount: '1.234', patient_name: 'Ana Test',
        item_id: 'item-1', item_name: 'Material fictiv', unit: 'buc', lot: 'LOT-FICTIV',
        location: 'Depozit fictiv', quantity: '1.234', movement_kind: 'consumption', source_type: 't18',
        author: 'Manager test', reason: 'Consum fictiv', procedure_name: 'Manoperă fictivă',
        estimated_quantity: '0.750', actual_quantity: '1.234',
      }],
    });
    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = TestRenderer.create(<ReportsPage />); });
    act(() => {
      renderer.root.findByProps({ 'aria-label': 'Medic' }).props.onChange({ target: { value: 'doctor-1' } });
      renderer.root.findByProps({ 'aria-label': 'Cabinet' }).props.onChange({ target: { value: 'room-1' } });
    });
    const generate = renderer.root.findAllByType('button').find(button => text(button) === 'Generează raportul')!;
    await act(async () => { await generate.props.onClick(); await flush(); });
    expect(report).toHaveBeenCalledWith('consumptions', expect.any(String), expect.any(String), { doctor_id: 'doctor-1', room_id: 'room-1' });
    expect(text(renderer.root)).toContain('Consumul direct nu poate fi atribuit');
    expect(text(renderer.root)).not.toContain('Indisponibil până la T17');
    expect(text(renderer.root)).toContain('1.234 buc');
    expect(text(renderer.root)).toContain('Estimat 0.750 · Real 1.234');
    expect(text(renderer.root)).toContain('Consum direct (Stocuri) · 2.500 buc');
    const card = renderer.root.findAllByType('article').find(article => text(article).includes('Consumuri'))!;
    const link = card.findAllByType('a').find(anchor => text(anchor) === 'Vezi înregistrările')!;
    await act(async () => { await link.props.onClick({ preventDefault: vi.fn() }); await flush(); });
    expect(records).toHaveBeenCalledWith('consumptions', expect.any(String), expect.any(String), { doctor_id: 'doctor-1', room_id: 'room-1' });
    expect(text(card)).toContain('LOT-FICTIV');
    expect(text(card)).toContain('Depozit fictiv');
    expect(text(card)).toContain('Manager test');
    expect(text(card)).toContain('Manoperă fictivă');
    expect(text(card)).toContain('Ana Test');
    expect(text(card)).toContain('Consum fictiv');
    await act(async () => { renderer.unmount(); });
  });
});
