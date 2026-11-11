import { useRef, useState, type FormEvent } from 'react';
import { useMutation } from '@tanstack/react-query';
import { apiErrorMessage } from '../api/client';
import type { Appointment, ProcedureCatalog } from '../api/client';
import { ADULT_TOOTH_IDS, formatFdi, type ToothId, type ToothSurface } from '../odontogram/types';
import { surfacesForTooth } from '../odontogram/surfaces';
import { createPerformedProcedure, type PerformedProcedure, type PerformedProcedureCreate } from './api';
import './t07.css';

const SURFACE_LABELS: Record<ToothSurface, string> = {
  mesial: 'Mesial', distal: 'Distal', buccal: 'Buccal', lingual: 'Lingual', palatal: 'Palatal',
  occlusal: 'Occlusal', incisal: 'Incisal', vestibular: 'Vestibular', labial: 'Labial',
  approximal: 'Approximal', interproximal: 'Interproximal',
};

export function ProcedureForm({ appointment, patientName, catalog, initialTooth, onSaved, onCancel, onSubmittingChange, canSubmit = true }: {
  appointment: Appointment;
  patientName: string;
  catalog: ProcedureCatalog[];
  initialTooth?: ToothId;
  onSaved: (procedure: PerformedProcedure) => void | Promise<void>;
  onCancel?: () => void;
  onSubmittingChange?: (submitting: boolean) => void;
  canSubmit?: boolean;
}) {
  const [procedureId, setProcedureId] = useState('');
  const [tooth, setTooth] = useState<ToothId | ''>(initialTooth ?? '');
  const [surfaces, setSurfaces] = useState<ToothSurface[]>([]);
  const [notes, setNotes] = useState('');
  const [refreshError, setRefreshError] = useState('');
  const submitLock = useRef(false);
  const mutation = useMutation({ mutationFn: (payload: PerformedProcedureCreate) => createPerformedProcedure(payload), retry: false });
  const selectedProcedure = catalog.find(item => item.id === procedureId);
  const allowedSurfaces = tooth ? surfacesForTooth(tooth).map(item => item.id) : [];

  function toggleSurface(surface: ToothSurface) {
    setSurfaces(current => current.includes(surface) ? current.filter(item => item !== surface) : [...current, surface]);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitLock.current || mutation.isPending || !procedureId || !canSubmit) return;
    submitLock.current = true;
    onSubmittingChange?.(true);
    setRefreshError('');
    const payload: PerformedProcedureCreate = {
      appointment_id: appointment.id,
      procedure_catalog_id: procedureId,
      ...(tooth ? { tooth_fdi: tooth } : {}),
      ...(surfaces.length ? { tooth_surface: surfaces.join(',') } : {}),
      ...(notes.trim() ? { notes: notes.trim() } : {}),
    };
    try {
      const saved = await mutation.mutateAsync(payload);
      setProcedureId('');
      setTooth('');
      setSurfaces([]);
      setNotes('');
      try {
        await onSaved(saved);
      } catch {
        setRefreshError('Manopera a fost salvată. Reîncarcă dosarul sau vizita dacă lista nu se actualizează.');
      }
    } catch {
      // Keep all inputs intact. Do not automatically retry an uncertain create request.
    } finally {
      submitLock.current = false;
      onSubmittingChange?.(false);
    }
  }

  const visitDate = new Intl.DateTimeFormat('ro-RO', { dateStyle: 'medium', timeStyle: 'short', timeZone: appointment.timezone }).format(new Date(appointment.local_start));
  const selectedSurfaceLabel = surfaces.length ? surfaces.map(surface => SURFACE_LABELS[surface]).join(', ') : 'No surface selected';

  return <form className="card clinical-form t07-procedure-form" onSubmit={submit}>
    <h2>Add performed procedure</h2>
    <dl className="t07-procedure-context">
      <div><dt>Patient</dt><dd>{patientName}</dd></div>
      <div><dt>Selected visit</dt><dd>{visitDate} · {appointment.appointment_type} · {appointment.status.replace('_', ' ')}</dd></div>
      <div><dt>Selected catalog procedure</dt><dd>{selectedProcedure ? `${selectedProcedure.code} · ${selectedProcedure.name}` : 'Choose a procedure'}</dd></div>
      <div><dt>Location</dt><dd>{tooth ? `FDI ${formatFdi(tooth)} · ${selectedSurfaceLabel}` : 'No tooth selected'}</dd></div>
    </dl>
    <label>Procedure from active catalog<select required value={procedureId} onChange={event => setProcedureId(event.target.value)}>
      <option value="">Choose a procedure</option>
      {catalog.map(item => <option key={item.id} value={item.id}>{item.code} · {item.name} · {item.base_price} {item.currency}</option>)}
    </select></label>
    <div className="grid two clinical-form-grid">
      <label>Tooth FDI (optional)<select aria-label="Procedure tooth" value={tooth} onChange={event => { setTooth(event.target.value as ToothId | ''); setSurfaces([]); }}>
        <option value="">No tooth</option>{ADULT_TOOTH_IDS.map(id => <option key={id} value={id}>{formatFdi(id)} · FDI {id}</option>)}
      </select></label>
      <fieldset className="t07-surface-field" disabled={!tooth}>
        <legend>Surfaces (optional)</legend>
        {allowedSurfaces.length ? <div className="t07-surface-options">{allowedSurfaces.map(surface => <label key={surface}>
          <input type="checkbox" checked={surfaces.includes(surface)} onChange={() => toggleSurface(surface)} />{SURFACE_LABELS[surface]}
        </label>)}</div> : <span className="muted">Select a tooth first.</span>}
      </fieldset>
    </div>
    <label>Procedure notes (optional)<textarea maxLength={1000} value={notes} onChange={event => setNotes(event.target.value)} /></label>
    {catalog.length === 0 && <p className="muted">No active catalog procedures are available.</p>}
    {mutation.isError && <p className="error" role="alert">{apiErrorMessage(mutation.error)} The form was kept. Check the visit before submitting again.</p>}
    {refreshError && <p className="error" role="alert">{refreshError}</p>}
    <p className="muted">Saving creates a pending procedure only. Start the timer explicitly from the visit list.</p>
    <button disabled={mutation.isPending || !procedureId || !canSubmit}>{mutation.isPending ? 'Saving…' : 'Save procedure'}</button>
    {onCancel && <button type="button" className="secondary" disabled={mutation.isPending} onClick={onCancel}>Cancel</button>}
  </form>;
}
