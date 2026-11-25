import { FormEvent, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { apiErrorMessage } from '../api/client';
import { CampaignFilter, FilterType, createCampaign, feedbackReport, getFeedbackSettings, launchCampaign, listCampaigns, listFeedback, previewCampaign, saveFeedbackSettings, submitPublicFeedback, getPublicFeedback, checkCampaignUnsubscribe, unsubscribeCampaigns } from './api';
import './feedback.css';

const labels: Record<FilterType, string> = { last_visit_before: 'Ultima vizită înainte de data aleasă', recall_due: 'Rechemare scadentă la data aleasă', procedure_in_period: 'Procedură efectuată în perioadă', no_future_appointment: 'Fără programare viitoare' };

function FeedbackList() {
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const filter = { ...(start ? { start } : {}), ...(end ? { end } : {}) };
  const query = useQuery({ queryKey: ['feedback', filter], queryFn: () => listFeedback(filter) });
  const report = useQuery({ queryKey: ['feedback-report', filter], queryFn: () => feedbackReport(filter) });
  return <><section className="card feedback-filters"><h2>Feedback primit</h2><div className="grid two"><label>De la<input type="date" value={start} onChange={e => setStart(e.target.value)} /></label><label>Până la<input type="date" value={end} onChange={e => setEnd(e.target.value)} /></label></div></section>
    {report.data && <section className="stats feedback-stats"><div className="card"><span>Rata de răspuns</span><strong>{Math.round(report.data.response_rate * 100)}%</strong><small>{report.data.responses} răspunsuri din {report.data.requests} cereri</small></div><div className="card"><span>Distribuția notelor</span><strong>{[1, 2, 3, 4, 5].map(n => `${n}: ${report.data.ratings[n]}`).join(' · ')}</strong></div><div className="card"><span>Pe medic</span>{Object.values(report.data.by_doctor).length ? Object.values(report.data.by_doctor).map(doctor => <small key={doctor.name}>{doctor.name}: {doctor.ratings[1]} / {doctor.ratings[2]} / {doctor.ratings[3]} / {doctor.ratings[4]} / {doctor.ratings[5]}</small>) : <small>Fără răspunsuri</small>}</div></section>}
    <section className="card feedback-list">{query.isLoading ? <p>Se încarcă…</p> : query.isError ? <p className="error">{apiErrorMessage(query.error)}</p> : !query.data?.length ? <p className="muted">Nu există răspunsuri în perioada aleasă.</p> : query.data.map(row => <article key={row.id}><div className="feedback-rating" aria-label={`Nota ${row.rating} din 5`}>{'★'.repeat(row.rating)}{'☆'.repeat(5 - row.rating)}</div><div><strong>{row.patient_name}</strong><p>{row.comment || 'Fără comentariu'}</p><small>{new Date(row.created_at).toLocaleDateString('ro-RO')} · {row.doctor_name} · <a href={`/appointments/${row.appointment_id}/visit`}>Vizita</a></small>{row.task && <p className="muted">Sarcină creată: {row.task.status}</p>}</div></article>)}</section></>;
}

function Campaigns() {
  const client = useQueryClient();
  const [filter, setFilter] = useState<FilterType>('last_visit_before');
  const [filterDate, setFilterDate] = useState('');
  const [periodStart, setPeriodStart] = useState('');
  const [periodEnd, setPeriodEnd] = useState('');
  const [name, setName] = useState('');
  const [scheduled, setScheduled] = useState('');
  const [preview, setPreview] = useState<Awaited<ReturnType<typeof previewCampaign>> | null>(null);
  const [error, setError] = useState('');
  const list = useQuery({ queryKey: ['feedback-campaigns'], queryFn: listCampaigns });
  const payload = (): CampaignFilter => ({ filter_type: filter, ...(filter === 'procedure_in_period' ? { period_start: new Date(periodStart).toISOString(), period_end: new Date(periodEnd).toISOString() } : filter !== 'no_future_appointment' ? { filter_date: filterDate } : {}) });
  const previewMutation = useMutation({ mutationFn: previewCampaign, onSuccess: data => { setPreview(data); setError(''); }, onError: e => setError(apiErrorMessage(e)) });
  const createMutation = useMutation({ mutationFn: () => createCampaign({ ...payload(), name, scheduled_for: new Date(scheduled).toISOString() }), onSuccess: async campaign => { await launchCampaign(campaign.id); setName(''); setPreview(null); await client.invalidateQueries({ queryKey: ['feedback-campaigns'] }); }, onError: e => setError(apiErrorMessage(e)) });
  const validFilter = filter === 'no_future_appointment' || (filter === 'procedure_in_period' ? !!periodStart && !!periodEnd : !!filterDate);
  return <div className="feedback-campaigns">
    <section className="card"><h2>Campanie nouă</h2><label>Nume<input value={name} onChange={e => setName(e.target.value)} /></label>
      <label>Public<select value={filter} onChange={e => { setFilter(e.target.value as FilterType); setPreview(null); }}>
        {Object.entries(labels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
      </select></label>
      {filter === 'procedure_in_period' ? <div className="grid two"><label>De la<input type="date" value={periodStart.slice(0, 10)} onChange={e => setPeriodStart(`${e.target.value}T00:00:00Z`)} /></label><label>Până la<input type="date" value={periodEnd.slice(0, 10)} onChange={e => setPeriodEnd(`${e.target.value}T23:59:59.999Z`)} /></label></div> : filter !== 'no_future_appointment' && <label>Data de referință<input type="date" value={filterDate} onChange={e => setFilterDate(e.target.value)} /></label>}
      <label>Momentul trimiterii<input type="datetime-local" value={scheduled} onChange={e => setScheduled(e.target.value)} /></label>
      <p className="muted">Se folosește șablonul „Campanie” din Comunicarea cu pacienții.</p>
      <div className="feedback-actions"><button type="button" disabled={!validFilter || previewMutation.isPending} onClick={() => previewMutation.mutate(payload())}>Previzualizează destinatarii</button><button type="button" disabled={!preview || !name.trim() || !scheduled || createMutation.isPending} onClick={() => createMutation.mutate()}>Salvează și lansează</button></div>
      {error && <p className="error" role="alert">{error}</p>}
      {preview && <div className="recipient-preview"><h3>{preview.eligible_count} eligibili din {preview.count}</h3><ul>{preview.recipients.map(item => <li key={item.id}><span>{item.name}</span><span>{item.eligible ? item.channel : 'Fără consimțământ sau dezabonat'}</span></li>)}</ul></div>}
    </section>
    <section className="card"><h2>Campanii lansate</h2>{list.data?.map(c => <article className="campaign-row" key={c.id}><strong>{c.name}</strong><span>{labels[c.filter_type]} · {c.status}</span><time>{new Date(c.scheduled_for).toLocaleString('ro-RO')}</time></article>)}</section>
  </div>;
}

function Settings() {
  const client = useQueryClient();
  const query = useQuery({ queryKey: ['feedback-settings'], queryFn: getFeedbackSettings });
  const [form, setForm] = useState({ delay_minutes: 120, negative_threshold: 3, review_enabled: false, review_url: '', hourly_limit: 100 });
  const [loaded, setLoaded] = useState(false);
  if (query.data && !loaded) { setForm({ ...query.data, review_url: query.data.review_url || '' }); setLoaded(true); }
  const save = useMutation({ mutationFn: () => saveFeedbackSettings({ ...form, review_url: form.review_url || null }), onSuccess: () => client.invalidateQueries({ queryKey: ['feedback-settings'] }) });
  return <form className="card feedback-settings" onSubmit={e => { e.preventDefault(); save.mutate(); }}><h2>Setări feedback</h2><label>Întârziere după închiderea vizitei (minute)<input type="number" min={0} max={10080} value={form.delay_minutes} onChange={e => setForm({ ...form, delay_minutes: Number(e.target.value) })} /></label><label>Prag pentru sarcină negativă<input type="number" min={1} max={5} value={form.negative_threshold} onChange={e => setForm({ ...form, negative_threshold: Number(e.target.value) })} /></label><label className="feedback-check"><input type="checkbox" checked={form.review_enabled} onChange={e => setForm({ ...form, review_enabled: e.target.checked })} /> Invită la recenzie publică</label><label>Link recenzii<input type="url" value={form.review_url} onChange={e => setForm({ ...form, review_url: e.target.value })} /></label><label>Limită mesaje de campanie pe oră<input type="number" min={1} max={10000} value={form.hourly_limit} onChange={e => setForm({ ...form, hourly_limit: Number(e.target.value) })} /></label>{save.isSuccess && <p role="status">Setări salvate.</p>}{save.isError && <p className="error">{apiErrorMessage(save.error)}</p>}<button disabled={save.isPending}>Salvează setările</button></form>;
}

export function FeedbackPage({ role }: { role: string }) {
  const [tab, setTab] = useState<'feedback' | 'campaigns' | 'settings'>('feedback');
  const manager = ['clinic_manager', 'administrator'].includes(role);
  return <div className="feedback-page"><header><div><p className="eyebrow">FEEDBACK PACIENȚI</p><h1>Feedback și campanii</h1></div></header><div className="feedback-tabs">{(['feedback', ...(manager ? ['campaigns', 'settings'] : [])] as Array<typeof tab>).map(key => <button key={key} className={tab === key ? 'active' : 'secondary'} onClick={() => setTab(key)}>{key === 'feedback' ? 'Feedback' : key === 'campaigns' ? 'Campanii' : 'Setări'}</button>)}</div>{tab === 'feedback' ? <FeedbackList /> : tab === 'campaigns' ? <Campaigns /> : <Settings />}</div>;
}

export function PublicFeedbackPage() {
  const { token = '' } = useParams();
  const query = useQuery({ queryKey: ['public-feedback', token], queryFn: () => getPublicFeedback(token), retry: false });
  const [rating, setRating] = useState(5);
  const [comment, setComment] = useState('');
  const [done, setDone] = useState(false);
  const [error, setError] = useState('');
  const mutation = useMutation({ mutationFn: () => submitPublicFeedback(token, rating, comment), onSuccess: () => setDone(true), onError: e => setError(apiErrorMessage(e)) });
  if (query.isLoading) return <main className="feedback-public"><p>Se încarcă…</p></main>;
  if (!query.data?.valid || done) return <main className="feedback-public"><section className="card"><h1>Feedback</h1><p>{done ? 'Mulțumim pentru răspuns.' : query.data?.message || 'Linkul nu este disponibil. Dacă ai trimis deja răspunsul, îți mulțumim.'}</p></section></main>;
  return <main className="feedback-public"><form className="card" onSubmit={(e: FormEvent) => { e.preventDefault(); mutation.mutate(); }}><p className="eyebrow">{query.data.clinic_name}</p><h1>Cum a fost vizita ta, {query.data.first_name}?</h1><p>{query.data.visit_date && new Date(`${query.data.visit_date}T12:00:00`).toLocaleDateString('ro-RO')}</p><label>Nota acordată <select aria-label="Nota acordată" value={rating} onChange={e => setRating(Number(e.target.value))}>{[5, 4, 3, 2, 1].map(n => <option key={n} value={n}>{n} din 5</option>)}</select></label><label>Comentariu (opțional)<textarea maxLength={2000} value={comment} onChange={e => setComment(e.target.value)} /></label>{error && <p className="error">{error}</p>}<button disabled={mutation.isPending}>Trimite feedback</button></form></main>;
}

export function PublicCampaignUnsubscribePage() {
  const { token = '' } = useParams();
  const query = useQuery({ queryKey: ['campaign-unsubscribe', token], queryFn: () => checkCampaignUnsubscribe(token), retry: false });
  const [message, setMessage] = useState('');
  const mutation = useMutation({ mutationFn: () => unsubscribeCampaigns(token), onSuccess: result => setMessage(result.message) });
  return <main className="feedback-public"><section className="card"><h1>Preferințe de comunicare</h1>{query.isLoading ? <p>Se verifică linkul…</p> : message ? <p>{message}</p> : !query.data?.valid ? <p>Linkul nu este disponibil.</p> : <><p>{query.data.message}</p><button disabled={mutation.isPending} onClick={() => mutation.mutate()}>Dezabonează-mă de la campanii</button></>}{mutation.isError && <p className="error">Nu am putut înregistra dezabonarea. Încearcă din nou.</p>}</section></main>;
}
