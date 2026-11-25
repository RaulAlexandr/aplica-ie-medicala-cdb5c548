import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react';
import { apiErrorMessage } from '../api/client';
import { fetchDocumentContent, type ClinicalDocument, type DicomMetadata } from './api';
import { decodeFrame, defaultWindow, valueRange, windowToRgba } from './dicomPixels';
import './imaging.css';

const MIN_ZOOM = 0.1;
const MAX_ZOOM = 16;
const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value));

export function DicomMetadataPanel({ meta }: { meta: DicomMetadata }) {
  const rows: Array<[string, string]> = [
    ['Modalitate', meta.modality ?? '—'], ['Data studiului', meta.study_date ?? '—'], ['Regiune', meta.body_part ?? '—'],
    ['Descriere', meta.study_description ?? '—'], ['Dimensiuni', meta.rows && meta.columns ? `${meta.columns} × ${meta.rows} px` : '—'],
    ['Număr de cadre', String(meta.frames)], ['Biți alocați', meta.bits_allocated ? String(meta.bits_allocated) : '—'],
    ['Transfer syntax', meta.transfer_syntax_name], ['UID transfer syntax', meta.transfer_syntax_uid],
  ];
  return <dl className="img-meta" aria-label="Metadate DICOM">{rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>;
}

export default function DicomViewer({ document }: { document: ClinicalDocument }) {
  const meta = document.imaging?.kind === 'dicom' ? document.imaging : null;
  const [buffer, setBuffer] = useState<ArrayBuffer | null>(null);
  const [error, setError] = useState('');
  const [frame, setFrame] = useState(0);
  const [center, setCenter] = useState(0);
  const [width, setWidth] = useState(1);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [mode, setMode] = useState<'pan' | 'window'>('pan');
  const canvas = useRef<HTMLCanvasElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; y: number; center: number; width: number; panX: number; panY: number } | null>(null);
  const viewable = Boolean(meta?.viewable);

  useEffect(() => {
    if (!viewable) return undefined;
    let cancelled = false;
    setBuffer(null); setError('');
    fetchDocumentContent(document.id).then(blob => blob.arrayBuffer()).then(data => { if (!cancelled) setBuffer(data); })
      .catch(err => { if (!cancelled) setError(apiErrorMessage(err)); });
    return () => { cancelled = true; };
  }, [document.id, viewable]);

  const values = useMemo(() => {
    if (!buffer || !meta) return null;
    try { return decodeFrame(buffer, meta, frame); } catch (err) { setError(err instanceof Error ? err.message : 'Imaginea nu poate fi afișată'); return null; }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [buffer, meta, frame]);

  const initialised = useRef('');
  useEffect(() => {
    if (!values || !meta || initialised.current === document.id) return;
    initialised.current = document.id;
    const initial = defaultWindow(meta, values);
    setCenter(initial.center); setWidth(initial.width);
  }, [values, meta, document.id]);

  useEffect(() => {
    if (!values || !meta?.rows || !meta.columns || !canvas.current) return;
    const context = canvas.current.getContext('2d');
    if (!context) return;
    const image = context.createImageData(meta.columns, meta.rows);
    image.data.set(windowToRgba(values, center, width, meta.photometric === 'MONOCHROME1'));
    context.putImageData(image, 0, 0);
  }, [values, meta, center, width]);

  if (!meta) return <p className="error" role="alert">Metadatele DICOM lipsesc.</p>;
  const range = values ? valueRange(values) : { min: 0, max: 1 };
  const sliderMin = Math.floor(Math.min(range.min, (meta.window_center ?? 0) - (meta.window_width ?? 0)));
  const sliderMax = Math.ceil(Math.max(range.max, (meta.window_center ?? 0) + (meta.window_width ?? 0)));
  const span = Math.max(1, sliderMax - sliderMin);

  const reset = () => {
    setZoom(1); setPan({ x: 0, y: 0 });
    if (values) { const initial = defaultWindow(meta, values); setCenter(initial.center); setWidth(initial.width); }
  };
  const fit = () => {
    const available = stage.current?.clientWidth ?? 0;
    setZoom(available && meta.columns ? clamp((available - 8) / meta.columns, MIN_ZOOM, MAX_ZOOM) : 1); setPan({ x: 0, y: 0 });
  };
  const zoomBy = (factor: number) => setZoom(value => clamp(Math.round(value * factor * 100) / 100, MIN_ZOOM, MAX_ZOOM));
  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    event.currentTarget.setPointerCapture?.(event.pointerId);
    drag.current = { x: event.clientX, y: event.clientY, center, width, panX: pan.x, panY: pan.y };
  };
  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const start = drag.current; if (!start) return;
    const dx = event.clientX - start.x; const dy = event.clientY - start.y;
    if (mode === 'pan') setPan({ x: start.panX + dx, y: start.panY + dy });
    else { setWidth(Math.max(1, Math.round(start.width + dx * (span / 400)))); setCenter(Math.round(start.center - dy * (span / 400))); }
  };
  const onPointerUp = () => { drag.current = null; };
  const onKey = (event: KeyboardEvent<HTMLDivElement>) => {
    const step = 20;
    const handled = ({
      ArrowLeft: () => setPan(p => ({ ...p, x: p.x + step })), ArrowRight: () => setPan(p => ({ ...p, x: p.x - step })),
      ArrowUp: () => setPan(p => ({ ...p, y: p.y + step })), ArrowDown: () => setPan(p => ({ ...p, y: p.y - step })),
      '+': () => zoomBy(1.25), '=': () => zoomBy(1.25), '-': () => zoomBy(0.8), '0': reset,
      PageUp: () => setFrame(f => clamp(f + 1, 0, meta.frames - 1)), PageDown: () => setFrame(f => clamp(f - 1, 0, meta.frames - 1)),
      ']': () => setFrame(f => clamp(f + 1, 0, meta.frames - 1)), '[': () => setFrame(f => clamp(f - 1, 0, meta.frames - 1)),
    } as Record<string, () => void>)[event.key];
    if (handled) { event.preventDefault(); handled(); }
  };

  if (!viewable) {
    return <div className="img-viewer" aria-label="Vizualizator DICOM">
      <p className="notice-text" role="status" data-testid="dicom-unsupported">{meta.viewer_message ?? 'Format DICOM nesuportat pentru vizualizare. Fișierul poate fi descărcat.'}</p>
      <DicomMetadataPanel meta={meta} />
    </div>;
  }
  return <div className="img-viewer" aria-label="Vizualizator DICOM">
    <div className="img-toolbar" role="toolbar" aria-label="Instrumente DICOM">
      <button type="button" className="secondary" aria-pressed={mode === 'pan'} onClick={() => setMode('pan')}>Deplasare</button>
      <button type="button" className="secondary" aria-pressed={mode === 'window'} onClick={() => setMode('window')}>Fereastră/nivel</button>
      <button type="button" className="secondary" aria-label="Micșorează" onClick={() => zoomBy(0.8)}>−</button>
      <span data-testid="dicom-zoom">{Math.round(zoom * 100)}%</span>
      <button type="button" className="secondary" aria-label="Mărește" onClick={() => zoomBy(1.25)}>+</button>
      <button type="button" className="secondary" onClick={fit}>Potrivește</button>
      <button type="button" className="secondary" onClick={reset}>Resetează</button>
    </div>
    <div className="img-sliders">
      <label>Fereastră (W) <output data-testid="dicom-width">{Math.round(width)}</output><input type="range" aria-label="Fereastră" min={1} max={span * 2} step={1} value={clamp(width, 1, span * 2)} onChange={event => setWidth(Number(event.target.value))} /></label>
      <label>Nivel (L) <output data-testid="dicom-center">{Math.round(center)}</output><input type="range" aria-label="Nivel" min={sliderMin} max={sliderMax} step={1} value={clamp(center, sliderMin, sliderMax)} onChange={event => setCenter(Number(event.target.value))} /></label>
      {meta.frames > 1 && <label>Cadru <output data-testid="dicom-frame">{frame + 1} / {meta.frames}</output><input type="range" aria-label="Cadru" min={0} max={meta.frames - 1} step={1} value={frame} onChange={event => setFrame(Number(event.target.value))} /></label>}
    </div>
    <div className="img-stage" ref={stage} tabIndex={0} role="application" aria-label="Imagine DICOM; săgeți: deplasare, plus/minus: zoom, 0: resetare, PageUp/PageDown: cadre"
      onKeyDown={onKey} onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerUp}
      onWheel={event => zoomBy(event.deltaY < 0 ? 1.1 : 0.9)}>
      {error && <p className="error" role="alert">{error}</p>}
      {!buffer && !error && <p className="muted">Se încarcă imaginea DICOM…</p>}
      <canvas ref={canvas} width={meta.columns ?? 1} height={meta.rows ?? 1} data-testid="dicom-canvas"
        style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`, imageRendering: zoom > 2 ? 'pixelated' : 'auto', display: buffer ? 'block' : 'none' }} />
    </div>
    <DicomMetadataPanel meta={meta} />
  </div>;
}
