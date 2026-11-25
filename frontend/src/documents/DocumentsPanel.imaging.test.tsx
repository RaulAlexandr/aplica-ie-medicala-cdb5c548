import TestRenderer, { act } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as documentsApi from './api';
import type { ClinicalDocument, DocumentLimits } from './api';
import { DocumentsPanel, precheckFile } from './DocumentsPanel';

const limits: DocumentLimits = {
  accepted_types: ['image/jpeg', 'image/png', 'application/pdf'], accepted_extensions: ['.jpg', '.jpeg', '.png', '.pdf'],
  max_bytes: 20 * 1024 * 1024, max_image_side: 12000, max_image_pixels: 50_000_000, categories: ['photo', 'imaging'], image_only_categories: ['photo'],
  not_supported: [], antivirus_scan: false, imaging_types: ['model/stl', 'application/dicom'], imaging_extensions: ['.stl', '.dcm'], imaging_max_bytes: 100 * 1024 * 1024,
};
const stl: ClinicalDocument = {
  id: 'stl-1', patient_id: 'patient-a', category: 'imaging', description: null, original_filename: 'arcada.stl', content_type: 'model/stl',
  imaging: { kind: 'stl', format: 'binary', triangles: 12 }, size_bytes: 700, width: null, height: null, is_image: false, tooth_fdi: null, appointment: null,
  clinical_date: null, uploaded_at: '2026-11-19T08:00:00Z', uploaded_by: { id: 'u1', name: 'Dr. Fictiv' }, status: 'active', archived_at: null, archived_by: null, archive_reason: null, sha256: 'x',
};
const text = (node: TestRenderer.ReactTestInstance): string => node.children.map(child => typeof child === 'string' ? child : text(child)).join('');
async function flush() { await new Promise(resolve => setTimeout(resolve, 20)); }

beforeEach(() => {
  vi.spyOn(documentsApi, 'getDocumentLimits').mockResolvedValue(limits);
  vi.spyOn(documentsApi, 'listVisitOptions').mockResolvedValue([]);
  vi.spyOn(documentsApi, 'listPatientDocuments').mockResolvedValue([stl]);
});
afterEach(() => vi.restoreAllMocks());

describe('Imaging category in the documents panel', () => {
  it('prechecks STL/DICOM by category, extension and the imaging size limit', () => {
    expect(precheckFile({ name: 'a.stl', size: 10 }, 'imaging', limits)).toBeNull();
    expect(precheckFile({ name: 'a.DCM', size: 10 }, 'imaging', limits)).toBeNull();
    expect(precheckFile({ name: 'a.png', size: 10 }, 'imaging', limits)).toContain('STL');
    expect(precheckFile({ name: 'a.stl', size: 10 }, 'document', limits)).toContain('categoria Imagistică');
    expect(precheckFile({ name: 'a.stl', size: 50 * 1024 * 1024 }, 'imaging', limits)).toBeNull(); // above the T11 limit, below the imaging one
    expect(precheckFile({ name: 'a.stl', size: 101 * 1024 * 1024 }, 'imaging', limits)).toContain('100.0 MiB');
    expect(precheckFile({ name: 'a.stl', size: 0 }, 'imaging', limits)).toContain('gol');
    expect(precheckFile({ name: 'a.png', size: 21 * 1024 * 1024 }, 'photo', limits)).toContain('limita'); // T11 rule unchanged
  });

  it('lists an STL with its type, triangle count and a Vizualizează button that opens the viewer', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => {
      renderer = TestRenderer.create(<QueryClientProvider client={qc}><DocumentsPanel patientId="patient-a" timezone="Europe/Bucharest" /></QueryClientProvider>, { createNodeMock: () => ({ clientWidth: 300, clientHeight: 200, querySelector: () => null, appendChild() {} }) });
      await flush(); await flush();
    });
    const row = renderer.root.findByProps({ 'data-document-id': 'stl-1' });
    expect(text(row)).toContain('STL');
    expect(text(row)).toContain('12 triunghiuri (binar)');
    const view = row.findAllByType('button').find(node => text(node) === 'Vizualizează')!;
    await act(async () => { view.props.onClick(); await flush(); await flush(); await flush(); });
    expect(renderer.root.findAllByProps({ 'aria-label': 'Vizualizator STL' }).length).toBeGreaterThan(0);
    expect(text(renderer.root.findByProps({ 'data-testid': 'document-rules' }))).toContain('100.0 MiB');
  });
});
