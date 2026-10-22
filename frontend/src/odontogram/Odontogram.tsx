import { useId, useState } from 'react';
import type { FormEvent } from 'react';
import type { ClinicalFinding, OdontogramProps, ToothId, ToothSurface, ToothStatus } from './types';
import { formatFdi } from './types';
import { ToothChart } from './ToothChart';
import { ToothSurfacePicker } from './ToothSurfacePicker';
import './odontogram.css';

const STATUS_LABELS: Record<ToothStatus, string> = { present: 'Prezent', missing: 'Lipsă', unevaluated: 'Neevaluat' };
const dateLabel = (value: string) => new Intl.DateTimeFormat('ro-RO', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value));

function FindingCard({ finding, onCorrect, pending, correctionDraft, onCorrectionDraftChange }: {
  finding: ClinicalFinding;
  onCorrect: (replacement: Pick<ClinicalFinding, 'kind' | 'description' | 'surfaces'>, onSaved: () => void) => void;
  pending: boolean;
  correctionDraft?: string;
  onCorrectionDraftChange: (findingId: string, description: string | undefined) => void;
}) {
  const editing = correctionDraft !== undefined;
  const description = correctionDraft ?? finding.description;
  return <article className="og-finding" data-finding-id={finding.id}>
    <div className="og-finding-top"><div><span className="og-finding-kind">{finding.kind}</span><p>{finding.description}</p></div>
      <button type="button" className="og-text-button" disabled={pending} onClick={() => onCorrectionDraftChange(finding.id, editing ? undefined : finding.description)}>{editing ? 'Renunță la corecție' : 'Corectează constatarea'}</button></div>
    {editing && <form className="og-correction-form" data-correction-id={finding.id} onSubmit={(event) => {
      event.preventDefault();
      const corrected = description.trim();
      if (!corrected || corrected === finding.description) return;
      onCorrect({ kind: finding.kind, description: corrected, surfaces: [...finding.surfaces] }, () => onCorrectionDraftChange(finding.id, undefined));
    }}>
      <label>Descriere corectată<textarea aria-label={`Descriere corectată pentru constatarea ${finding.id}`} value={description} disabled={pending} onChange={(event) => onCorrectionDraftChange(finding.id, event.target.value)} required rows={2} /></label>
      <button className="og-text-button" type="submit" disabled={pending || !description.trim() || description.trim() === finding.description}>{pending ? 'Se confirmă…' : 'Salvează corecția'}</button>
    </form>}
    <div className="og-finding-meta"><span>{finding.surfaces.length ? finding.surfaces.join(', ') : 'Fără suprafață asociată'}</span><span>{finding.author} · {dateLabel(finding.observedAt)}</span></div>
  </article>;
}

