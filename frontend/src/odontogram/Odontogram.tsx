import { useId, useState } from 'react';
import type { FormEvent } from 'react';
import { apiErrorMessage } from '../api/client';
import type { ClinicalFinding, FindingDraftInput, OdontogramProps, ToothId, ToothSurface, ToothStatus } from './types';
import { formatFdi } from './types';
import { ToothChart } from './ToothChart';
import { ToothSurfacePicker } from './ToothSurfacePicker';
import './odontogram.css';

const STATUS_LABELS: Record<ToothStatus, string> = { present: 'Prezent', missing: 'Lipsă', unevaluated: 'Neevaluat' };
const formatDate = (value: string, timezone: string) => new Intl.DateTimeFormat('ro-RO', { dateStyle: 'medium', timeStyle: 'short', timeZone: timezone }).format(new Date(value));
type FindingDraft = FindingDraftInput;
type CorrectionDraft = FindingDraftInput & { reason: string };

function FindingCard({
  finding, timezone, pending, editableDraft, finalizable, correctable, draft, correction, onDraftChange, onEditDraft,
  onSaveDraft, onFinalize, onCorrectionChange, onCorrect,
}: {
  finding: ClinicalFinding;
  timezone: string;
  pending: boolean;
  editableDraft: boolean;
  finalizable: boolean;
  correctable: boolean;
  draft?: FindingDraft;
  correction?: CorrectionDraft;
  onDraftChange: (draft: FindingDraft | undefined) => void;
  onEditDraft: () => void;
  onSaveDraft: () => void;
  onFinalize: () => void;
  onCorrectionChange: (draft: CorrectionDraft | undefined) => void;
  onCorrect: () => void;
}) {
  return <article className="og-finding" data-finding-id={finding.id}>
    <div className="og-finding-top"><div><span className="og-finding-kind">{finding.kind}</span><span className={`clinical-status${finding.status === 'finalized' ? ' complete' : ''}`}>{finding.status === 'draft' ? 'Ciornă' : 'Finalizată'}</span><p>{finding.description}</p></div>
      {correctable && <button type="button" className="og-text-button" disabled={pending} onClick={() => onCorrectionChange(correction ? undefined : { kind: finding.kind, description: finding.description, surfaces: [...finding.surfaces], reason: '' })}>{correction ? 'Renunță la corecție' : 'Corectează constatarea'}</button>}</div>
    {editableDraft && <div className="clinical-actions"><button type="button" className="secondary" disabled={pending} onClick={onEditDraft}>{draft ? 'Renunță la editare' : 'Editează ciorna'}</button>
      {finalizable && <button type="button" disabled={pending} onClick={onFinalize}>{pending ? 'Se confirmă…' : 'Finalizează explicit'}</button>}</div>}
    {draft && <form className="og-correction-form" data-draft-id={finding.id} onSubmit={(event) => { event.preventDefault(); onSaveDraft(); }}>
      <label>Tipul constatării<input value={draft.kind} disabled={pending} onChange={(event) => onDraftChange({ ...draft, kind: event.target.value })} required /></label>
      <label>Descriere<textarea aria-label={`Descriere ciornă ${finding.id}`} value={draft.description} disabled={pending} onChange={(event) => onDraftChange({ ...draft, description: event.target.value })} required rows={2} /></label>
      <label>Suprafețe (separate prin virgulă)<input aria-label={`Suprafețe ciornă ${finding.id}`} value={draft.surfaces.join(', ')} disabled={pending} onChange={(event) => onDraftChange({ ...draft, surfaces: event.target.value.split(',').map((item) => item.trim()).filter(Boolean) as ToothSurface[] })} /></label>
      <button type="submit" className="og-text-button" disabled={pending || !draft.kind.trim() || !draft.description.trim()}>{pending ? 'Se confirmă…' : 'Salvează ciorna'}</button>
    </form>}
    {correction && <form className="og-correction-form" data-correction-id={finding.id} onSubmit={(event) => { event.preventDefault(); onCorrect(); }}>
      <label>Descriere corectată<textarea aria-label={`Descriere corectată pentru constatarea ${finding.id}`} value={correction.description} disabled={pending} onChange={(event) => onCorrectionChange({ ...correction, description: event.target.value })} required rows={2} /></label>
      <label>Motivul corecției<textarea aria-label={`Motiv corecție ${finding.id}`} value={correction.reason} disabled={pending} onChange={(event) => onCorrectionChange({ ...correction, reason: event.target.value })} required rows={2} /></label>
      <button className="og-text-button" type="submit" disabled={pending || !correction.description.trim() || !correction.reason.trim() || correction.description.trim() === finding.description}>{pending ? 'Se confirmă…' : 'Salvează corecția'}</button>
    </form>}
    <div className="og-finding-meta"><span>{finding.surfaces.length ? finding.surfaces.join(', ') : 'Fără suprafață asociată'}</span><span>{finding.author} · {formatDate(finding.observedAt, timezone)}</span></div>
  </article>;
}

