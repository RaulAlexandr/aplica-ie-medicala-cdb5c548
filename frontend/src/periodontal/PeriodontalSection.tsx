import { useEffect, useState } from 'react';
import { apiErrorMessage, type User } from '../api/client';
import { compareAssessments, createAssessment, emptyTooth, finalizeAssessment, getAssessment, listAssessments, saveMeasurements, sites, type Assessment, type Comparison, type Site, type Tooth } from './api';
import './periodontal.css';

const fdi = Array.from({ length: 32 }, (_, i) => `${Math.floor(i / 8) + 1}${i % 8 + 1}`);
const display = (value: unknown) => value === null || value === undefined ? '—' : typeof value === 'boolean' ? (value ? 'Da' : 'Nu') : String(value);
const MAX_PROBING_DEPTH_MM = 20; // PM decision 2026-11-20; mirrors the backend limit
const MAX_RECESSION_MM = 20; // CUR-05: signed convention, magnitude bounded like probing depth; mirrors the backend limit
const limits = { probing_depth_mm: { label: 'Adâncime', min: 0, max: MAX_PROBING_DEPTH_MM }, recession_mm: { label: 'Recesiune', min: -MAX_RECESSION_MM, max: MAX_RECESSION_MM } } as const;
export function measurementError(teeth: Tooth[]): string {
  for (const tooth of teeth) for (const site of tooth.sites) for (const key of ['probing_depth_mm', 'recession_mm'] as const) {
    const value = site[key];
    if (value !== null && (!Number.isFinite(value) || value < limits[key].min || value > limits[key].max)) return `${limits[key].label} la dintele ${tooth.tooth_fdi} trebuie să fie între ${limits[key].min} și ${limits[key].max} mm.`;
  }
  return '';
}
const labels: Record<string, string> = { buccal_mesial: 'Vestibular · mezial', buccal_mid: 'Vestibular · mijloc', buccal_distal: 'Vestibular · distal', oral_mesial: 'Oral · mezial', oral_mid: 'Oral · mijloc', oral_distal: 'Oral · distal' };
// Clinic-local presentation (same rule as the rest of the clinical record): server instants are shown in the
// clinic time zone, and the default clinical date is the clinic's current calendar day, not the browser's UTC day.
const formatClinicDateTime = (value: string, timezone: string) => new Intl.DateTimeFormat('ro-RO', { dateStyle: 'medium', timeStyle: 'short', timeZone: timezone }).format(new Date(value));
const clinicToday = (timezone: string, now: Date = new Date()) => new Intl.DateTimeFormat('en-CA', { year: 'numeric', month: '2-digit', day: '2-digit', timeZone: timezone }).format(now);
const fields = [['probing_depth_mm', 'Adâncime (mm)', 'number'], ['recession_mm', 'Recesiune (mm)', 'number'], ['bop', 'BOP', 'boolean'], ['plaque', 'Placă', 'boolean'], ['suppuration', 'Supurație', 'boolean']] as const;

