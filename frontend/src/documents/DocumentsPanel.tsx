import { useEffect, useRef, useState, type FormEvent } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { apiErrorMessage } from '../api/client';
import { ADULT_TOOTH_IDS } from '../odontogram/types';
import {
  archiveDocument, fetchDocumentContent, getDocumentLimits, listPatientDocuments, listVisitOptions, uploadDocument,
  type ClinicalDocument, type DocumentCategory, type DocumentLimits,
} from './api';
import './documents.css';

export const CATEGORY_LABELS: Record<DocumentCategory, string> = {
  photo: 'Fotografie clinică', radiograph: 'Radiografie', document: 'Document', other: 'Altele',
};
const EXTENSIONS: Record<string, string[]> = {
  'image/jpeg': ['.jpg', '.jpeg'], 'image/png': ['.png'], 'application/pdf': ['.pdf'],
};
const ACCEPT = '.jpg,.jpeg,.png,.pdf,image/jpeg,image/png,application/pdf';
const MIN_ZOOM = 0.1;
const MAX_ZOOM = 8;

function newKey() { return crypto.randomUUID(); }
export function formatBytes(value: number) {
  if (value >= 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MiB`;
  if (value >= 1024) return `${Math.round(value / 1024)} KiB`;
  return `${value} B`;
}
function formatDateTime(value: string | null | undefined, timezone: string) {
  if (!value) return '—';
  return new Intl.DateTimeFormat('ro-RO', { dateStyle: 'medium', timeStyle: 'short', timeZone: timezone }).format(new Date(value));
}
function extensionOf(name: string) {
  const index = name.lastIndexOf('.');
  return index >= 0 ? name.slice(index).toLowerCase() : '';
}
/** Client-side pre-check only; the server is authoritative on type, size and dimensions. */
export function precheckFile(file: { name: string; size: number }, category: DocumentCategory, limits: DocumentLimits | undefined): string | null {
  const ext = extensionOf(file.name);
  const acceptedExtensions = limits?.accepted_extensions ?? Object.values(EXTENSIONS).flat();
  if (!acceptedExtensions.includes(ext)) return 'Format neacceptat. Sunt acceptate doar JPEG, PNG și PDF.';
  if (limits && file.size > limits.max_bytes) return `Fișierul depășește limita de ${formatBytes(limits.max_bytes)}.`;
  if (file.size === 0) return 'Fișierul este gol.';
  if ((category === 'photo' || category === 'radiograph') && ext === '.pdf') return 'Fotografiile și radiografiile trebuie să fie imagini JPEG sau PNG.';
  return null;
}

type Draft = { category: DocumentCategory; appointmentId: string; tooth: string; clinicalDate: string; description: string };
const emptyDraft = (): Draft => ({ category: 'photo', appointmentId: '', tooth: '', clinicalDate: '', description: '' });

export function DocumentsPanel({ patientId, timezone }: { patientId: string; timezone: string }) {
  const queryClient = useQueryClient();
  const [showArchived, setShowArchived] = useState(false);
  const limits = useQuery({ queryKey: ['document-limits'], queryFn: getDocumentLimits, staleTime: 5 * 60_000 });
  const documents = useQuery({ queryKey: ['patient-documents', patientId, showArchived], queryFn: () => listPatientDocuments(patientId, showArchived) });
  const visits = useQuery({ queryKey: ['document-visit-options', patientId], queryFn: () => listVisitOptions(patientId) });

  const [file, setFile] = useState<File | null>(null);
  const [fileInputKey, setFileInputKey] = useState(0);
  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const [idempotencyKey, setIdempotencyKey] = useState(newKey);
  const [saving, setSaving] = useState(false);
  const savingRef = useRef(false);
  const [formError, setFormError] = useState('');
  const [notice, setNotice] = useState('');
  const mountedPatient = useRef<string | null>(patientId);
  useEffect(() => { mountedPatient.current = patientId; return () => { mountedPatient.current = null; }; }, [patientId]);

  const [filterCategory, setFilterCategory] = useState('');
  const [filterVisit, setFilterVisit] = useState('');
  const [filterTooth, setFilterTooth] = useState('');
  const [viewing, setViewing] = useState<ClinicalDocument | null>(null);
  const [compare, setCompare] = useState<string[]>([]);
  const [comparing, setComparing] = useState(false);
  const [archiving, setArchiving] = useState<string | null>(null);
  const [archiveReason, setArchiveReason] = useState('');
  const [archiveError, setArchiveError] = useState('');

  const update = (patch: Partial<Draft>) => { setDraft(value => ({ ...value, ...patch })); setFormError(''); setNotice(''); };
  const chooseFile = (next: File | null) => {
    setFile(next); setFormError(''); setNotice('');
    setIdempotencyKey(newKey()); // a different file is a different upload
  };

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (savingRef.current) return;
    if (!file) { setFormError('Alege un fișier.'); return; }
    const problem = precheckFile(file, draft.category, limits.data);
    if (problem) { setFormError(problem); return; }
    savingRef.current = true;
    setSaving(true);
    setFormError('');
    const forPatient = patientId;
    try {
      await uploadDocument(forPatient, {
        file, fileName: file.name, category: draft.category, appointmentId: draft.appointmentId || undefined,
        toothFdi: draft.tooth || undefined, clinicalDate: draft.clinicalDate || undefined, description: draft.description,
      }, idempotencyKey);
      if (mountedPatient.current !== forPatient) return; // patient changed meanwhile: never carry state over
      setFile(null); setFileInputKey(value => value + 1); setDraft(emptyDraft()); setIdempotencyKey(newKey());
      setNotice('Fișier salvat în dosarul pacientului.');
      await queryClient.invalidateQueries({ queryKey: ['patient-documents', forPatient] });
    } catch (error) {
      if (mountedPatient.current !== forPatient) return;
      setFormError(apiErrorMessage(error)); // keep file and fields for correction or retry
    } finally {
      savingRef.current = false;
      if (mountedPatient.current === forPatient) setSaving(false);
    }
  }

  async function archive(document: ClinicalDocument) {
    if (!archiveReason.trim()) { setArchiveError('Motivul este obligatoriu.'); return; }
    try {
      await archiveDocument(document.id, archiveReason.trim());
      setArchiving(null); setArchiveReason(''); setArchiveError('');
      setCompare(value => value.filter(id => id !== document.id));
      if (viewing?.id === document.id) setViewing(null);
      await queryClient.invalidateQueries({ queryKey: ['patient-documents', patientId] });
    } catch (error) {
      setArchiveError(apiErrorMessage(error));
    }
  }

  async function download(document: ClinicalDocument) {
    try {
      const blob = await fetchDocumentContent(document.id, true);
      const url = URL.createObjectURL(blob);
      const anchor = window.document.createElement('a');
      anchor.href = url; anchor.download = document.original_filename; anchor.rel = 'noopener';
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      setNotice(''); setFormError(`Descărcare eșuată: ${apiErrorMessage(error)}`);
    }
  }

  const toggleCompare = (id: string) => setCompare(value => value.includes(id) ? value.filter(item => item !== id) : value.length >= 2 ? [value[1], id] : [...value, id]);
  const rows = (documents.data ?? []).filter(row =>
    (!filterCategory || row.category === filterCategory)
    && (!filterVisit || (filterVisit === 'none' ? !row.appointment : row.appointment?.id === filterVisit))
    && (!filterTooth || row.tooth_fdi === filterTooth));
  const compareDocs = compare.map(id => documents.data?.find(row => row.id === id)).filter((row): row is ClinicalDocument => Boolean(row?.is_image));
  const maxLabel = limits.data ? formatBytes(limits.data.max_bytes) : '20.0 MiB';
  const sideLabel = limits.data ? `${limits.data.max_image_side} px` : '';

  return <section className="card clinical-record-section doc-panel" aria-label="Documente și imagini">
    <div className="detail-heading"><div><p className="eyebrow">T11 · ACCES PROTEJAT</p><h2>Documente și imagini</h2></div></div>
    <p className="muted doc-rules" data-testid="document-rules">
      Formate acceptate: JPEG și PNG (fotografii, radiografii) și PDF (documente). Maximum {maxLabel} per fișier{sideLabel ? `; imagini de cel mult ${sideLabel} pe latură` : ''}.
      DICOM, SVG, HTML și PDF-uri cu JavaScript sau fișiere atașate sunt respinse. Nu se face scanare antivirus.
      PDF-urile se descarcă, nu sunt afișate în aplicație. Vizualizarea nu este interpretare diagnostică; pilot cu date fictive.
    </p>

    <form className="doc-form" onSubmit={event => void submit(event)} aria-label="Încarcă document">
      <label>Fișier<input key={fileInputKey} aria-label="Fișier" type="file" accept={ACCEPT} disabled={saving}
        onChange={event => chooseFile(event.target.files?.[0] ?? null)} /></label>
      {file && <p className="muted" data-testid="selected-file">Selectat: {file.name} · {formatBytes(file.size)}</p>}
      <div className="doc-grid">
        <label>Categorie<select aria-label="Categorie" value={draft.category} disabled={saving} onChange={event => update({ category: event.target.value as DocumentCategory })}>
          {(Object.keys(CATEGORY_LABELS) as DocumentCategory[]).map(key => <option key={key} value={key}>{CATEGORY_LABELS[key]}</option>)}
        </select></label>
        <label>Vizită (opțional)<select aria-label="Vizită" value={draft.appointmentId} disabled={saving} onChange={event => update({ appointmentId: event.target.value })}>
          <option value="">Fără vizită</option>
          {visits.data?.map(visit => <option key={visit.id} value={visit.id}>{formatDateTime(visit.starts_at, timezone)} · {visit.appointment_type} · {visit.status}</option>)}
        </select></label>
        <label>Dinte FDI (opțional)<select aria-label="Dinte" value={draft.tooth} disabled={saving} onChange={event => update({ tooth: event.target.value })}>
          <option value="">Fără dinte</option>
          {ADULT_TOOTH_IDS.map(tooth => <option key={tooth} value={tooth}>{tooth}</option>)}
        </select></label>
        <label>Data clinică (opțional, introdusă de utilizator)<input aria-label="Data clinică" type="date" value={draft.clinicalDate} disabled={saving} onChange={event => update({ clinicalDate: event.target.value })} /></label>
      </div>
      <label>Descriere (opțional)<input aria-label="Descriere" maxLength={500} value={draft.description} disabled={saving} onChange={event => update({ description: event.target.value })} /></label>
      {visits.isError && <p className="error" role="alert">Vizitele nu au putut fi încărcate: {apiErrorMessage(visits.error)}</p>}
      {formError && <p className="error" role="alert" data-testid="document-form-error">{formError}</p>}
      {notice && <p className="notice-text" role="status">{notice}</p>}
      <div className="clinical-actions"><button type="submit" disabled={saving || !file}>{saving ? 'Se salvează…' : 'Salvează în dosar'}</button></div>
    </form>

    <div className="clinical-filters doc-filters">
      <label>Categorie<select aria-label="Filtru categorie" value={filterCategory} onChange={event => setFilterCategory(event.target.value)}>
        <option value="">Toate</option>{(Object.keys(CATEGORY_LABELS) as DocumentCategory[]).map(key => <option key={key} value={key}>{CATEGORY_LABELS[key]}</option>)}
      </select></label>
      <label>Vizită<select aria-label="Filtru vizită" value={filterVisit} onChange={event => setFilterVisit(event.target.value)}>
        <option value="">Toate</option><option value="none">Fără vizită</option>
        {visits.data?.map(visit => <option key={visit.id} value={visit.id}>{formatDateTime(visit.starts_at, timezone)}</option>)}
      </select></label>
      <label>Dinte<select aria-label="Filtru dinte" value={filterTooth} onChange={event => setFilterTooth(event.target.value)}>
        <option value="">Toți</option>{ADULT_TOOTH_IDS.map(tooth => <option key={tooth} value={tooth}>{tooth}</option>)}
      </select></label>
      <label className="clinical-checkbox"><input type="checkbox" aria-label="Arată arhivate" checked={showArchived} onChange={event => setShowArchived(event.target.checked)} />Arată arhivate</label>
    </div>

    {compare.length > 0 && <div className="clinical-actions">
      <span className="muted">{compare.length}/2 imagini selectate pentru comparație</span>
      <button type="button" className="secondary" disabled={compareDocs.length !== 2} onClick={() => { setComparing(true); setViewing(null); }}>Compară imaginile</button>
      <button type="button" className="secondary" onClick={() => { setCompare([]); setComparing(false); }}>Golește selecția</button>
    </div>}

    {documents.isLoading && <p className="muted">Se încarcă documentele…</p>}
    {documents.isError && <p className="error" role="alert">{apiErrorMessage(documents.error)}</p>}
    {documents.data && rows.length === 0 && <p className="muted">Niciun document pentru filtrele alese.</p>}
    <div className="clinical-list" data-testid="document-list">{rows.map(row => <article key={row.id} className={`clinical-list-item doc-item${row.status === 'archived' ? ' doc-archived' : ''}`} data-document-id={row.id}>
      <div className="detail-heading">
        <div>
          <p className="eyebrow">{CATEGORY_LABELS[row.category]}{row.status === 'archived' ? ' · ARHIVAT' : ''}</p>
          <h3 className="doc-name">{row.original_filename}</h3>
        </div>
        <span className="tp-badge">{row.content_type === 'application/pdf' ? 'PDF' : row.content_type === 'image/png' ? 'PNG' : 'JPEG'}</span>
      </div>
      {row.description && <p className="clinical-note-text">{row.description}</p>}
      <p className="muted">
        {formatBytes(row.size_bytes)}{row.width && row.height ? ` · ${row.width}×${row.height} px` : ''}
        {' · '}{row.tooth_fdi ? `Dinte ${row.tooth_fdi}` : 'Fără dinte'}
        {' · '}{row.appointment ? `Vizita ${formatDateTime(row.appointment.starts_at, timezone)} (${row.appointment.appointment_type})` : 'Fără vizită'}
      </p>
      <p className="muted">
        Data clinică: {row.clinical_date ?? 'nespecificată'} · Înregistrat {formatDateTime(row.uploaded_at, timezone)} de {row.uploaded_by.name}
      </p>
      {row.status === 'archived' && <p className="muted">Arhivat {formatDateTime(row.archived_at, timezone)} de {row.archived_by ?? '—'}: {row.archive_reason}</p>}
      <div className="clinical-actions">
        {row.is_image && <button type="button" className="secondary" onClick={() => { setViewing(row); setComparing(false); }}>Vizualizează</button>}
        <button type="button" className="secondary" onClick={() => void download(row)}>Descarcă</button>
        {row.is_image && <label className="clinical-checkbox"><input type="checkbox" aria-label={`Compară ${row.original_filename}`} checked={compare.includes(row.id)} onChange={() => toggleCompare(row.id)} />Compară</label>}
        {row.status === 'active' && archiving !== row.id && <button type="button" className="secondary" onClick={() => { setArchiving(row.id); setArchiveReason(''); setArchiveError(''); }}>Arhivează</button>}
      </div>
      {archiving === row.id && <div className="tp-inline">
        <input aria-label="Motiv arhivare" placeholder="Motivul arhivării" maxLength={500} value={archiveReason} onChange={event => setArchiveReason(event.target.value)} />
        <button type="button" className="secondary" onClick={() => void archive(row)}>Confirmă arhivarea</button>
        <button type="button" className="secondary" onClick={() => setArchiving(null)}>Renunță</button>
        {archiveError && <p className="error" role="alert">{archiveError}</p>}
      </div>}
    </article>)}</div>

    {viewing && !comparing && <div className="doc-viewer" aria-label="Vizualizare imagine">
      <div className="detail-heading"><h3>{viewing.original_filename}</h3><button type="button" className="secondary" onClick={() => setViewing(null)}>Închide</button></div>
      <ZoomableImage document={viewing} />
    </div>}
    {comparing && compareDocs.length === 2 && <CompareView documents={compareDocs} timezone={timezone} onClose={() => setComparing(false)} />}
  </section>;
}

function useObjectUrl(documentId: string) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let cancelled = false;
    let created: string | null = null;
    setUrl(null); setError('');
    fetchDocumentContent(documentId).then(blob => {
      if (cancelled) return;
      created = URL.createObjectURL(blob);
      setUrl(created);
    }).catch(err => { if (!cancelled) setError(apiErrorMessage(err)); });
    return () => { cancelled = true; if (created) URL.revokeObjectURL(created); };
  }, [documentId]);
  return { url, error };
}

function clampZoom(value: number) { return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, Math.round(value * 100) / 100)); }

export function ZoomableImage({ document, zoom: controlledZoom, onZoom }: { document: ClinicalDocument; zoom?: number; onZoom?: (zoom: number) => void }) {
  const { url, error } = useObjectUrl(document.id);
  const [ownZoom, setOwnZoom] = useState(1);
  const zoom = controlledZoom ?? ownZoom;
  const setZoom = (value: number) => { const next = clampZoom(value); if (onZoom) onZoom(next); else setOwnZoom(next); };
  const stage = useRef<HTMLDivElement>(null);
  const fit = () => {
    const width = stage.current?.clientWidth ?? 0;
    setZoom(width && document.width ? Math.min(1, (width - 8) / document.width) : 1);
  };
  const width = document.width ? Math.round(document.width * zoom) : undefined;
  return <div className="doc-zoom">
    <div className="tp-actions doc-zoom-controls">
      <button type="button" className="secondary" aria-label="Micșorează" onClick={() => setZoom(zoom / 1.25)} disabled={zoom <= MIN_ZOOM}>−</button>
      <span data-testid="zoom-level">{Math.round(zoom * 100)}%</span>
      <button type="button" className="secondary" aria-label="Mărește" onClick={() => setZoom(zoom * 1.25)} disabled={zoom >= MAX_ZOOM}>+</button>
      <button type="button" className="secondary" onClick={() => setZoom(1)}>100%</button>
      <button type="button" className="secondary" onClick={fit}>Potrivește</button>
    </div>
    <div className="doc-stage" ref={stage}>
      {error && <p className="error" role="alert">{error}</p>}
      {!url && !error && <p className="muted">Se încarcă imaginea…</p>}
      {url && <img src={url} alt={document.original_filename} style={{ width, maxWidth: 'none' }} draggable={false} />}
    </div>
  </div>;
}

function CompareView({ documents, timezone, onClose }: { documents: ClinicalDocument[]; timezone: string; onClose: () => void }) {
  const [sync, setSync] = useState(true);
  const [shared, setShared] = useState(1);
  const [left, setLeft] = useState(1);
  const [right, setRight] = useState(1);
  const zooms = sync ? [shared, shared] : [left, right];
  const setters = sync ? [setShared, setShared] : [setLeft, setRight];
  return <div className="doc-viewer" aria-label="Comparație imagini">
    <div className="detail-heading"><h3>Comparație</h3>
      <div className="tp-actions">
        <label className="clinical-checkbox"><input type="checkbox" aria-label="Zoom sincronizat" checked={sync} onChange={event => {
          if (!event.target.checked) { setLeft(shared); setRight(shared); } else setShared(left);
          setSync(event.target.checked);
        }} />Zoom sincronizat</label>
        <button type="button" className="secondary" onClick={onClose}>Închide comparația</button>
      </div>
    </div>
    <div className="doc-compare">{documents.map((document, index) => <figure key={document.id} data-testid="compare-pane">
      <figcaption><strong>{document.original_filename}</strong><span className="muted"> · {CATEGORY_LABELS[document.category]}{document.tooth_fdi ? ` · Dinte ${document.tooth_fdi}` : ''} · {document.clinical_date ?? formatDateTime(document.uploaded_at, timezone)}</span></figcaption>
      <ZoomableImage document={document} zoom={zooms[index]} onZoom={setters[index]} />
    </figure>)}</div>
  </div>;
}