export function Odontogram(props: OdontogramProps) {
  const { teeth, findings, history, selectedToothId, timezone, canWrite, canEditDraft, canFinalizeDraft, canCorrectFinding,
    onToothSelect, onFindingCreate, onFindingDraftUpdate, onFindingFinalize, onFindingCorrect, onToothStatusChange } = props;
  const formId = useId();
  const tooth = teeth.find((item) => item.id === selectedToothId) ?? { id: selectedToothId, status: 'unevaluated' as const };
  const toothFindings = findings.filter((finding) => finding.toothId === selectedToothId);
  const toothHistory = history.filter((event) => event.toothId === selectedToothId).slice().sort((a, b) => b.occurredAt.localeCompare(a.occurredAt));
  const [drafts, setDrafts] = useState<Partial<Record<ToothId, FindingDraft>>>({});
  const [draftEdits, setDraftEdits] = useState<Record<string, FindingDraft>>({});
  const [correctionDrafts, setCorrectionDrafts] = useState<Record<string, CorrectionDraft>>({});
  const [pending, setPending] = useState(false);
  const [callbackError, setCallbackError] = useState<{ toothId: ToothId; message: string } | null>(null);
  const draft = drafts[selectedToothId] ?? { kind: 'Carie', description: '', surfaces: [] };
  const updateDraft = (update: Partial<FindingDraft>) => setDrafts((current) => ({
    ...current, [selectedToothId]: { ...(current[selectedToothId] ?? { kind: 'Carie', description: '', surfaces: [] }), ...update },
  }));
  const toggleSurface = (surface: ToothSurface) => updateDraft({ surfaces: draft.surfaces.includes(surface) ? draft.surfaces.filter((item) => item !== surface) : [...draft.surfaces, surface] });

  function runCallback(toothId: ToothId, operation: () => void | Promise<void>, onSuccess: () => void, errorMessage: string) {
    setCallbackError(null);
    try {
      const result = operation();
      if (result && typeof result.then === 'function') {
        setPending(true);
        void result.then(onSuccess).catch((error: unknown) => {
          const detail = apiErrorMessage(error);
          setCallbackError({ toothId, message: detail === 'The server returned an error. Please try again.' ? errorMessage : `${errorMessage} ${detail}` });
        }).finally(() => setPending(false));
      } else onSuccess();
    } catch (error) {
      const detail = apiErrorMessage(error);
      setCallbackError({ toothId, message: detail === 'The server returned an error. Please try again.' ? errorMessage : `${errorMessage} ${detail}` });
    }
  }

  function submitFinding(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = draft.description.trim();
    if (!text || pending || !canWrite) return;
    const toothId = selectedToothId;
    runCallback(toothId, () => onFindingCreate(toothId, { kind: draft.kind.trim(), description: text, surfaces: [...draft.surfaces] }),
      () => setDrafts((current) => ({ ...current, [toothId]: { ...draft, description: '', surfaces: [] } })),
      `Salvarea pentru dintele ${formatFdi(toothId)} nu a fost confirmată. Ciorna a fost păstrată; încearcă din nou.`);
  }

  function saveDraft(finding: ClinicalFinding) {
    const edit = draftEdits[finding.id];
    if (!edit || pending) return;
    runCallback(finding.toothId, () => onFindingDraftUpdate(finding.id, edit),
      () => setDraftEdits((current) => { const next = { ...current }; delete next[finding.id]; return next; }),
      `Salvarea ciornei pentru dintele ${formatFdi(finding.toothId)} nu a fost confirmată. Modificările au fost păstrate.`);
  }

  function finalizeDraft(finding: ClinicalFinding) {
    if (pending) return;
    runCallback(finding.toothId, () => onFindingFinalize(finding.id), () => undefined,
      `Finalizarea constatării pentru dintele ${formatFdi(finding.toothId)} nu a fost confirmată.`);
  }

  function correctFinding(finding: ClinicalFinding) {
    const correction = correctionDrafts[finding.id];
    if (!correction || pending) return;
    runCallback(finding.toothId, () => onFindingCorrect(finding.id, correction),
      () => setCorrectionDrafts((current) => { const next = { ...current }; delete next[finding.id]; return next; }),
      `Corecția constatării de pe dintele ${formatFdi(finding.toothId)} nu a fost confirmată. Textul și motivul au fost păstrate.`);
  }

  function changeToothStatus(status: ToothStatus) {
    if (pending || !canWrite) return;
    const toothId = selectedToothId;
    runCallback(toothId, () => onToothStatusChange(toothId, status), () => undefined,
      `Schimbarea stării pentru dintele ${formatFdi(toothId)} nu a fost confirmată. Starea rămâne cea primită de la server.`);
  }

  return <section className="og-shell">
    <header className="og-page-header"><div><p className="og-kicker">DOSAR PACIENT</p><h2>Odontogramă clinică</h2><p className="og-subtitle">Starea dinților și constatările sunt păstrate separat de tratamente și manopere.</p></div></header>
    <div className="og-layout"><ToothChart teeth={teeth} selectedToothId={selectedToothId} onToothSelect={onToothSelect} />
      <aside className="og-detail" aria-labelledby={`${formId}-title`}>
        <div className="og-detail-heading"><div><p className="og-kicker">DINTE SELECTAT</p><h3 id={`${formId}-title`}>{formatFdi(selectedToothId)} <span>· FDI {selectedToothId}</span></h3></div>
          {canWrite ? <label className="og-status-field">Stare<select aria-label={`Starea dintelui ${formatFdi(selectedToothId)}`} value={tooth.status} disabled={pending} onChange={(event) => changeToothStatus(event.target.value as ToothStatus)}>{Object.entries(STATUS_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label> : <span className="clinical-status">{STATUS_LABELS[tooth.status]}</span>}</div>
        {callbackError?.toothId === selectedToothId && <p className="og-callback-error" role="alert">{callbackError.message}</p>}
        {tooth.status === 'missing' && <p className="og-missing-note" role="status">Dinte marcat lipsă. Poate fi selectat pentru consultarea istoricului.</p>}
        <section className="og-detail-section"><div className="og-section-title"><h3>Constatări existente</h3><span className="og-count">{toothFindings.length}</span></div>
          {toothFindings.length ? <div className="og-finding-list">{toothFindings.map((finding) => <FindingCard key={finding.id} finding={finding} pending={pending}
            timezone={timezone} editableDraft={canEditDraft(finding)} finalizable={canFinalizeDraft(finding)} correctable={canCorrectFinding(finding)}
            draft={draftEdits[finding.id]} correction={correctionDrafts[finding.id]}
            onEditDraft={() => setDraftEdits((current) => current[finding.id] ? Object.fromEntries(Object.entries(current).filter(([id]) => id !== finding.id)) : { ...current, [finding.id]: { kind: finding.kind, description: finding.description, surfaces: [...finding.surfaces] } })}
            onDraftChange={(value) => setDraftEdits((current) => value ? { ...current, [finding.id]: value } : current)}
            onSaveDraft={() => saveDraft(finding)} onFinalize={() => finalizeDraft(finding)}
            onCorrectionChange={(value) => setCorrectionDrafts((current) => { const next = { ...current }; if (value) next[finding.id] = value; else delete next[finding.id]; return next; })}
            onCorrect={() => correctFinding(finding)} />)}</div> : <p className="og-empty">Nu există constatări pentru acest dinte.</p>}</section>
        {canWrite && <form className="og-new-finding" onSubmit={submitFinding}><div className="og-section-title"><div><p className="og-kicker">ÎNREGISTRARE NOUĂ</p><h3>Constatare clinică</h3></div></div>
          <label htmlFor={`${formId}-kind`}>Tipul constatării<select id={`${formId}-kind`} value={draft.kind} disabled={pending} onChange={(event) => updateDraft({ kind: event.target.value })}><option>Carie</option><option>Fractură</option><option>Uzură</option><option>Discromie</option><option>Altă constatare</option></select></label>
          <label htmlFor={`${formId}-description`}>Descriere<textarea id={`${formId}-description`} value={draft.description} disabled={pending} onChange={(event) => updateDraft({ description: event.target.value })} placeholder="Descrie observația clinică" rows={2} required /></label>
          <fieldset className="og-surfaces-field" disabled={pending}><legend>Suprafețe asociate <span>(opțional)</span></legend><ToothSurfacePicker toothId={selectedToothId} selected={draft.surfaces} onToggle={toggleSurface} /></fieldset>
          <button className="og-primary-button" type="submit" disabled={pending}>{pending ? 'Se confirmă…' : 'Salvează ciornă'}</button>
        </form>}
        <section className="og-detail-section og-history"><div className="og-section-title"><div><p className="og-kicker">ISTORIC SERVER</p><h3>Istoric</h3></div></div>
          {toothHistory.length ? <ol>{toothHistory.map((event) => <li key={event.id}><span className="og-history-marker"/><div><strong>{event.summary}</strong>{event.previousValue && <p>Anterior: {event.previousValue}</p>}{event.currentValue && <p>Valoare curentă: {event.currentValue}</p>}{event.reason && <p>Motiv: {event.reason}</p>}<small>{event.author} · {formatDate(event.occurredAt, timezone)}</small></div></li>)}</ol> : <p className="og-empty">Nu există evenimente salvate pentru acest dinte.</p>}
        </section>
      </aside>
    </div>
  </section>;
}
