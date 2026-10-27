import { useState, type FormEvent } from 'react';
import { useMutation } from '@tanstack/react-query';
import { apiErrorMessage, type User } from '../api/client';
import { createClinicalNote, updateClinicalNote, type ClinicalNote, type ClinicalNoteCreate } from './api';

type NoteFormState = {
  note_type: string;
  title: string;
  content: string;
  tooth_fdi: string;
  tooth_surface: string;
  is_alert: boolean;
};

const emptyForm: NoteFormState = { note_type: 'note', title: '', content: '', tooth_fdi: '', tooth_surface: '', is_alert: false };

export function ClinicalNoteEditor({ patientId, appointmentId, user, initial, onSaved, onCancel }: {
  patientId: string;
  appointmentId?: string;
  user: User;
  initial?: ClinicalNote;
  onSaved: (note: ClinicalNote) => void;
  onCancel?: () => void;
}) {
  const [form, setForm] = useState<NoteFormState>(() => initial ? {
    note_type: initial.note_type,
    title: initial.title ?? '',
    content: initial.content,
    tooth_fdi: initial.tooth_fdi ?? '',
    tooth_surface: initial.tooth_surface ?? '',
    is_alert: initial.is_alert,
  } : emptyForm);
  const mutation = useMutation({
    mutationFn: () => {
      if (initial) {
        return updateClinicalNote(initial.id, {
          title: form.title.trim() || null,
          content: form.content.trim(),
          tooth_fdi: form.tooth_fdi.trim() || null,
          tooth_surface: form.tooth_surface.trim() || null,
          is_alert: form.is_alert,
        });
      }
      const payload: ClinicalNoteCreate = {
        patient_id: patientId,
        note_type: form.note_type,
        content: form.content.trim(),
        is_alert: form.is_alert,
      };
      if (appointmentId) payload.appointment_id = appointmentId;
      if (form.title.trim()) payload.title = form.title.trim();
      if (form.tooth_fdi.trim()) payload.tooth_fdi = form.tooth_fdi.trim();
      if (form.tooth_surface.trim()) payload.tooth_surface = form.tooth_surface.trim();
      return createClinicalNote(payload);
    },
    onSuccess: onSaved,
  });
  const set = (key: keyof NoteFormState, value: string | boolean) => setForm(current => ({ ...current, [key]: value }));
  const isAuthor = !initial || initial.author_id === user.id;

  function submit(event: FormEvent) {
    event.preventDefault();
    mutation.mutate();
  }

  return <form className="card clinical-form" onSubmit={submit}>
    <div className="detail-heading"><div><p className="eyebrow">{initial ? 'DRAFT' : 'NEW RECORD'}</p><h2>{initial ? 'Edit clinical draft' : 'New clinical note or finding'}</h2></div>{initial && <span className="clinical-status">Draft</span>}</div>
    <div className="grid two clinical-form-grid">
      {!initial && <label>Record type<select value={form.note_type} onChange={event => set('note_type', event.target.value)}><option value="note">Clinical note</option><option value="finding">Finding</option><option value="alert">Alert</option><option value="diagnosis">Diagnosis</option><option value="treatment_plan">Treatment plan</option></select></label>}
      <label>Title (optional)<input maxLength={200} value={form.title} onChange={event => set('title', event.target.value)} /></label>
      <label>Tooth FDI (optional)<input inputMode="numeric" maxLength={2} pattern="[1-4][1-8]" value={form.tooth_fdi} onChange={event => set('tooth_fdi', event.target.value)} placeholder="e.g. 26" /></label>
      <label>Surface (optional)<input maxLength={100} value={form.tooth_surface} onChange={event => set('tooth_surface', event.target.value)} placeholder="e.g. occlusal, mesial" /></label>
    </div>
    {appointmentId && !initial && <p className="muted">This note will be associated with the open visit.</p>}
    <label>Clinical content<textarea required minLength={1} maxLength={5000} value={form.content} onChange={event => set('content', event.target.value)} /></label>
    <label className="clinical-checkbox"><input type="checkbox" checked={form.is_alert} onChange={event => set('is_alert', event.target.checked)} /> Mark as a clinical alert</label>
    {!isAuthor && <p className="error" role="alert">Only the draft author can edit this note.</p>}
    {mutation.isError && <p className="error" role="alert">{apiErrorMessage(mutation.error)}</p>}
    <div className="clinical-actions">
      {onCancel && <button type="button" className="secondary" disabled={mutation.isPending} onClick={onCancel}>Cancel</button>}
      <button disabled={!isAuthor || mutation.isPending || !form.content.trim()}>{mutation.isPending ? 'Saving…' : initial ? 'Save draft' : 'Save as draft'}</button>
    </div>
  </form>;
}
