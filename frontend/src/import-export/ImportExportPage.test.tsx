import TestRenderer, { act } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as importApi from './api';
import { ImportExportPage } from './ImportExportPage';

const flush = () => new Promise(resolve => setTimeout(resolve, 0));
const text = (node: TestRenderer.ReactTestInstance): string => node.children.map(child => typeof child === 'string' ? child : text(child)).join('');

afterEach(() => vi.restoreAllMocks());

describe('ImportExportPage', () => {
  it('requires preview before confirmation and exposes row by row patient review', async () => {
    vi.spyOn(importApi, 'listImportRuns').mockResolvedValue([]);
    const preview = vi.spyOn(importApi, 'previewCsv').mockResolvedValue({
      filename: 'pacienti.csv', sha256: 'hash', delimiter: ';', total: 2, new: 1, existing: 1, errors: 0,
      rows: [
        { row_number: 2, status: 'new', message: null, data: {} },
        { row_number: 3, status: 'review', message: 'De verificat: telefon', data: {} },
      ],
    });
    const confirm = vi.spyOn(importApi, 'confirmCsv').mockResolvedValue({ id: 'run-1', kind: 'patients', filename: 'pacienti.csv', sha256: 'hash', created_at: '2026-11-25T10:00:00Z', created: 1, updated: 1, rows: [] });
    let renderer!: TestRenderer.ReactTestRenderer;
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await act(async () => { renderer = TestRenderer.create(<QueryClientProvider client={client}><ImportExportPage /></QueryClientProvider>); await flush(); });
    expect(renderer.root.findAllByType('button').some(button => text(button).includes('Confirmă importul'))).toBe(false);
    const input = renderer.root.findAllByType('input').find(item => item.props.type === 'file')!;
    const file = new File(['Nume;Prenume\nPop;Ana\n'], 'pacienti.csv', { type: 'text/csv' });
    await act(async () => { input.props.onChange({ target: { files: [file] } }); await flush(); });
    const previewButton = renderer.root.findAllByType('button').find(button => text(button).includes('Previzualizează'))!;
    await act(async () => { previewButton.props.onClick(); await flush(); });
    expect(preview).toHaveBeenCalledWith('patients', file, expect.objectContaining({ last_name: 'Nume', first_name: 'Prenume' }));
    expect(text(renderer.root)).toContain('1 noi');
    const reviewBox = renderer.root.findByProps({ 'aria-label': 'Actualizează rândul 3' });
    act(() => reviewBox.props.onChange());
    const confirmButton = renderer.root.findAllByType('button').find(button => text(button).includes('Confirmă importul'))!;
    await act(async () => { confirmButton.props.onClick(); await flush(); });
    expect(confirm).toHaveBeenCalledWith('patients', file, expect.any(Object), [3]);
    expect(text(renderer.root)).toContain('1 create, 1 actualizate');
    await act(async () => { renderer.unmount(); });
    client.clear();
  });
});
