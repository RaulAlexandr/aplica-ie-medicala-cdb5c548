import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiErrorMessage, type User } from '../api/client';
import { ClinicalNoteEditor } from './ClinicalNoteEditor';
import { finalizeClinicalNote, getClinicalNote, getClinicalNoteRevisions, listPatientClinicalNotes, type ClinicalNote } from './api';

const pageSize = 20;

function NoteCard({ note, timezone, onChanged, onEdit }: {
  note: ClinicalNote;
  timezone: string;
  onChanged: () => Promise<void>;
  onEdit: () => void;
}) {
  const [showRevisions, setShowRevisions] = useState(false);
  const revisions = useQuery({ queryKey: ['clinical-note-revisions', note.id], queryFn: () => getClinicalNoteRevisions(note.id), enabled: showRevisions });
  const finalize = useMutation({ mutationFn: () => finalizeClinicalNote(note.id), onSuccess: onChanged });
  const date = new Intl.DateTimeFormat('ro-RO', { dateStyle: 'medium', timeStyle: 'short', timeZone: timezone }).format(new Date(note.created_at));
  return <article className="clinical-list-item">
    <div className="detail-heading"><div><p className="eyebrow">{note.note_type.replace('_', ' ')}</p><h3>{note.title || 'Untitled clinical record'}</h3></div><span className={`clinical-status ${note.status === 'finalized' ? 'complete' : ''}`}>{note.status}</span></div>
    <p className="muted">{date} · {note.author_name}{note.is_alert ? ' · Alert' : ''}</p>
    <p className="clinical-note-text">{note.content}</p>
    {(note.tooth_fdi || note.tooth_surface) && <p className="muted">{note.tooth_fdi ? `FDI ${note.tooth_fdi}` : ''}{note.tooth_surface ? ` · ${note.tooth_surface}` : ''}</p>}
    {note.status === 'draft' && <div className="clinical-actions">
      <button className="secondary" onClick={onEdit}>Reopen draft</button>
      <button disabled={finalize.isPending} onClick={() => finalize.mutate()}>{finalize.isPending ? 'Finalizing…' : 'Finalize note'}</button>
    </div>}
    {finalize.isError && <p className="error" role="alert">{apiErrorMessage(finalize.error)}</p>}
    <button className="link clinical-link" onClick={() => setShowRevisions(value => !value)}>{showRevisions ? 'Hide revisions' : 'Read revisions'}</button>
    {showRevisions && <section className="clinical-history" aria-label={`Revisions for ${note.title || note.note_type}`}>
      {revisions.isLoading && <p className="muted">Loading revisions…</p>}
      {revisions.isError && <p className="error" role="alert">{apiErrorMessage(revisions.error)}</p>}
      {revisions.data?.length === 0 && <p className="muted">No revisions recorded.</p>}
      {revisions.data?.map(revision => <p key={revision.id}><strong>{revision.field}</strong>: {revision.previous_value ?? '—'} → {revision.new_value ?? '—'} · {revision.actor_name} · {new Intl.DateTimeFormat('ro-RO', { dateStyle: 'medium', timeStyle: 'short', timeZone: timezone }).format(new Date(revision.occurred_at))}</p>)}
    </section>}
  </article>;
}

function ReopenDraft({ noteId, patientId, user, onSaved, onCancel }: {
  noteId: string;
  patientId: string;
  user: User;
  onSaved: (note: ClinicalNote) => void;
  onCancel: () => void;
}) {
  const query = useQuery({ queryKey: ['clinical-note', noteId], queryFn: () => getClinicalNote(noteId) });
  if (query.isLoading) return <p className="muted">Loading saved draft…</p>;
  if (query.isError || !query.data) return <p className="error" role="alert">{apiErrorMessage(query.error)}</p>;
  if (query.data.patient_id !== patientId || query.data.status !== 'draft' || query.data.author_id !== user.id) return <p className="error" role="alert">This draft is no longer available to edit.</p>;
  return <ClinicalNoteEditor key={query.data.id} patientId={patientId} user={user} initial={query.data} onSaved={onSaved} onCancel={onCancel} />;
}

export function ClinicalNotesPanel({ patientId, appointmentId, user, timezone }: {
  patientId: string;
  appointmentId?: string;
  user: User;
  timezone: string;
}) {
  const client = useQueryClient();
  const [page, setPage] = useState(0);
  const [status, setStatus] = useState('');
  const [noteType, setNoteType] = useState('');
  const [creating, setCreating] = useState(false);
  const [editingNoteId, setEditingNoteId] = useState<string | null>(null);
  const query = useQuery({
    queryKey: ['patient-notes', patientId, status, noteType, page],
    queryFn: () => listPatientClinicalNotes(patientId, { status: status || undefined, note_type: noteType || undefined, limit: pageSize, offset: page * pageSize }),
  });
  const refresh = async () => {
    setCreating(false);
    setEditingNoteId(null);
    await Promise.all([
      query.refetch(),
      client.invalidateQueries({ queryKey: ['patient-record', patientId] }),
      client.invalidateQueries({ queryKey: ['patient-timeline', patientId] }),
    ]);
  };
  useEffect(() => setPage(0), [status, noteType]);

  return <section className="card clinical-record-section">
    <div className="detail-heading"><div><p className="eyebrow">CLINICAL NOTES AND FINDINGS</p><h2>Clinical records</h2></div><button onClick={() => { setCreating(value => !value); setEditingNoteId(null); }}>{creating ? 'Close form' : 'New note or finding'}</button></div>
    {creating && <ClinicalNoteEditor key={`${patientId}:${appointmentId ?? 'patient'}`} patientId={patientId} appointmentId={appointmentId} user={user} onSaved={refresh} onCancel={() => setCreating(false)} />}
    {editingNoteId && <ReopenDraft key={`${patientId}:${editingNoteId}`} noteId={editingNoteId} patientId={patientId} user={user} onSaved={refresh} onCancel={() => setEditingNoteId(null)} />}
    <div className="clinical-filters">
      <label>Type<select value={noteType} onChange={event => setNoteType(event.target.value)}><option value="">All types</option><option value="note">Clinical note</option><option value="finding">Finding</option><option value="alert">Alert</option><option value="diagnosis">Diagnosis</option><option value="treatment_plan">Treatment plan</option></select></label>
      <label>Status<select value={status} onChange={event => setStatus(event.target.value)}><option value="">All statuses</option><option value="draft">Drafts visible to you</option><option value="finalized">Finalized</option></select></label>
    </div>
    {query.isLoading && <p className="muted">Loading clinical records…</p>}
    {query.isError && <p className="error" role="alert">{apiErrorMessage(query.error)}</p>}
    {!query.isLoading && !query.isError && query.data?.length === 0 && <p className="muted">No clinical records match these filters.</p>}
    <div className="clinical-list">{query.data?.filter(note => note.id !== editingNoteId).map(note => <NoteCard key={note.id} note={note} timezone={timezone} onChanged={refresh} onEdit={() => { setCreating(false); setEditingNoteId(note.id); }} />)}</div>
    <div className="pagination">
      <button className="secondary" disabled={page === 0} onClick={() => setPage(value => Math.max(0, value - 1))}>Previous</button>
      <span>Page {page + 1}</span>
      <button className="secondary" disabled={(query.data?.length ?? 0) < pageSize} onClick={() => setPage(value => value + 1)}>Next</button>
    </div>
  </section>;
}
