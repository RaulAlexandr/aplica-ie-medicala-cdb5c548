import { FormEvent, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { apiErrorMessage } from '../api/client';
import { amountError, createEntry, getVisitFinance, METHOD_LABELS, newRequestId, PaymentMethod } from './api';
import './finance.css';

const cents = (value: string) => Math.round(Number(value) * 100);
const money = (value: number) => (value / 100).toFixed(2);

/** Visit page: completed services of this visit, money recorded for it and a quick visit-linked payment. */
export function VisitFinanceCard({ appointmentId, refreshKey }: { appointmentId: string; refreshKey: string }) {
  const client = useQueryClient();
  const summary = useQuery({ queryKey: ['visit-finance', appointmentId, refreshKey], queryFn: () => getVisitFinance(appointmentId) });
  const [amount, setAmount] = useState('');
  const [method, setMethod] = useState<PaymentMethod>('cash');
  const [requestId, setRequestId] = useState(newRequestId);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState('');
  const inFlight = useRef(false);
  const data = summary.data;
  const remaining = data ? cents(data.services_completed) - cents(data.paid_net_for_visit) : 0;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!data || inFlight.current) return;
    const value = amount.trim() || (remaining > 0 ? money(remaining) : '');
    const problem = amountError(value);
    if (problem) { setError(problem); return; }
    inFlight.current = true;
    setBusy(true); setError(''); setSaved('');
    try {
      await createEntry(data.patient_id, { kind: 'payment', amount: value, method, appointment_id: appointmentId, client_request_id: requestId });
      setAmount(''); setRequestId(newRequestId()); setSaved(`Încasare de ${value} ${data.currency} înregistrată pentru vizită.`);
      await client.invalidateQueries({ queryKey: ['visit-finance', appointmentId] });
      await client.invalidateQueries({ queryKey: ['patient-account', data.patient_id] });
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  return <section className="finance-visit-card" aria-label="Încasare vizită">
    <p className="eyebrow">T13 · EVIDENȚĂ INTERNĂ</p>
    {summary.isLoading && <p className="muted">Se încarcă suma vizitei…</p>}
    {summary.isError && <p className="error" role="alert">{apiErrorMessage(summary.error)}</p>}
    {data && <>
      <p><strong>Servicii efectuate la vizită:</strong> <span data-testid="visit-services">{data.services_completed} {data.currency}</span> ({data.completed_count} finalizate{data.in_progress_count ? `, ${data.in_progress_count} în lucru fără sumă` : ''})</p>
      <p><strong>Încasat pentru vizită:</strong> <span data-testid="visit-paid">{data.paid_net_for_visit} {data.currency}</span></p>
      {data.permissions.can_record_cash && <form className="finance-form" aria-label="Încasare rapidă vizită" onSubmit={event => void submit(event)}>
        <label>Sumă de încasat<input aria-label="Sumă vizită" inputMode="decimal" placeholder={remaining > 0 ? money(remaining) : '0.00'} value={amount} disabled={busy} onChange={event => setAmount(event.target.value)} /></label>
        <label>Metodă<select aria-label="Metodă vizită" value={method} disabled={busy} onChange={event => setMethod(event.target.value as PaymentMethod)}>
          {(Object.keys(METHOD_LABELS) as PaymentMethod[]).map(option => <option key={option} value={option}>{METHOD_LABELS[option]}</option>)}
        </select></label>
        {error && <p className="error" role="alert">{error}</p>}
        {saved && <p className="success">{saved}</p>}
        <button type="submit" disabled={busy}>{busy ? 'Se înregistrează…' : 'Încasează pentru vizită'}</button>
      </form>}
      <Link className="button secondary" to={`/patients/${data.patient_id}`}>Cont pacient</Link>
    </>}
  </section>;
}