export function PeriodontalSection({ patientId, user, timezone }: { patientId: string; user: User; timezone: string }) {
  const [history, setHistory] = useState<Assessment[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [assessment, setAssessment] = useState<Assessment | null>(null);
  const [tooth, setTooth] = useState('11');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [compareFirst, setCompareFirst] = useState('');
  const [compareSecond, setCompareSecond] = useState('');
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [clinicalDate, setClinicalDate] = useState(() => clinicToday(timezone));
  const [correctionReason, setCorrectionReason] = useState('');
  const canCreate = ['doctor', 'clinic_manager', 'administrator'].includes(user.role);
  const refresh = async (selectId?: string) => {
    const rows = await listAssessments(patientId); setHistory(rows);
    const id = selectId ?? (rows.find(row => row.id === selectedId)?.id ?? rows[0]?.id ?? '');
    setSelectedId(id); setAssessment(id ? await getAssessment(id) : null);
  };
  useEffect(() => { setHistory([]); setAssessment(null); setSelectedId(''); setCompareFirst(''); setCompareSecond(''); setError(''); setComparison(null); void refresh().catch(e => setError(apiErrorMessage(e))); }, [patientId]);
  const run = async (action: () => Promise<void>, teeth?: Tooth[]) => { const invalid = teeth ? measurementError(teeth) : ''; if (invalid) { setError(invalid); return; } setBusy(true); setError(''); try { await action(); } catch (e) { setError(apiErrorMessage(e)); } finally { setBusy(false); } };
  const updateTooth = (fdiCode: string, change: (row: Tooth) => Tooth) => setAssessment(current => {
    if (!current) return current;
    const rows = current.teeth.some(row => row.tooth_fdi === fdiCode) ? current.teeth.map(row => row.tooth_fdi === fdiCode ? change(row) : row) : [...current.teeth, change(emptyTooth(fdiCode))];
    return { ...current, teeth: rows.sort((a, b) => a.tooth_fdi.localeCompare(b.tooth_fdi)) };
  });
  const currentTooth = assessment?.teeth.find(row => row.tooth_fdi === tooth) ?? emptyTooth(tooth);
  return <section className="card clinical-record-section periodontal-section" aria-label="Evaluare parodontală">
    <header className="detail-heading"><div><p className="eyebrow">EVALUĂRI PERSISTENTE</p><h2>Evaluare parodontală</h2></div>{canCreate && <button disabled={busy || !clinicalDate} onClick={() => void run(async () => {
      const row = await createAssessment(patientId, { clinical_date: clinicalDate }); await refresh(row.id);
    })}>Evaluare nouă</button>}</header>
    <p className="muted">Șase situri/dinte · adâncime și recesiune în mm · oral = lingual/palatal. Gol înseamnă nemăsurat; „Nu” înseamnă măsurat și absent.</p>
    {canCreate && <label>Data clinică pentru o evaluare nouă<input type="date" value={clinicalDate} onChange={event => setClinicalDate(event.target.value)} /></label>}
    {error && <p role="alert" className="error">{error}</p>}
    {!history.length && <p className="muted">Nu există evaluări parodontale.</p>}
    <label>Istoric<select value={selectedId} onChange={event => void run(async () => { setSelectedId(event.target.value); setAssessment(event.target.value ? await getAssessment(event.target.value) : null); })}><option value="">Alege o evaluare</option>{history.map(row => <option key={row.id} value={row.id}>{row.clinical_date} · {row.status === 'draft' ? 'Ciornă proprie' : 'Finalizată'} · {row.author_name}</option>)}</select></label>
    {assessment && <>
      <p className="muted">Data clinică: {assessment.clinical_date} · Autor: {assessment.author_name} · {assessment.status === 'draft' ? 'Ciornă' : `Finalizată ${assessment.finalized_at ? formatClinicDateTime(assessment.finalized_at, timezone) : ''}`}{assessment.relationship_kind ? ` · ${assessment.relationship_kind}${assessment.change_reason ? `: ${assessment.change_reason}` : ''}` : ''}</p>
      {assessment.status === 'draft' && assessment.author_id === user.id && <div className="periodontal-editor">
        <label>Dinte FDI<select value={tooth} onChange={event => setTooth(event.target.value)}>{fdi.map(code => <option key={code} value={code}>{code}</option>)}</select></label>
        <label><input type="checkbox" checked={currentTooth.is_excluded} onChange={e => updateTooth(tooth, row => ({ ...row, is_excluded: e.target.checked, mobility: e.target.checked ? null : row.mobility, furcation: e.target.checked ? null : row.furcation, sites: row.sites.map(site => ({ ...site, probing_depth_mm: null, recession_mm: null, bop: null, plaque: null, suppuration: null })) }))} /> Dinte exclus din evaluare</label>
        {!currentTooth.is_excluded && <>
          <div className="periodontal-tooth-fields"><label>Mobilitate<select value={currentTooth.mobility ?? ''} onChange={e => updateTooth(tooth, row => ({ ...row, mobility: e.target.value || null }))}><option value="">Nemăsurată</option>{['0','1','2','3'].map(v => <option key={v}>{v}</option>)}</select></label><label>Furcație<select value={currentTooth.furcation ?? ''} onChange={e => updateTooth(tooth, row => ({ ...row, furcation: e.target.value || null }))}><option value="">Nemăsurată</option>{['I','II','III'].map(v => <option key={v}>{v}</option>)}</select></label></div>
          <div className="periodontal-sites">{sites.map(name => { const value = currentTooth.sites.find(s => s.site === name)!; return <fieldset key={name}><legend>{labels[name]}</legend>{fields.map(([key, label, kind]) => <label key={key}>{label}{kind === 'number' ? <input type="number" min={limits[key as 'probing_depth_mm' | 'recession_mm'].min} max={limits[key as 'probing_depth_mm' | 'recession_mm'].max} step="0.01" value={value[key] ?? ''} onChange={e => updateTooth(tooth, row => ({ ...row, sites: row.sites.map(s => s.site === name ? { ...s, [key]: e.target.value === '' ? null : Number(e.target.value) } as Site : s) }))} /> : <select value={value[key] === null ? '' : value[key] ? 'yes' : 'no'} onChange={e => updateTooth(tooth, row => ({ ...row, sites: row.sites.map(s => s.site === name ? { ...s, [key]: e.target.value === '' ? null : e.target.value === 'yes' } as Site : s) }))}><option value="">Nemăsurată</option><option value="yes">Da</option><option value="no">Nu</option></select>}</label>)}</fieldset>; })}</div>
        </>}
        <button className="secondary" disabled={busy} onClick={() => void run(async () => { const saved = await saveMeasurements(assessment.id, assessment.teeth); setAssessment(saved); await refresh(saved.id); }, assessment.teeth)}>Salvează ciorna</button>
        <button disabled={busy} onClick={() => void run(async () => { const saved = await saveMeasurements(assessment.id, assessment.teeth); const done = await finalizeAssessment(saved.id); await refresh(done.id); }, assessment.teeth)}>Salvează și finalizează</button>
      </div>}
      {assessment.status === 'finalized' && <div className="periodontal-history">{assessment.teeth.map(row => <article key={row.tooth_fdi}><strong>FDI {row.tooth_fdi}</strong>{row.is_excluded ? <span> Exclus</span> : <><span> · Mobilitate {row.mobility ?? '—'} · Furcație {row.furcation ?? '—'}</span><div className="periodontal-readings">{row.sites.map(site => <span key={site.site}>{labels[site.site]}: {site.probing_depth_mm ?? '—'} / {site.recession_mm ?? '—'} mm; BOP {site.bop === null ? '—' : site.bop ? 'Da' : 'Nu'}</span>)}</div></>}</article>)}</div>}
      {assessment.status === 'finalized' && <><button className="secondary" onClick={() => void run(async () => { const next = await createAssessment(patientId, { clinical_date: clinicalDate, previous_assessment_id: assessment.id, relationship_kind: 'follow_up' }); await refresh(next.id); })}>Creează reevaluare ulterioară</button><div className="periodontal-correction"><label>Motivul corecției<input value={correctionReason} onChange={event => setCorrectionReason(event.target.value)} /></label><button className="secondary" disabled={!correctionReason.trim() || !clinicalDate || busy} onClick={() => void run(async () => { const next = await createAssessment(patientId, { clinical_date: clinicalDate, previous_assessment_id: assessment.id, relationship_kind: 'correction', change_reason: correctionReason.trim() }); setCorrectionReason(''); await refresh(next.id); })}>Creează corecție legată</button></div></>}
    </>}
    {history.filter(row => row.status === 'finalized').length >= 2 && <div className="periodontal-compare"><label>Prima evaluare<select value={compareFirst} onChange={e => setCompareFirst(e.target.value)}><option value="">Alege</option>{history.filter(x => x.status === 'finalized').map(x => <option key={x.id} value={x.id}>{x.clinical_date} · {x.author_name}</option>)}</select></label><label>A doua evaluare<select value={compareSecond} onChange={e => setCompareSecond(e.target.value)}><option value="">Alege</option>{history.filter(x => x.status === 'finalized').map(x => <option key={x.id} value={x.id}>{x.clinical_date} · {x.author_name}</option>)}</select></label><button className="secondary" disabled={!compareFirst || !compareSecond || busy} onClick={() => void run(async () => setComparison(await compareAssessments(patientId, compareFirst, compareSecond)))}>Compară</button>{comparison !== null && <div className="periodontal-comparison-results"><p>{comparison.first.clinical_date} → {comparison.second.clinical_date}</p>{comparison.teeth.map(row => <article key={row.tooth_fdi}><strong>FDI {row.tooth_fdi}</strong><p>Mobilitate: {row.mobility.first ?? '—'} → {row.mobility.second ?? '—'} (schimbare {row.mobility.changed === null ? '—' : row.mobility.changed ? 'da' : 'nu'}); furcație: {row.furcation.first ?? '—'} → {row.furcation.second ?? '—'} (schimbare {row.furcation.changed === null ? '—' : row.furcation.changed ? 'da' : 'nu'})</p>{row.sites.map(site => <div className="periodontal-comparison-site" key={site.site}><strong>{labels[site.site]}</strong>{fields.map(([key, label]) => <span key={key}>{label}: {display(site.first?.[key] ?? null)} → {display(site.second?.[key] ?? null)} · Δ {display(site.differences[key])}</span>)}</div>)}<p>BOP a doua evaluare: {row.second_bop_positive}/{row.second_bop_known_denominator} situri cunoscute · {row.second_bop_percent === null ? 'indisponibil' : `${row.second_bop_percent.toFixed(1)}%`}</p></article>)}</div>}</div>}
  </section>;
}
