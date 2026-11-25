import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import {
  AmbientLight, Box3, DirectionalLight, Mesh, MeshStandardMaterial, PerspectiveCamera, Scene, Spherical, Vector3, WebGLRenderer,
} from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js';
import { apiErrorMessage } from '../api/client';
import { fetchDocumentContent, type ClinicalDocument } from './api';
import './imaging.css';

type Api = { reset: () => void; setWireframe: (value: boolean) => void; orbit: (dTheta: number, dPhi: number) => void; zoom: (factor: number) => void; pan: (dx: number, dy: number) => void };

export default function StlViewer({ document }: { document: ClinicalDocument }) {
  const host = useRef<HTMLDivElement>(null);
  const api = useRef<Api | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [message, setMessage] = useState('');
  const [wireframe, setWireframe] = useState(false);
  const [camera, setCamera] = useState('');

  useEffect(() => {
    const element = host.current;
    if (!element) return undefined;
    let cancelled = false;
    let frame = 0;
    let cleanup = () => {};
    setState('loading'); setMessage('');
    (async () => {
      let renderer: WebGLRenderer;
      try {
        renderer = new WebGLRenderer({ antialias: true });
      } catch {
        if (!cancelled) { setState('error'); setMessage('Afișarea 3D nu este disponibilă în acest browser (WebGL lipsește). Fișierul poate fi descărcat.'); }
        return;
      }
      try {
        const blob = await fetchDocumentContent(document.id);
        const geometry = new STLLoader().parse(await blob.arrayBuffer());
        if (cancelled) { geometry.dispose(); renderer.dispose(); return; }
        geometry.computeVertexNormals();
        const box = new Box3().setFromBufferAttribute(geometry.getAttribute('position') as never);
        const centre = box.getCenter(new Vector3());
        geometry.translate(-centre.x, -centre.y, -centre.z);
        const radius = Math.max(box.getSize(new Vector3()).length() / 2, 1e-6);
        const material = new MeshStandardMaterial({ color: 0xd9d4c7, roughness: 0.6, metalness: 0.05 });
        const mesh = new Mesh(geometry, material);
        const scene = new Scene();
        scene.add(mesh, new AmbientLight(0xffffff, 0.8));
        const light = new DirectionalLight(0xffffff, 2.2); light.position.set(1, 2, 3); scene.add(light);
        const view = new PerspectiveCamera(40, 1, radius / 100, radius * 100);
        const home = new Vector3(radius * 1.6, radius * 1.1, radius * 2.4);
        view.position.copy(home);
        const controls = new OrbitControls(view, renderer.domElement);
        controls.enableDamping = false; controls.screenSpacePanning = true;
        controls.saveState();
        renderer.domElement.setAttribute('aria-hidden', 'true');
        element.querySelector('canvas')?.remove();
        element.appendChild(renderer.domElement);
        const resize = () => {
          const w = Math.max(element.clientWidth, 1); const h = Math.max(element.clientHeight, 1);
          renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2)); renderer.setSize(w, h, false);
          view.aspect = w / h; view.updateProjectionMatrix(); draw();
        };
        const draw = () => {
          renderer.render(scene, view);
          const p = view.position;
          setCamera(`${p.x.toFixed(2)},${p.y.toFixed(2)},${p.z.toFixed(2)}`);
        };
        const schedule = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(draw); };
        controls.addEventListener('change', schedule);
        const observer = new ResizeObserver(resize); observer.observe(element);
        const spherical = new Spherical();
        api.current = {
          reset: () => { controls.reset(); schedule(); },
          setWireframe: value => { material.wireframe = value; schedule(); },
          orbit: (dTheta, dPhi) => {
            const offset = view.position.clone().sub(controls.target);
            spherical.setFromVector3(offset);
            spherical.theta += dTheta; spherical.phi = Math.min(Math.PI - 0.05, Math.max(0.05, spherical.phi + dPhi));
            offset.setFromSpherical(spherical); view.position.copy(controls.target).add(offset); view.lookAt(controls.target); schedule();
          },
          zoom: factor => {
            const offset = view.position.clone().sub(controls.target).multiplyScalar(factor);
            view.position.copy(controls.target).add(offset); schedule();
          },
          pan: (dx, dy) => {
            const right = new Vector3().setFromMatrixColumn(view.matrix, 0); const up = new Vector3().setFromMatrixColumn(view.matrix, 1);
            const move = right.multiplyScalar(dx * radius * 0.05).add(up.multiplyScalar(dy * radius * 0.05));
            view.position.add(move); controls.target.add(move); view.lookAt(controls.target); schedule();
          },
        };
        resize();
        setState('ready');
        cleanup = () => {
          cancelAnimationFrame(frame); observer.disconnect(); controls.dispose();
          geometry.dispose(); material.dispose(); renderer.dispose(); renderer.domElement.remove(); api.current = null;
        };
        if (cancelled) cleanup();
      } catch (error) {
        renderer.dispose();
        if (!cancelled) { setState('error'); setMessage(error instanceof Error && /STL|buffer|Offset/i.test(error.message) ? 'Fișierul STL nu a putut fi citit.' : apiErrorMessage(error)); }
      }
    })();
    return () => { cancelled = true; cleanup(); };
  }, [document.id]);

  const toggleWireframe = () => { const next = !wireframe; setWireframe(next); api.current?.setWireframe(next); };
  const onKey = (event: KeyboardEvent<HTMLDivElement>) => {
    const control = api.current; if (!control) return;
    const step = 0.12;
    const actions: Record<string, () => void> = {
      ArrowLeft: () => (event.shiftKey ? control.pan(-1, 0) : control.orbit(-step, 0)), ArrowRight: () => (event.shiftKey ? control.pan(1, 0) : control.orbit(step, 0)),
      ArrowUp: () => (event.shiftKey ? control.pan(0, 1) : control.orbit(0, -step)), ArrowDown: () => (event.shiftKey ? control.pan(0, -1) : control.orbit(0, step)),
      '+': () => control.zoom(0.85), '=': () => control.zoom(0.85), '-': () => control.zoom(1.18), '0': () => control.reset(),
      w: toggleWireframe, W: toggleWireframe,
    };
    const action = actions[event.key];
    if (action) { event.preventDefault(); action(); }
  };

  return <div className="img-viewer" aria-label="Vizualizator STL">
    <div className="img-toolbar" role="toolbar" aria-label="Instrumente 3D">
      <button type="button" className="secondary" aria-pressed={wireframe} disabled={state !== 'ready'} onClick={toggleWireframe}>Wireframe</button>
      <button type="button" className="secondary" aria-label="Mărește" disabled={state !== 'ready'} onClick={() => api.current?.zoom(0.85)}>+</button>
      <button type="button" className="secondary" aria-label="Micșorează" disabled={state !== 'ready'} onClick={() => api.current?.zoom(1.18)}>−</button>
      <button type="button" className="secondary" disabled={state !== 'ready'} onClick={() => api.current?.reset()}>Resetează vederea</button>
    </div>
    <p className="muted img-help">Mouse: trage pentru rotire, rotiță pentru zoom, click dreapta pentru deplasare. Touch: un deget rotește, două degete mărește și deplasează. Tastatură: săgeți rotesc, Shift+săgeți deplasează, + / − zoom, 0 resetare, W wireframe.</p>
    <div className="img-stage img-stl" ref={host} tabIndex={0} role="application" aria-label={`Model 3D ${document.original_filename}`} onKeyDown={onKey}
      data-testid="stl-stage" data-state={state} data-wireframe={String(wireframe)} data-camera={camera}>
      {state === 'loading' && <p className="muted img-overlay">Se încarcă modelul 3D…</p>}
      {state === 'error' && <p className="error img-overlay" role="alert">{message}</p>}
    </div>
  </div>;
}