export function Odontogram(props: OdontogramProps) {
  const { teeth, findings, history, selectedToothId, onToothSelect, onFindingCreate, onFindingCorrect, onToothStatusChange } = props;
  const formId = useId();
  const tooth = teeth.find((item) => item.id === selectedToothId) ?? { id: selectedToothId, status: 'unevaluated' as const };
  const toothFindings = findings.filter((finding) => finding.toothId === selectedToothId);
  const toothHistory = history.filter((event) => event.toothId === selectedToothId).slice().sort((a, b) => b.occurredAt.localeCompare(a.occurredAt));
  const [drafts, setDrafts] = useState<Partial<Record<ToothId, { kind: string; description: string; surfaces: ToothSurface[] }>>>({});
  const [correctionDrafts, setCorrectionDrafts] = useState<Record<string, string>>({});
  const [pending, setPending] = useState(false);
  const [callbackError, setCallbackError] = useState('');
  const draft = drafts[selectedToothId] ?? { kind: 'Carie', description: '', surfaces: [] };
  const updateDraft = (update: Partial<typeof draft>) => setDrafts((current) => ({
    ...current,
    [selectedToothId]: { ...(current[selectedToothId] ?? { kind: 'Carie', description: '', surfaces: [] }), ...update },
  }));
  const toggleSurface = (surface: ToothSurface) => updateDraft({ surfaces: draft.surfaces.includes(surface) ? draft.surfaces.filter((item) => item !== surface) : [...draft.surfaces, surface] });

  function runCallback(operation: () => void | Promise<void>, onSuccess: () => void, errorMessage: string) {
    setCallbackError('');
    try {
      const result = operation();
      if (result && typeof result.then === 'function') {
        setPending(true);
        void result.then(onSuccess).catch(() => setCallbackError(errorMessage)).finally(() => setPending(false));
      } else {
        onSuccess();
      }
    } catch {
      setCallbackError(errorMessage);
    }
  }

  function submitFinding(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = draft.description.trim();
    if (!text || pending) return;
    const toothId = selectedToothId;
    runCallback(
      () => onFindingCreate({ toothId, kind: draft.kind, description: text, surfaces: [...draft.surfaces] }),
      () => setDrafts((current) => ({ ...current, [toothId]: { ...draft, description: '', surfaces: [] } })),
      `Salvarea pentru dintele ${formatFdi(toothId)} nu a fost confirmată. Ciorna acelui dinte a fost păstrată; încearcă din nou.`,
    );
  }

  function correctFinding(finding: ClinicalFinding, replacement: Pick<ClinicalFinding, 'kind' | 'description' | 'surfaces'>, onSaved: () => void) {
    if (pending) return;
    runCallback(
      () => onFindingCorrect(finding.id, replacement),
      onSaved,
      `Corecția constatării ${finding.id} de pe dintele ${formatFdi(finding.toothId)} nu a fost confirmată. Valoarea editată a fost păstrată.`,
    );
  }

  function changeToothStatus(status: ToothStatus) {
    if (pending) return;
    runCallback(
      () => onToothStatusChange(selectedToothId, status),
      () => undefined,
      `Schimbarea stării pentru dintele ${formatFdi(selectedToothId)} nu a fost confirmată. Starea afișată rămâne cea primită din date.`,
    );
  }

  return <main className="og-shell">
    <header className="og-page-header"><div><p className="og-kicker">DOSAR CLINIC · DEMO UI</p><h1>Odontogramă clinică</h1><p className="og-subtitle">Constatările și starea fiecărui dinte rămân separate de planul de tratament și de manoperele efectuate.</p></div>
      <span className="og-demo-badge">Demonstrație locală</span></header>
    <div className="og-layout"><ToothChart teeth={teeth} selectedToothId={selectedToothId} onToothSelect={onToothSelect} />
      <aside className="og-detail" aria-labelledby={`${formId}-title`}>
        <div className="og-detail-heading"><div><p className="og-kicker">DINTE SELECTAT</p><h2 id={`${formId}-title`}>{formatFdi(selectedToothId)} <span>· FDI {selectedToothId}</span></h2></div>
          <label className="og-status-field">Stare<select aria-label={`Starea dintelui ${formatFdi(selectedToothId)}`} value={tooth.status} disabled={pending} onChange={(event) => changeToothStatus(event.target.value as ToothStatus)}>{Object.entries(STATUS_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label></div>
        {callbackError && <p className="og-callback-error" role="alert">{callbackError}</p>}
        {tooth.status === 'missing' && <p className="og-missing-note" role="status">Dinte marcat lipsă. Poate fi selectat pentru consultarea istoricului.</p>}
        <section className="og-detail-section"><div className="og-section-title"><h3>Constatări existente</h3><span className="og-count">{toothFindings.length}</span></div>
          {toothFindings.length ? <div className="og-finding-list">{toothFindings.map((finding) => <FindingCard key={finding.id} finding={finding} onCorrect={(replacement, onSaved) => correctFinding(finding, replacement, onSaved)} pending={pending} correctionDraft={correctionDrafts[finding.id]} onCorrectionDraftChange={(findingId, description) => setCorrectionDrafts((current) => {
            const next = { ...current };
            if (description === undefined) delete next[findingId]; else next[findingId] = description;
            return next;
          })} />)}</div> : <p className="og-empty">Nu există constatări demonstrative pentru acest dinte.</p>}</section>
        <form className="og-new-finding" onSubmit={submitFinding}><div className="og-section-title"><div><p className="og-kicker">ÎNREGISTRARE NOUĂ</p><h3>Constatare clinică</h3></div></div>
          <label htmlFor={`${formId}-kind`}>Tipul constatării<select id={`${formId}-kind`} value={draft.kind} disabled={pending} onChange={(event) => updateDraft({ kind: event.target.value })}><option>Carie</option><option>Fractură</option><option>Uzură</option><option>Discromie</option><option>Altă constatare</option></select></label>
          <label htmlFor={`${formId}-description`}>Descriere<textarea id={`${formId}-description`} value={draft.description} disabled={pending} onChange={(event) => updateDraft({ description: event.target.value })} placeholder="Descrie observația clinică" rows={2} required /></label>
          <fieldset className="og-surfaces-field" disabled={pending}><legend>Suprafețe asociate <span>(opțional)</span></legend><ToothSurfacePicker toothId={selectedToothId} selected={draft.surfaces} onToggle={toggleSurface} /></fieldset>
          <button className="og-primary-button" type="submit" disabled={pending}>{pending ? 'Se confirmă…' : 'Adaugă constatarea'}</button>
        </form>
        <section className="og-detail-section og-history"><div className="og-section-title"><div><p className="og-kicker">EVENIMENTE ÎN MEMORIE</p><h3>Istoric demonstrativ</h3></div></div>
          {toothHistory.length ? <ol>{toothHistory.map((event) => <li key={event.id}><span className="og-history-marker"/><div><strong>{event.summary}</strong>{event.previousValue && <p>Anterior: {event.previousValue}</p>}{event.currentValue && <p>Valoare curentă: {event.currentValue}</p>}<small>{event.author} · {dateLabel(event.occurredAt)}</small></div></li>)}</ol> : <p className="og-empty">Nu există evenimente pentru acest dinte.</p>}
          <p className="og-history-note">Istoric păstrat doar în memoria acestei demonstrații; nu este audit persistent.</p></section>
      </aside>
    </div>
  </main>;
}
