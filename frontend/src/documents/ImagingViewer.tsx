import { Suspense, lazy } from 'react';
import type { ClinicalDocument } from './api';

// The 3D engine and the DICOM viewer are separate chunks, fetched only when someone opens a file.
const StlViewer = lazy(() => import('./StlViewer'));
const DicomViewer = lazy(() => import('./DicomViewer'));

export function ImagingViewer({ document }: { document: ClinicalDocument }) {
  return <Suspense fallback={<p className="muted" role="status">Se încarcă vizualizatorul…</p>}>
    {document.content_type === 'model/stl' ? <StlViewer key={document.id} document={document} /> : <DicomViewer key={document.id} document={document} />}
  </Suspense>;
}
