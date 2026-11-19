import TestRenderer, { act } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AxiosError } from 'axios';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as documentsApi from './api';
import type { ClinicalDocument, DocumentLimits } from './api';
import { DocumentsPanel, precheckFile } from './DocumentsPanel';

const limits: DocumentLimits = {
  accepted_types: ['image/jpeg', 'image/png', 'application/pdf'], accepted_extensions: ['.jpg', '.jpeg', '.png', '.pdf'],
  max_bytes: 20 * 1024 * 1024, max_image_side: 12000, max_image_pixels: 50_000_000,
  categories: ['photo', 'radiograph', 'document', 'other'], image_only_categories: ['photo', 'radiograph'],
  not_supported: ['DICOM'], antivirus_scan: false,
};
function doc(overrides: Partial<ClinicalDocument> = {}): ClinicalDocument {
  return {
    id: 'doc-1', patient_id: 'patient-a', category: 'radiograph', description: null, original_filename: 'rx-16.png',
    content_type: 'image/png', size_bytes: 2048, width: 800, height: 600, is_image: true, tooth_fdi: '16',
    appointment: { id: 'visit-a', starts_at: '2026-12-01T09:00:00Z', appointment_type: 'Control', status: 'scheduled' },
    clinical_date: '2026-11-18', uploaded_at: '2026-11-19T08:00:00Z', uploaded_by: { id: 'u1', name: 'Dr. Fictiv' },
    status: 'active', archived_at: null, archived_by: null, archive_reason: null, sha256: 'x', ...overrides,
  };
}

function text(node: TestRenderer.ReactTestInstance): string {
  return node.children.map(child => typeof child === 'string' ? child : text(child)).join('');
}
async function flush() { await new Promise(resolve => setTimeout(resolve, 20)); }
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((ok, fail) => { resolve = ok; reject = fail; });
  return { promise, resolve, reject };
}
function client() { return new QueryClient({ defaultOptions: { queries: { retry: false } } }); }
function tree(qc: QueryClient, patientId: string) {
  return <QueryClientProvider client={qc}><DocumentsPanel key={`${patientId}:documents`} patientId={patientId} timezone="Europe/Bucharest" /></QueryClientProvider>;
}
async function render(qc = client(), patientId = 'patient-a') {
  let renderer!: TestRenderer.ReactTestRenderer;
  await act(async () => { renderer = TestRenderer.create(tree(qc, patientId)); await flush(); });
  return renderer;
}
function byLabel(r: TestRenderer.ReactTestRenderer, label: string) { return r.root.findByProps({ 'aria-label': label }); }
function button(r: TestRenderer.ReactTestRenderer, label: string) {
  const found = r.root.findAllByType('button').find(node => text(node).trim() === label);
  if (!found) throw new Error(`Button not found: ${label}`);
  return found;
}
function pickFile(r: TestRenderer.ReactTestRenderer, file: File) {
  act(() => byLabel(r, 'Fișier').props.onChange({ target: { files: [file] } }));
}
function select(r: TestRenderer.ReactTestRenderer, label: string, value: string) {
  act(() => byLabel(r, label).props.onChange({ target: { value } }));
}
async function submit(r: TestRenderer.ReactTestRenderer) {
  await act(async () => { byLabel(r, 'Încarcă document').props.onSubmit({ preventDefault() {} }); await flush(); });
}
function axiosError(status: number, detail: string) {
  return new AxiosError(detail, String(status), undefined, undefined, { status, data: { detail }, statusText: '', headers: {}, config: {} as never });
}
const png = () => new File([new Uint8Array([137, 80, 78, 71])], 'intraoral.png', { type: 'image/png' });

beforeEach(() => {
  vi.spyOn(documentsApi, 'getDocumentLimits').mockResolvedValue(limits);
  vi.spyOn(documentsApi, 'listVisitOptions').mockImplementation(async patientId => patientId === 'patient-a'
    ? [{ id: 'visit-a', starts_at: '2026-12-01T09:00:00Z', appointment_type: 'Control', status: 'scheduled' }]
    : [{ id: 'visit-b', starts_at: '2026-12-02T09:00:00Z', appointment_type: 'Tratament', status: 'scheduled' }]);
  vi.spyOn(documentsApi, 'listPatientDocuments').mockResolvedValue([]);
});
afterEach(() => vi.restoreAllMocks());

