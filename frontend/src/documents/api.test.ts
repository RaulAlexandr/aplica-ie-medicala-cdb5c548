import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '../api/client';
import { fetchDocumentContent, uploadDocument } from './api';

afterEach(() => vi.restoreAllMocks());

describe('documents api', () => {
  it('streams the raw file with percent-encoded metadata headers and reference query params', async () => {
    const post = vi.spyOn(api, 'post').mockResolvedValue({ data: { id: 'doc-1' } });
    const file = new Blob([new Uint8Array([1, 2, 3])]);
    await uploadDocument('patient-1', { file, fileName: 'rând "1".png', category: 'photo', appointmentId: 'v1', toothFdi: '16', clinicalDate: '2026-11-18', description: ' Notă\nfictivă ' }, 'key-1');
    const [url, body, config] = post.mock.calls[0];
    expect(url).toBe('/documents/patients/patient-1');
    expect(body).toBe(file);
    expect(config?.headers).toEqual({
      'Content-Type': 'application/octet-stream', 'X-File-Name': encodeURIComponent('rând "1".png'),
      'Idempotency-Key': 'key-1', 'X-Document-Description': encodeURIComponent('Notă\nfictivă'),
    });
    expect(config?.params).toEqual({ category: 'photo', appointment_id: 'v1', tooth_fdi: '16', clinical_date: '2026-11-18' });
  });

  it('fetches content as an authorized blob without putting credentials in the URL', async () => {
    const get = vi.spyOn(api, 'get').mockResolvedValue({ data: new Blob() });
    await fetchDocumentContent('doc-1', true);
    expect(get).toHaveBeenCalledWith('/documents/doc-1/content', { params: { download: true }, responseType: 'blob' });
  });
});
