// Guard: the 3D engine and the DICOM viewer stay out of the main bundle (real sizes are in the T31 report).
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const source = (name: string) => readFileSync(new URL(`./${name}`, import.meta.url), 'utf8');

describe('lazy imaging viewers', () => {
  it('only StlViewer imports three, and the panel reaches the viewers through dynamic import()', () => {
    for (const file of ['DocumentsPanel.tsx', 'ImagingViewer.tsx', 'DicomViewer.tsx', 'dicomPixels.ts', 'api.ts']) {
      expect(source(file), file).not.toMatch(/from 'three/);
    }
    expect(source('StlViewer.tsx')).toMatch(/from 'three'/);
    const loader = source('ImagingViewer.tsx');
    expect(loader).toContain("lazy(() => import('./StlViewer'))");
    expect(loader).toContain("lazy(() => import('./DicomViewer'))");
    expect(loader).not.toMatch(/^import .*(StlViewer|DicomViewer)/m);
    expect(source('DocumentsPanel.tsx')).not.toMatch(/import .*(StlViewer|DicomViewer)/);
  });
});