describe('DocumentsPanel', () => {
  it('explains accepted formats and limits in the UI', async () => {
    const r = await render();
    const rules = text(r.root.findByProps({ 'data-testid': 'document-rules' }));
    expect(rules).toContain('JPEG și PNG');
    expect(rules).toContain('PDF');
    expect(rules).toContain('20.0 MiB');
    expect(rules).toContain('12000 px');
    expect(rules).toContain('DICOM');
    expect(rules).toContain('Nu se face scanare antivirus');
  });

  it('saves once even when submitted repeatedly and sends visit/tooth/category', async () => {
    const pending = deferred<ClinicalDocument>();
    const upload = vi.spyOn(documentsApi, 'uploadDocument').mockReturnValue(pending.promise);
    const r = await render();
    pickFile(r, png());
    select(r, 'Categorie', 'radiograph');
    select(r, 'Vizită', 'visit-a');
    select(r, 'Dinte', '16');
    await submit(r);
    await submit(r);
    await submit(r);
    expect(upload).toHaveBeenCalledTimes(1);
    const [patientId, input, key] = upload.mock.calls[0];
    expect(patientId).toBe('patient-a');
    expect(input).toMatchObject({ fileName: 'intraoral.png', category: 'radiograph', appointmentId: 'visit-a', toothFdi: '16' });
    expect(key).toMatch(/^[0-9a-f-]{36}$/);
    expect(button(r, 'Se salvează…').props.disabled).toBe(true);
    await act(async () => { pending.resolve(doc()); await flush(); });
    expect(r.root.findAllByProps({ 'data-testid': 'selected-file' })).toHaveLength(0);
    expect(byLabel(r, 'Dinte').props.value).toBe('');
    expect(documentsApi.listPatientDocuments).toHaveBeenCalledTimes(2); // invalidated after success
  });

  it('keeps the form and the same retry key after a server error', async () => {
    const upload = vi.spyOn(documentsApi, 'uploadDocument')
      .mockRejectedValueOnce(axiosError(415, 'Unsupported file type: only JPEG, PNG and PDF are accepted'))
      .mockResolvedValueOnce(doc());
    const r = await render();
    pickFile(r, png());
    select(r, 'Categorie', 'radiograph');
    select(r, 'Dinte', '21');
    act(() => byLabel(r, 'Descriere').props.onChange({ target: { value: 'Control fictiv' } }));
    await submit(r);
    expect(text(r.root.findByProps({ 'data-testid': 'document-form-error' }))).toContain('Unsupported file type');
    expect(text(r.root.findByProps({ 'data-testid': 'selected-file' }))).toContain('intraoral.png');
    expect(byLabel(r, 'Categorie').props.value).toBe('radiograph');
    expect(byLabel(r, 'Dinte').props.value).toBe('21');
    expect(byLabel(r, 'Descriere').props.value).toBe('Control fictiv');
    expect(button(r, 'Salvează în dosar').props.disabled).toBe(false);
    await submit(r);
    expect(upload).toHaveBeenCalledTimes(2);
    expect(upload.mock.calls[1][2]).toBe(upload.mock.calls[0][2]);
  });

  it('rejects oversize and unsupported files before uploading', () => {
    expect(precheckFile({ name: 'a.svg', size: 10 }, 'photo', limits)).toContain('Format neacceptat');
    expect(precheckFile({ name: 'a.png', size: limits.max_bytes + 1 }, 'photo', limits)).toContain('limita');
    expect(precheckFile({ name: 'a.pdf', size: 10 }, 'radiograph', limits)).toContain('JPEG sau PNG');
    expect(precheckFile({ name: 'a.PDF', size: 10 }, 'document', limits)).toBeNull();
  });

  it('does not carry the file, fields or a late response over to another patient', async () => {
    const pending = deferred<ClinicalDocument>();
    const upload = vi.spyOn(documentsApi, 'uploadDocument').mockReturnValue(pending.promise);
    const qc = client();
    const r = await render(qc, 'patient-a');
    pickFile(r, png());
    select(r, 'Vizită', 'visit-a');
    select(r, 'Dinte', '36');
    await submit(r);
    await act(async () => { r.update(tree(qc, 'patient-b')); await flush(); });
    expect(r.root.findAllByProps({ 'data-testid': 'selected-file' })).toHaveLength(0);
    expect(byLabel(r, 'Vizită').props.value).toBe('');
    expect(byLabel(r, 'Dinte').props.value).toBe('');
    expect(button(r, 'Salvează în dosar').props.disabled).toBe(true);
    const visitOptions = byLabel(r, 'Vizită').findAllByType('option').map(option => option.props.value);
    expect(visitOptions).toEqual(['', 'visit-b']);
    await act(async () => { pending.reject(axiosError(500, 'late failure for A')); await flush(); });
    expect(r.root.findAllByProps({ 'data-testid': 'document-form-error' })).toHaveLength(0);
    expect(upload).toHaveBeenCalledTimes(1);
    expect(upload.mock.calls[0][0]).toBe('patient-a');
  });

  it('views an image with zoom and compares two images with synchronized zoom', async () => {
    vi.spyOn(documentsApi, 'listPatientDocuments').mockResolvedValue([
      doc(), doc({ id: 'doc-2', original_filename: 'rx-16-control.png', clinical_date: '2026-11-19' }),
      doc({ id: 'doc-3', original_filename: 'consimtamant.pdf', content_type: 'application/pdf', is_image: false, width: null, height: null, category: 'document' }),
    ]);
    const content = vi.spyOn(documentsApi, 'fetchDocumentContent').mockResolvedValue(new Blob([new Uint8Array([1])], { type: 'image/png' }));
    const created = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:fictiv');
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    const r = await render();
    const viewButtons = r.root.findAllByType('button').filter(node => text(node) === 'Vizualizează');
    expect(viewButtons).toHaveLength(2); // PDFs are downloaded, not rendered
    await act(async () => { viewButtons[0].props.onClick(); await flush(); });
    expect(content).toHaveBeenCalledWith('doc-1');
    expect(created).toHaveBeenCalled();
    const zoomIn = byLabel(r, 'Mărește');
    act(() => zoomIn.props.onClick());
    expect(text(r.root.findByProps({ 'data-testid': 'zoom-level' }))).toBe('125%');
    expect(r.root.findByType('img').props.style.width).toBe(1000);

    act(() => byLabel(r, 'Compară rx-16.png').props.onChange());
    act(() => byLabel(r, 'Compară rx-16-control.png').props.onChange());
    await act(async () => { button(r, 'Compară imaginile').props.onClick(); await flush(); });
    const panes = r.root.findAllByProps({ 'data-testid': 'compare-pane' });
    expect(panes).toHaveLength(2);
    act(() => panes[0].findByProps({ 'aria-label': 'Mărește' }).props.onClick());
    expect(r.root.findAllByProps({ 'data-testid': 'zoom-level' }).map(text)).toEqual(['125%', '125%']);
    act(() => byLabel(r, 'Zoom sincronizat').props.onChange({ target: { checked: false } }));
    act(() => r.root.findAllByProps({ 'data-testid': 'compare-pane' })[1].findByProps({ 'aria-label': 'Mărește' }).props.onClick());
    expect(r.root.findAllByProps({ 'data-testid': 'zoom-level' }).map(text)).toEqual(['100%', '125%']);
  });

  it('requires a reason to archive and keeps traceability visible', async () => {
    vi.spyOn(documentsApi, 'listPatientDocuments').mockResolvedValue([doc()]);
    const archive = vi.spyOn(documentsApi, 'archiveDocument').mockResolvedValue(doc({ status: 'archived', archive_reason: 'Greșit' }));
    const r = await render();
    act(() => button(r, 'Arhivează').props.onClick());
    await act(async () => { button(r, 'Confirmă arhivarea').props.onClick(); await flush(); });
    expect(archive).not.toHaveBeenCalled();
    act(() => byLabel(r, 'Motiv arhivare').props.onChange({ target: { value: 'Greșit' } }));
    await act(async () => { button(r, 'Confirmă arhivarea').props.onClick(); await flush(); });
    expect(archive).toHaveBeenCalledWith('doc-1', 'Greșit');
  });
});
