import { useState } from 'react';
import { apiErrorMessage } from '../api/client';
import { getReport, getReportRecords, ReportResult } from './api';
import './reports.css';

const indicators = [
  'appointments_total', 'appointments_cancelled', 'appointments_no_show', 'appointments_completed',
  'cancellation_rate', 'no_show_rate', 'room_occupancy', 'procedure_estimated_duration',
  'procedure_measured_duration', 'procedure_manual_duration', 'treatment_items_uncontinued',
  'services_performed', 'receipts_net_by_method', 'financial_reversals', 'consumptions',
];
const labels: Record<string, string> = {
  appointments_total: 'Programări', appointments_cancelled: 'Anulări', appointments_no_show: 'Neprezentări',
  appointments_completed: 'Finalizate', cancellation_rate: 'Rată de anulare', no_show_rate: 'Rată de neprezentare',
  room_occupancy: 'Ocupare cabinet', procedure_estimated_duration: 'Durată estimată',
  procedure_measured_duration: 'Durată măsurată', procedure_manual_duration: 'Durate manuale',
  treatment_items_uncontinued: 'Tratamente necontinuate', services_performed: 'Servicii efectuate',
  receipts_net_by_method: 'Încasări nete pe metodă', financial_reversals: 'Anulări financiare', consumptions: 'Consumuri',
};
const today = () => new Intl.DateTimeFormat('en-CA', { year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
const showValue = (value: ReportResult['value']) => value === null ? 'Nedefinit' : typeof value === 'object' ? JSON.stringify(value) : String(value);

export function ReportsPage() {
  const [from, setFrom] = useState(today());
  const [to, setTo] = useState(today());
  const [doctorId, setDoctorId] = useState('');
  const [roomId, setRoomId] = useState('');
  const [results, setResults] = useState<ReportResult[]>([]);
  const [records, setRecords] = useState<Record<string, Awaited<ReturnType<typeof getReportRecords>>>>({});
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  async function loadReports() {
    const start = Date.parse(`${from}T00:00:00Z`);
    const end = Date.parse(`${to}T00:00:00Z`);
    if (!Number.isFinite(start) || !Number.isFinite(end)) { setError('Alegeți date valide pentru perioadă.'); return; }
    if (start > end) { setError('Data de început nu poate fi după data de final.'); return; }
    const calendarDays = (end - start) / 86_400_000 + 1;
    if (calendarDays > 366) { setError('Perioada trebuie să aibă între 1 și 366 de zile calendaristice.'); return; }
    setLoading(true); setError(''); setRecords({});
    try {
      const filters = { ...(doctorId ? { doctor_id: doctorId } : {}), ...(roomId ? { room_id: roomId } : {}) };
      const next = await Promise.all(indicators.map(key => getReport(key, from, to, filters)));
      setResults(next);
    } catch (err) { setError(apiErrorMessage(err)); }
    finally { setLoading(false); }
  }

  async function toggleRecords(report: ReportResult) {
    const indicator = report.indicator;
    if (records[indicator]) { setRecords(current => { const next = { ...current }; delete next[indicator]; return next; }); return; }
    try {
      const filters = { ...(report.filters.doctor_id ? { doctor_id: report.filters.doctor_id } : {}), ...(report.filters.room_id ? { room_id: report.filters.room_id } : {}) };
      const result = await getReportRecords(indicator, report.period.from, report.period.to, filters);
      setRecords(current => ({ ...current, [indicator]: result }));
    }
    catch (err) { setError(apiErrorMessage(err)); }
  }

  return <section className="reports-page" aria-label="Rapoarte">
    <header><p className="eyebrow">T24 · RAPOARTE</p><h1>Rapoarte</h1></header>
    <div className="reports-period">
      <label>De la<input type="date" aria-label="De la" value={from} onChange={e => setFrom(e.target.value)} /></label>
      <label>Până la<input type="date" aria-label="Până la" value={to} onChange={e => setTo(e.target.value)} /></label>
      <label>Medic (UUID opțional)<input aria-label="Medic" value={doctorId} onChange={e => setDoctorId(e.target.value)} /></label>
      <label>Cabinet (UUID opțional)<input aria-label="Cabinet" value={roomId} onChange={e => setRoomId(e.target.value)} /></label>
      <button type="button" onClick={loadReports} disabled={loading || !from || !to}>{loading ? 'Se încarcă…' : 'Generează raportul'}</button>
    </div>
    <p className="reports-financial-note">Evidență internă, nu raport fiscal.</p>
    {error && <p role="alert" className="reports-error">{error}</p>}
    <div className="reports-grid">
      {results.map(report => <article className="report-card" key={report.indicator}>
        <h2>{labels[report.indicator]}</h2>
        <p className="report-value">{report.value && typeof report.value === 'object' && 'status' in report.value && report.value.status === 'undefined'
          ? 'Nedefinit' : showValue(report.value)} <span>{report.unit}</span></p>
        {report.undefined_reason && <p>{report.undefined_reason}</p>}
        <p>{report.definition}</p>
        <small>{report.period.from} – {report.period.to} ({report.period.timezone}) · Sursă: {report.source}</small>
        <a href={report.records_url} onClick={event => { event.preventDefault(); void toggleRecords(report); }}>
          {records[report.indicator] ? 'Ascunde înregistrările' : 'Vezi înregistrările'}
        </a>
        {report.indicator === 'consumptions' && <p>Indisponibil până la T17.</p>}
        {records[report.indicator] && <ul aria-label={`Înregistrări ${labels[report.indicator]}`}>
          {records[report.indicator].records.map(record => <li key={`${record.entity}:${record.id}:${record.date}`}>{record.date} · {record.entity} · {record.patient_name ?? '—'} · {record.status}{record.amount ? ` · ${record.amount}` : ''}</li>)}
        </ul>}
      </article>)}
    </div>
  </section>;
}
