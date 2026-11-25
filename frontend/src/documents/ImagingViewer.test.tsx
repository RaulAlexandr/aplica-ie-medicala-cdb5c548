import TestRenderer, { act } from 'react-test-renderer';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as documentsApi from './api';
import type { ClinicalDocument, DicomMetadata } from './api';
import DicomViewer from './DicomViewer';
import { ImagingViewer } from './ImagingViewer';
import StlViewer from './StlViewer';

const dicomMeta = (over: Partial<DicomMetadata> = {}): DicomMetadata => ({
  kind: 'dicom', modality: 'CT', study_date: '2026-11-05', body_part: 'JAW', study_description: null, rows: 2, columns: 2, frames: 3, bits_allocated: 8, bits_stored: 8,
  samples_per_pixel: 1, photometric: 'MONOCHROME2', pixel_representation: 0, transfer_syntax_uid: '1.2.840.10008.1.2.1', transfer_syntax_name: 'Explicit VR Little Endian',
  window_center: 128, window_width: 256, rescale_slope: null, rescale_intercept: null, pixel_spacing: null, pixel_offset: 0, pixel_length: 12, viewable: true, viewer_message: null, ...over,
});
const doc = (content_type: ClinicalDocument['content_type'], imaging: ClinicalDocument['imaging']): ClinicalDocument => ({
  id: 'doc-1', patient_id: 'p', category: 'imaging', description: null, original_filename: 'fisier', content_type, imaging, size_bytes: 100, width: null, height: null, is_image: false,
  tooth_fdi: null, appointment: null, clinical_date: null, uploaded_at: '2026-11-05T10:00:00Z', uploaded_by: { id: 'u', name: 'Dr. Fictiv' }, status: 'active', archived_at: null, archived_by: null, archive_reason: null, sha256: 'x',
});
const text = (node: TestRenderer.ReactTestInstance): string => node.children.map(child => typeof child === 'string' ? child : text(child)).join('');
const blobOf = (bytes: Uint8Array) => ({ arrayBuffer: async () => bytes.buffer }) as unknown as Blob;
async function render(element: JSX.Element) {
  let renderer!: TestRenderer.ReactTestRenderer;
  await act(async () => { renderer = TestRenderer.create(element, { createNodeMock: () => ({ clientWidth: 300, clientHeight: 200, querySelector: () => null, appendChild() {}, getContext: () => null }) }); await new Promise(resolve => setTimeout(resolve, 30)); });
  return renderer;
}
afterEach(() => vi.restoreAllMocks());

describe('DICOM viewer', () => {
  it('shows the exact unsupported message, the metadata and no error for a format it cannot display', async () => {
    const fetch = vi.spyOn(documentsApi, 'fetchDocumentContent');
    const renderer = await render(<DicomViewer document={doc('application/dicom', dicomMeta({ viewable: false, transfer_syntax_name: 'JPEG 2000 Image Compression', viewer_message: 'Format DICOM nesuportat pentru vizualizare. Fișierul poate fi descărcat.' }))} />);
    expect(text(renderer.root.findByProps({ 'data-testid': 'dicom-unsupported' }))).toBe('Format DICOM nesuportat pentru vizualizare. Fișierul poate fi descărcat.');
    expect(text(renderer.root)).toContain('JPEG 2000');
    expect(renderer.root.findAll(node => node.props.role === 'alert')).toHaveLength(0);
    expect(fetch).not.toHaveBeenCalled();
  });

  it('shows window, level, a frame slider for multi-frame files and the metadata panel', async () => {
    vi.spyOn(documentsApi, 'fetchDocumentContent').mockResolvedValue(blobOf(new Uint8Array(12).map((_v, index) => index * 20)));
    const renderer = await render(<DicomViewer document={doc('application/dicom', dicomMeta())} />);
    const labels = renderer.root.findAll(node => node.type === 'input' && node.props.type === 'range').map(node => node.props['aria-label']);
    expect(labels).toEqual(['Fereastră', 'Nivel', 'Cadru']);
    expect(text(renderer.root.findByProps({ 'data-testid': 'dicom-frame' }))).toBe('1 / 3');
    expect(text(renderer.root.findByProps({ 'aria-label': 'Metadate DICOM' }))).toContain('Modalitate' + 'CT');
    const frame = renderer.root.findByProps({ 'aria-label': 'Cadru' });
    await act(async () => { frame.props.onChange({ target: { value: '2' } }); });
    expect(text(renderer.root.findByProps({ 'data-testid': 'dicom-frame' }))).toBe('3 / 3');
    const width = renderer.root.findByProps({ 'aria-label': 'Fereastră' });
    await act(async () => { width.props.onChange({ target: { value: '100' } }); });
    expect(text(renderer.root.findByProps({ 'data-testid': 'dicom-width' }))).toBe('100');
  });

  it('a single-frame file has no frame slider, and keys zoom the image', async () => {
    vi.spyOn(documentsApi, 'fetchDocumentContent').mockResolvedValue(blobOf(new Uint8Array(4)));
    const renderer = await render(<DicomViewer document={doc('application/dicom', dicomMeta({ frames: 1, pixel_length: 4 }))} />);
    expect(renderer.root.findAll(node => node.props['aria-label'] === 'Cadru' && node.type === 'input')).toHaveLength(0);
    const stage = renderer.root.findByProps({ role: 'application' });
    await act(async () => { stage.props.onKeyDown({ key: '+', preventDefault() {} }); });
    expect(text(renderer.root.findByProps({ 'data-testid': 'dicom-zoom' }))).toBe('125%');
    await act(async () => { stage.props.onKeyDown({ key: '0', preventDefault() {} }); });
    expect(text(renderer.root.findByProps({ 'data-testid': 'dicom-zoom' }))).toBe('100%');
  });
});

describe('STL viewer', () => {
  it('explains instead of failing when WebGL is unavailable', async () => {
    const renderer = await render(<StlViewer document={doc('model/stl', { kind: 'stl', format: 'binary', triangles: 12 })} />);
    const stage = renderer.root.findByProps({ 'data-testid': 'stl-stage' });
    expect(stage.props['data-state']).toBe('error');
    expect(text(stage)).toContain('WebGL');
  });

  it('picks the viewer from the content type, lazily', async () => {
    vi.spyOn(documentsApi, 'fetchDocumentContent').mockResolvedValue(blobOf(new Uint8Array(4)));
    const renderer = await render(<ImagingViewer document={doc('application/dicom', dicomMeta({ viewable: false, viewer_message: 'x' }))} />);
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 50)); });
    expect(renderer.root.findAllByProps({ 'aria-label': 'Vizualizator DICOM' }).length).toBeGreaterThan(0);
  });
});
