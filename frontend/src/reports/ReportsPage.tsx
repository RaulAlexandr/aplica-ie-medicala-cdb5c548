import { useEffect, useState } from 'react';
import { apiErrorMessage, ClinicLocation, getLocationPreferences, LocationPreferences, listLocations } from '../api/client';
import { getReport, getReportRecords, ReportResult } from './api';
import './reports.css';

type ConsumptionGroup = {
  item_id: string; item_name: string; unit: string; source_type: 't18' | 'direct';
  quantity: string; estimated_quantity: string | null; actual_quantity: string;
};

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
  const [siteId, setSiteId] = useState('');
  const [sites, setSites] = useState<ClinicLocation[]>([]);
  const [sitePreferences, setSitePreferences] = useState<LocationPreferences | null>(null);
  const [results, setResults] = useState<ReportResult[]>([]);
  const [records, setRecords] = useState<Record<string, Awaited<ReturnType<typeof getReportRecords>>>>({});
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    let live = true;
    void Promise.all([listLocations(), getLocationPreferences()]).then(([locations, preferences]) => {
      if (!live) return;
      setSites(locations); setSitePreferences(preferences);
      if (preferences.default_location_id) setSiteId(current => current || preferences.default_location_id!);
    }).catch(() => undefined);
    return () => { live = false; };
  }, []);

  async function loadReports() {
    const start = Date.parse(`${from}T00:00:00Z`);
    const end = Date.parse(`${to}T00:00:00Z`);
    if (!Number.isFinite(start) || !Number.isFinite(end)) { setError('Alegeți date valide pentru perioadă.'); return; }
    if (start > end) { setError('Data de început nu poate fi după data de final.'); return; }
    const calendarDays = (end - start) / 86_400_000 + 1;
    if (calendarDays > 366) { setError('Perioada trebuie să aibă între 1 și 366 de zile calendaristice.'); return; }
    setLoading(true); setError(''); setRecords({});
    try {
      const filters = { ...(doctorId ? { doctor_id: doctorId } : {}), ...(roomId ? { room_id: roomId } : {}), ...(siteId ? { site_id: siteId } : {}) };
      const next = await Promise.all(indicators.filter(key => key !== 'room_occupancy' || Boolean(roomId))
        .map(key => getReport(key, from, to, filters)));
      if (!roomId) {
        const occupancyIndex = indicators.indexOf('room_occupancy');
        const reason = 'Selectați un cabinet pentru a calcula ocuparea.';
        next.splice(occupancyIndex, 0, {
          indicator: 'room_occupancy', value: { status: 'undefined', reason }, unit: 'minute și %',
          definition: 'Minute active programate / minute disponibile din WorkingHours pentru cabinet.',
          period: next[0].period, filters: { doctor_id: doctorId || null, room_id: null, site_id: siteId || null },
          source: 'appointments, working_hours', undefined_reason: reason, records_url: '',
        });
      }
      setResults(next);
    } catch (err) { setError(apiErrorMessage(err)); }
    finally { setLoading(false); }
  }

  async function toggleRecords(report: ReportResult) {
    const indicator = report.indicator;
    if (records[indicator]) { setRecords(current => { const next = { ...current }; delete next[indicator]; return next; }); return; }
    try {
      const filters = { ...(report.filters.doctor_id ? { doctor_id: report.filters.doctor_id } : {}), ...(report.filters.room_id ? { room_id: report.filters.room_id } : {}), ...(report.filters.site_id ? { site_id: report.filters.site_id } : {}) };
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
      <label>Cabinet (UUID; necesar pentru ocupare)<input aria-label="Cabinet" value={roomId} onChange={e => setRoomId(e.target.value)} /></label>
      <label>Sediu<select aria-label="Sediu rapoarte" value={siteId} onChange={e => setSiteId(e.target.value)}><option value="">Toate sediile</option>{sites.filter(site => site.is_active && (sitePreferences?.all_locations !== false || sitePreferences?.location_ids.includes(site.id))).map(site => <option key={site.id} value={site.id}>{site.name}</option>)}</select></label>
      <button type="button" onClick={loadReports} disabled={loading || !from || !to}>{loading ? 'Se încarcă…' : 'Generează raportul'}</button>
    </div>
    <p className="reports-financial-note">Evidență internă, nu raport fiscal.</p>
    {error && <p role="alert" className="reports-error">{error}</p>}
    <div className="reports-grid">
      {results.map(report => <article className="report-card" key={report.indicator}>
        <h2>{labels[report.indicator]}</h2>
        <p className="report-value">{report.indicator === 'consumptions' && Array.isArray(report.value)
          ? `${report.value.length} grupe articol/sursă`
          : report.value && typeof report.value === 'object' && 'status' in report.value && report.value.status === 'undefined'
            ? 'Nedefinit' : showValue(report.value)} <span>{report.unit}</span></p>
        {report.undefined_reason && <p>{report.undefined_reason}</p>}
        <p>{report.definition}</p>
        {report.indicator === 'consumptions' && (report.filters.doctor_id || report.filters.room_id)
          && <p>Consumul direct nu poate fi atribuit medicului sau cabinetului; este exclus când sunt aplicate aceste filtre.</p>}
        {report.indicator === 'consumptions' && Array.isArray(report.value) && <ul aria-label="Totaluri de consum pe articol, unitate și sursă">
          {(report.value as ConsumptionGroup[]).map(group => <li key={`${group.item_id}:${group.unit}:${group.source_type}`}>
            {group.item_name} · {group.source_type === 't18' ? 'Manoperă (T18)' : 'Consum direct (Stocuri)'} · {group.quantity} {group.unit}
            {group.source_type === 't18' && ` · Estimat ${group.estimated_quantity ?? '0.000'} · Real ${group.actual_quantity}`}
          </li>)}
          {report.value.length === 0 && <li>Nu există consumuri în perioada selectată.</li>}
        </ul>}
        <small>{report.period.from} – {report.period.to} ({report.period.timezone}) · Sursă: {report.source}</small>
        {report.indicator === 'room_occupancy' && !report.filters.room_id
          ? <p>Selectați cabinetul pentru a vedea înregistrările ocupării.</p>
          : <a href={report.records_url} onClick={event => { event.preventDefault(); void toggleRecords(report); }}>
            {records[report.indicator] ? 'Ascunde înregistrările' : 'Vezi înregistrările'}
          </a>}
        {records[report.indicator] && report.indicator === 'consumptions' && <ul aria-label="Înregistrări Consumuri">
          {records[report.indicator].records.map(record => <li key={`${record.entity}:${record.id}:${record.date}`}>
            <strong>{String(record.date)} · {String(record.item_name)} · {String(record.quantity)} {String(record.unit)}</strong>
            <p>{record.source_type === 't18' ? 'Consum confirmat (T18)' : 'Consum direct (Stocuri)'} · Lot {String(record.lot)} · Locație {String(record.location)} · Autor {String(record.author)}</p>
            <p>Tip {String(record.movement_kind)} · Motiv {String(record.reason || '—')}</p>
            {record.source_type === 't18' && <p>Manoperă {String(record.procedure_name)} · Pacient {String(record.patient_name ?? '—')} · Estimat {String(record.estimated_quantity)} · Real {String(record.actual_quantity)}</p>}
          </li>)}
        </ul>}
        {records[report.indicator] && report.indicator !== 'consumptions' && <ul aria-label={`Înregistrări ${labels[report.indicator]}`}>
          {records[report.indicator].records.map(record => <li key={`${record.entity}:${record.id}:${record.date}`}>{record.date} · {record.entity} · {record.patient_name ?? '—'} · {record.status}{record.amount ? ` · ${record.amount}` : ''}</li>)}
        </ul>}
      </article>)}
    </div>
  </section>;
}
