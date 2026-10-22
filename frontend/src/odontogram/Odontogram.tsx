import { useId, useState } from 'react';
import type { FormEvent } from 'react';
import type { ClinicalFinding, OdontogramProps, ToothSurface, ToothStatus } from './types';
import { formatFdi } from './types';
import { ToothChart } from './ToothChart';
import { ToothSurfacePicker } from './ToothSurfacePicker';
import './odontogram.css';

const STATUS_LABELS: Record<ToothStatus, string> = { present: 'Prezent', missing: 'Lipsă', unevaluated: 'Neevaluat' };
const dateLabel = (value: string) => new Intl.DateTimeFormat('ro-RO', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value));

function FindingCard({ finding, onCorrect }: { finding: ClinicalFinding; onCorrect: () => void }) {
  return <article className="og-finding" data-finding-id={finding.id}>
    <div className="og-finding-top"><div><span className="og-finding-kind">{finding.kind}</span><p>{finding.description}</p></div>
      <button type="button" className="og-text-button" onClick={onCorrect}>Corectează demonstrativ</button></div>
    <div className="og-finding-meta"><span>{finding.surfaces.length ? finding.surfaces.join(', ') : 'Fără suprafață asociată'}</span><span>{finding.author} · {dateLabel(finding.observedAt)}</span></div>
  </article>;
}

export function Odontogram(props: OdontogramProps) {
  const { teeth, findings, history, selectedToothId, onToothSelect, onFindingCreate, onFindingCorrect, onToothStatusChange } = props;
  const formId = useId();
  const tooth = teeth.find((item) => item.id === selectedToothId) ?? { id: selectedToothId, status: 'unevaluated' as const };
  const toothFindings = findings.filter((finding) => finding.toothId === selectedToothId);
  const toothHistory = history.filter((event) => event.toothId === selectedToothId).slice().sort((a, b) => b.occurredAt.localeCompare(a.occurredAt));
  const [kind, setKind] = useState('Carie');
  const [description, setDescription] = useState('');
  const [surfaces, setSurfaces] = useState<ToothSurface[]>([]);
  const toggleSurface = (surface: ToothSurface) => setSurfaces((current) => current.includes(surface) ? current.filter((item) => item !== surface) : [...current, surface]);

  function submitFinding(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = description.trim();
    if (!text) return;
    onFindingCreate({ toothId: selectedToothId, kind, description: text, surfaces: [...surfaces] });
    setDescription('');
  }

  function correctFinding(finding: ClinicalFinding) {
    onFindingCorrect(finding.id, { kind: finding.kind, description: `${finding.description} (corectat după verificare)`, surfaces: [...finding.surfaces] });
  }

  return <main className="og-shell">
    <header className="og-page-header"><div><p className="og-kicker">DOSAR CLINIC · DEMO UI</p><h1>Odontogramă clinică</h1><p className="og-subtitle">Constatările și starea fiecărui dinte rămân separate de planul de tratament și de manoperele efectuate.</p></div>
      <span className="og-demo-badge">Demonstrație locală</span></header>
    <div className="og-layout"><ToothChart teeth={teeth} selectedToothId={selectedToothId} onToothSelect={onToothSelect} />
      <aside className="og-detail" aria-labelledby={`${formId}-title`}>
        <div className="og-detail-heading"><div><p className="og-kicker">DINTE SELECTAT</p><h2 id={`${formId}-title`}>{formatFdi(selectedToothId)} <span>· FDI {selectedToothId}</span></h2></div>
          <label className="og-status-field">Stare<select aria-label={`Starea dintelui ${formatFdi(selectedToothId)}`} value={tooth.status} onChange={(event) => onToothStatusChange(selectedToothId, event.target.value as ToothStatus)}>{Object.entries(STATUS_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label></div>
        {tooth.status === 'missing' && <p className="og-missing-note" role="status">Dinte marcat lipsă. Poate fi selectat pentru consultarea istoricului.</p>}
        <section className="og-detail-section"><div className="og-section-title"><h3>Constatări existente</h3><span className="og-count">{toothFindings.length}</span></div>
          {toothFindings.length ? <div className="og-finding-list">{toothFindings.map((finding) => <FindingCard key={finding.id} finding={finding} onCorrect={() => correctFinding(finding)} />)}</div> : <p className="og-empty">Nu există constatări demonstrative pentru acest dinte.</p>}</section>
        <form className="og-new-finding" onSubmit={submitFinding}><div className="og-section-title"><div><p className="og-kicker">ÎNREGISTRARE NOUĂ</p><h3>Constatare clinică</h3></div></div>
          <label htmlFor={`${formId}-kind`}>Tipul constatării<select id={`${formId}-kind`} value={kind} onChange={(event) => setKind(event.target.value)}><option>Carie</option><option>Fractură</option><option>Uzură</option><option>Discromie</option><option>Altă constatare</option></select></label>
          <label htmlFor={`${formId}-description`}>Descriere<textarea id={`${formId}-description`} value={description} onChange={(event) => setDescription(event.target.value)} placeholder="Descrie observația clinică" rows={2} required /></label>
          <fieldset className="og-surfaces-field"><legend>Suprafețe asociate <span>(opțional)</span></legend><ToothSurfacePicker toothId={selectedToothId} selected={surfaces} onToggle={toggleSurface} /></fieldset>
          <button className="og-primary-button" type="submit">Adaugă constatarea</button>
        </form>
        <section className="og-detail-section og-history"><div className="og-section-title"><div><p className="og-kicker">EVENIMENTE ÎN MEMORIE</p><h3>Istoric demonstrativ</h3></div></div>
          {toothHistory.length ? <ol>{toothHistory.map((event) => <li key={event.id}><span className="og-history-marker"/><div><strong>{event.summary}</strong>{event.previousValue && <p>Anterior: {event.previousValue}</p>}{event.currentValue && <p>Valoare curentă: {event.currentValue}</p>}<small>{event.author} · {dateLabel(event.occurredAt)}</small></div></li>)}</ol> : <p className="og-empty">Nu există evenimente pentru acest dinte.</p>}
          <p className="og-history-note">Istoric păstrat doar în memoria acestei demonstrații; nu este audit persistent.</p></section>
      </aside>
    </div>
  </main>;
}
