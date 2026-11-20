import { FormEvent, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { apiErrorMessage } from '../api/client';
import {
  AccountEntry, amountError, createEntry, EntryInput, getPatientAccount, KIND_LABELS, METHOD_LABELS, newRequestId,
  PatientAccount, PaymentMethod, reverseEntry,
} from './api';
import './finance.css';

type FormKind = EntryInput['kind'];
const CASH_KINDS: FormKind[] = ['payment', 'refund'];
const ADJUST_KINDS: FormKind[] = ['adjustment_credit', 'adjustment_debit'];
const STATUS_LABELS: Record<string, string> = { pending: 'în lucru (nepornită)', running: 'în lucru', paused: 'în lucru (pauză)', cancelled: 'anulată, fără sumă', completed: 'efectuată' };

export function formatClinicDateTime(value: string | null, timezone: string) {
  if (!value) return '—';
  return new Intl.DateTimeFormat('ro-RO', { dateStyle: 'medium', timeStyle: 'short', timeZone: timezone }).format(new Date(value));
}

function balanceLabel(account: PatientAccount) {
  const { balance, balance_status: status } = account.totals;
  if (status === 'credit') return `${balance.replace('-', '')} ${account.currency} credit (avans al pacientului)`;
  if (status === 'settled') return `0.00 ${account.currency} · achitat`;
  return `${balance} ${account.currency} de plată`;
}

function EntryForm({ patientId, account, onSaved }: { patientId: string; account: PatientAccount; onSaved: () => Promise<void> }) {
  const kinds = [...(account.permissions.can_record_cash ? CASH_KINDS : []), ...(account.permissions.can_adjust ? ADJUST_KINDS : [])];
  const [kind, setKind] = useState<FormKind>(kinds[0]);
  const [amount, setAmount] = useState('');
  const [method, setMethod] = useState<PaymentMethod | ''>('cash');
  const [reason, setReason] = useState('');
  const [appointmentId, setAppointmentId] = useState('');
  const [requestId, setRequestId] = useState(newRequestId);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState('');
  const inFlight = useRef(false); // synchronous guard: two submits in the same tick send one request
  const isCash = CASH_KINDS.includes(kind);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (inFlight.current) return;
    setSaved('');
    const problem = amountError(amount)
      ?? (isCash && !method ? 'Alege metoda de plată.' : null)
      ?? (kind !== 'payment' && !reason.trim() ? 'Motivul este obligatoriu pentru rambursări și ajustări.' : null);
    if (problem) { setError(problem); return; }
    inFlight.current = true;
    setBusy(true); setError('');
    try {
      await createEntry(patientId, {
        kind, amount: amount.trim(), method: isCash ? method as PaymentMethod : null, reason: reason.trim() || null,
        appointment_id: appointmentId || null, client_request_id: requestId,
      });
      setAmount(''); setReason(''); setAppointmentId(''); setRequestId(newRequestId());
      setSaved(`${KIND_LABELS[kind]} înregistrată.`);
      await onSaved();
    } catch (err) {
      setError(apiErrorMessage(err)); // the same client_request_id is kept, so a retry cannot duplicate
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  return <form className="finance-form" aria-label="Mișcare nouă" onSubmit={event => void submit(event)}>
    <div className="finance-grid">
      <label>Tip mișcare<select aria-label="Tip mișcare" value={kind} disabled={busy} onChange={event => { setKind(event.target.value as FormKind); setError(''); }}>
        {kinds.map(option => <option key={option} value={option}>{KIND_LABELS[option]}</option>)}
      </select></label>
      <label>Sumă ({account.currency})<input aria-label="Sumă" inputMode="decimal" placeholder="150.00" value={amount} disabled={busy} onChange={event => setAmount(event.target.value)} /></label>
      {isCash && <label>Metodă<select aria-label="Metodă" value={method} disabled={busy} onChange={event => setMethod(event.target.value as PaymentMethod)}>
        {(Object.keys(METHOD_LABELS) as PaymentMethod[]).map(option => <option key={option} value={option}>{METHOD_LABELS[option]}</option>)}
      </select></label>}
      <label>Vizită asociată (opțional)<select aria-label="Vizită asociată" value={appointmentId} disabled={busy} onChange={event => setAppointmentId(event.target.value)}>
        <option value="">Fără vizită (avans / general)</option>
        {account.services.visits.map(visit => <option key={visit.appointment_id} value={visit.appointment_id}>{formatClinicDateTime(visit.starts_at, account.timezone)} · {visit.appointment_type ?? 'vizită'}</option>)}
      </select></label>
    </div>
    <label>Motiv{kind === 'payment' ? ' (opțional)' : ' (obligatoriu)'}<input aria-label="Motiv" value={reason} maxLength={500} disabled={busy} onChange={event => setReason(event.target.value)} /></label>
    {error && <p className="error" role="alert" data-testid="finance-form-error">{error}</p>}
    {saved && <p className="success" data-testid="finance-form-saved">{saved}</p>}
    <button type="submit" disabled={busy}>{busy ? 'Se înregistrează…' : 'Înregistrează'}</button>
  </form>;
}

function ReverseControl({ entry, onDone }: { entry: AccountEntry; onDone: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState('');
  const [requestId, setRequestId] = useState(newRequestId);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const inFlight = useRef(false);
  if (!open) return <button type="button" className="secondary" onClick={() => setOpen(true)}>Anulează mișcarea</button>;
  async function confirm() {
    if (inFlight.current || !reason.trim()) return;
    inFlight.current = true;
    setBusy(true); setError('');
    try {
      await reverseEntry(entry.id, reason.trim(), requestId);
      setOpen(false); setReason(''); setRequestId(newRequestId());
      await onDone();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }
  return <div className="finance-reverse">
    <label>Motivul anulării<input aria-label={`Motiv anulare ${entry.id}`} value={reason} maxLength={500} disabled={busy} onChange={event => setReason(event.target.value)} /></label>
    <p className="muted">Anularea creează o mișcare inversă trasabilă; înregistrarea inițială rămâne în istoric.</p>
    {error && <p className="error" role="alert">{error}</p>}
    <button type="button" disabled={busy || !reason.trim()} onClick={() => void confirm()}>{busy ? 'Se anulează…' : 'Confirmă anularea'}</button>
    <button type="button" className="link" disabled={busy} onClick={() => { setOpen(false); setError(''); }}>Renunță</button>
  </div>;
}

export function PatientAccountPanel({ patientId }: { patientId: string }) {
  const client = useQueryClient();
  const account = useQuery({ queryKey: ['patient-account', patientId], queryFn: () => getPatientAccount(patientId) });
  const refresh = async () => { await client.invalidateQueries({ queryKey: ['patient-account', patientId] }); };
  const data = account.data;
  const canWrite = Boolean(data && (data.permissions.can_record_cash || data.permissions.can_adjust));
  const canReverse = (entry: AccountEntry) => Boolean(data) && entry.kind !== 'reversal' && !entry.reversed_by
    && (['payment', 'refund'].includes(entry.kind) ? data!.permissions.can_record_cash : data!.permissions.can_adjust);
  return <section className="card clinical-record-section finance-panel" aria-label="Cont pacient">
    <div className="detail-heading"><div><p className="eyebrow">T13 · EVIDENȚĂ INTERNĂ — NU ESTE FACTURĂ SAU BON FISCAL</p><h2>Cont pacient</h2></div></div>
    {account.isLoading && <p className="muted">Se încarcă contul…</p>}
    {account.isError && <p className="error" role="alert">{apiErrorMessage(account.error)} <button className="secondary" onClick={() => void account.refetch()}>Reîncearcă</button></p>}
    {data && <>
      <dl className="finance-totals">
        <div><dt>Servicii efectuate</dt><dd data-testid="account-services">{data.totals.services_completed} {data.currency}</dd></div>
        <div><dt>Încasat net</dt><dd data-testid="account-paid">{data.totals.paid_net} {data.currency}</dd></div>
        <div><dt>Sold</dt><dd data-testid="account-balance" className={`balance-${data.totals.balance_status}`}>{balanceLabel(data)}</dd></div>
      </dl>
      {(data.totals.adjustments_debit !== '0.00' || data.totals.adjustments_credit !== '0.00') && <p className="muted" data-testid="account-adjustments">Ajustări: +{data.totals.adjustments_debit} / −{data.totals.adjustments_credit} {data.currency}</p>}
      <h3>Servicii pe vizită</h3>
      {data.services.visits.length === 0 && <p className="muted">Nicio manoperă înregistrată.</p>}
      {data.services.visits.map(visit => <article className="finance-visit" key={visit.appointment_id} data-visit-id={visit.appointment_id}>
        <p><strong>{formatClinicDateTime(visit.starts_at, data.timezone)}</strong> · {visit.appointment_type ?? 'Vizită'} · total efectuat <strong>{visit.completed_total} {data.currency}</strong></p>
        <ul>{visit.procedures.map(procedure => <li key={procedure.id} data-procedure-status={procedure.status}>
          {procedure.code} · {procedure.name}{procedure.tooth_fdi ? ` · FDI ${procedure.tooth_fdi}` : ''} · {STATUS_LABELS[procedure.status] ?? procedure.status}
          {procedure.counted ? ` · ${procedure.amount} ${data.currency}${procedure.price_overridden ? ' (tarif ajustat de manager)' : ''}` : ''}
        </li>)}</ul>
      </article>)}
      {data.services.in_progress_count > 0 && <p className="muted" data-testid="account-in-progress">{data.services.in_progress_count} manopere în lucru — nu intră în suma datorată până la finalizare.</p>}
      {canWrite && <EntryForm key={patientId} patientId={patientId} account={data} onSaved={refresh} />}
      {!canWrite && <p className="muted">Rolul tău poate consulta contul, fără a înregistra mișcări.</p>}
      <h3>Mișcări</h3>
      {data.entries.length === 0 && <p className="muted">Nicio mișcare înregistrată.</p>}
      <div className="finance-entries">{[...data.entries].reverse().map(entry => <article key={entry.id} className={`finance-entry${entry.reversed_by ? ' reversed' : ''}`} data-entry-id={entry.id} data-entry-kind={entry.kind}>
        <p><strong>{KIND_LABELS[entry.kind]}</strong> · {entry.amount} {entry.currency}{entry.method ? ` · ${METHOD_LABELS[entry.method]}` : ''}{entry.appointment_id ? ' · legată de vizită' : ''}</p>
        <p className="muted">{entry.created_by.name ?? '—'} · {formatClinicDateTime(entry.created_at, data.timezone)}{entry.reason ? ` · Motiv: ${entry.reason}` : ''}</p>
        {entry.reverses_entry_id && <p className="muted">Anulează mișcarea {entry.reverses_entry_id.slice(0, 8)}</p>}
        {entry.reversed_by && <p className="finance-reversed" data-testid="entry-reversed">ANULATĂ de {entry.reversed_by.by ?? '—'} la {formatClinicDateTime(entry.reversed_by.created_at, data.timezone)}: {entry.reversed_by.reason}</p>}
        {canReverse(entry) && <ReverseControl entry={entry} onDone={refresh} />}
      </article>)}</div>
    </>}
  </section>;
}
