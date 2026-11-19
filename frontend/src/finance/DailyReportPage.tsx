import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { apiErrorMessage, getClinic } from '../api/client';
import { DailyReport, getDailyReport, KIND_LABELS, METHOD_LABELS, PaymentMethod } from './api';
import { formatClinicDateTime } from './PatientAccountPanel';
import './finance.css';

const clinicToday = (timezone: string) => new Intl.DateTimeFormat('en-CA', { year: 'numeric', month: '2-digit', day: '2-digit', timeZone: timezone }).format(new Date());

function Report({ report }: { report: DailyReport }) {
  return <>
    <table aria-label="Încasări pe metodă"><thead><tr><th>Metodă</th><th>Net ({report.currency})</th></tr></thead><tbody>
      {(Object.keys(METHOD_LABELS) as PaymentMethod[]).map(method => <tr key={method} data-method={method}><td>{METHOD_LABELS[method]}</td><td>{report.by_method[method]}</td></tr>)}
      <tr><td><strong>Total net</strong></td><td data-testid="daily-net"><strong>{report.net}</strong></td></tr>
    </tbody></table>
    <p className="muted">Încasat {report.received} · rambursat {report.refunded} · anulări {report.reversed} {report.currency}. Ziua {report.date} în fusul clinicii ({report.timezone}).</p>
    <h2>Mișcări</h2>
    {report.entries.length === 0 && <p className="muted">Nicio mișcare de numerar în această zi.</p>}
    <table aria-label="Mișcări zi"><tbody>{report.entries.map(entry => <tr key={entry.id} data-entry-id={entry.id}>
      <td>{formatClinicDateTime(entry.created_at, report.timezone)}</td><td>{entry.patient_name ?? '—'}</td><td>{KIND_LABELS[entry.kind]}</td>
      <td>{entry.method ? METHOD_LABELS[entry.method] : '—'}</td><td>{entry.signed_amount}</td><td>{entry.created_by ?? '—'}{entry.reason ? ` · ${entry.reason}` : ''}</td>
    </tr>)}</tbody></table>
  </>;
}

export function DailyReportPage() {
  const clinic = useQuery({ queryKey: ['clinic'], queryFn: getClinic });
  const timezone = clinic.data?.timezone;
  const [date, setDate] = useState('');
  const day = date || (timezone ? clinicToday(timezone) : '');
  const report = useQuery({ queryKey: ['finance-daily', day], queryFn: () => getDailyReport(day), enabled: Boolean(day) });
  return <section className="card finance-daily" aria-label="Încasări zi">
    <header><div><p className="eyebrow">T13 · EVIDENȚĂ INTERNĂ — NU ESTE RAPORT FISCAL</p><h1>Încasări zi</h1></div></header>
    {clinic.isError && <p className="error" role="alert">Fusul clinicii nu a putut fi încărcat: {apiErrorMessage(clinic.error)}</p>}
    <label>Ziua (în fusul clinicii)<input type="date" aria-label="Ziua raportului" value={day} onChange={event => setDate(event.target.value)} /></label>
    {report.isLoading && <p className="muted">Se încarcă…</p>}
    {report.isError && <p className="error" role="alert">{apiErrorMessage(report.error)}</p>}
    {report.data && <Report report={report.data} />}
  </section>;
}
