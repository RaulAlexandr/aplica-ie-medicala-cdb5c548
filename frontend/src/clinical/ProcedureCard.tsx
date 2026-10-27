import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import type { User } from '../api/client';
import { apiErrorMessage } from '../api/client';
import {
  cancelPerformedProcedure,
  completePerformedProcedure,
  getPerformedProcedureRevisions,
  getProcedureSegments,
  pausePerformedProcedure,
  resumePerformedProcedure,
  startPerformedProcedure,
  type PerformedProcedure,
} from './api';

type Action = 'start' | 'pause' | 'resume' | 'complete' | 'cancel';
const managementRoles = new Set(['clinic_manager', 'administrator']);
const clinicalRoles = new Set(['doctor', 'clinic_manager', 'administrator']);

function durationLabel(seconds: number | null) {
  if (seconds === null) return 'No active time recorded yet';
  const minutes = Math.floor(seconds / 60);
  const remaining = seconds % 60;
  return minutes ? `${minutes} min ${remaining} sec` : `${remaining} sec`;
}

function clinicDateTime(value: string, timezone: string) {
  return new Intl.DateTimeFormat('ro-RO', { dateStyle: 'medium', timeStyle: 'short', timeZone: timezone }).format(new Date(value));
}

export function ProcedureCard({ procedure, user, timezone, isVisitClosed, onChanged }: {
  procedure: PerformedProcedure;
  user: User;
  timezone: string;
  isVisitClosed: boolean;
  onChanged: () => Promise<void>;
}) {
  const [showSegments, setShowSegments] = useState(false);
  const [showRevisions, setShowRevisions] = useState(false);
  const segments = useQuery({ queryKey: ['procedure-segments', procedure.id], queryFn: () => getProcedureSegments(procedure.id), enabled: showSegments });
  const revisions = useQuery({ queryKey: ['procedure-revisions', procedure.id], queryFn: () => getPerformedProcedureRevisions(procedure.id), enabled: showRevisions });
  const action = useMutation({
    mutationFn: (next: Action) => {
      if (next === 'start') return startPerformedProcedure(procedure.id);
      if (next === 'pause') return pausePerformedProcedure(procedure.id);
      if (next === 'resume') return resumePerformedProcedure(procedure.id);
      if (next === 'complete') return completePerformedProcedure(procedure.id);
      return cancelPerformedProcedure(procedure.id);
    },
    onSuccess: onChanged,
  });
  const canManage = clinicalRoles.has(user.role) && (
    procedure.executed_by_id === user.id || managementRoles.has(user.role)
  );
  const canMutate = canManage && !isVisitClosed;
  const actions: Array<{ action: Action; label: string; className?: string }> = procedure.status === 'pending'
    ? [{ action: 'start', label: 'Start' }, { action: 'cancel', label: 'Cancel', className: 'secondary' }]
    : procedure.status === 'running'
      ? [{ action: 'pause', label: 'Pause' }, { action: 'complete', label: 'Complete' }, { action: 'cancel', label: 'Cancel', className: 'secondary' }]
      : procedure.status === 'paused'
        ? [{ action: 'resume', label: 'Resume' }, { action: 'complete', label: 'Complete' }, { action: 'cancel', label: 'Cancel', className: 'secondary' }]
        : [];

  return <article className="clinical-list-item procedure-card">
    <div className="detail-heading">
      <div>
        <h3>{procedure.procedure_code} · {procedure.procedure_name}</h3>
        <p className="muted">{procedure.status.replace('_', ' ')} · {procedure.tooth_fdi ? `FDI ${procedure.tooth_fdi}` : 'No tooth'}{procedure.tooth_surface ? ` · ${procedure.tooth_surface}` : ''}</p>
      </div>
      <strong className="clinical-status">{procedure.status.replace('_', ' ')}</strong>
    </div>
    <p><strong>Catalog price snapshot:</strong> {procedure.procedure_base_price} {procedure.procedure_currency}</p>
    {procedure.current_price !== null && procedure.current_price !== procedure.procedure_base_price && <p><strong>Current recorded price:</strong> {procedure.current_price} {procedure.procedure_currency}</p>}
    <p><strong>Active time from server:</strong> {durationLabel(procedure.total_duration_seconds)}{procedure.status === 'running' ? ' · current segment is not included until stopped' : ''}</p>
    {procedure.notes && <p className="clinical-note-text">{procedure.notes}</p>}
    {canMutate && actions.length > 0 && <div className="clinical-actions">
      {actions.map(item => <button key={item.action} className={item.className ?? ''} disabled={action.isPending} onClick={() => action.mutate(item.action)}>{action.isPending ? 'Saving…' : item.label}</button>)}
    </div>}
    {action.isError && <p className="error" role="alert">{apiErrorMessage(action.error)}</p>}
    <div className="clinical-actions clinical-history-actions">
      <button className="secondary" onClick={() => setShowSegments(value => !value)}>{showSegments ? 'Hide timer segments' : 'Timer segments'}</button>
      <button className="secondary" onClick={() => setShowRevisions(value => !value)}>{showRevisions ? 'Hide revisions' : 'Revisions'}</button>
    </div>
    {showSegments && <section className="clinical-history" aria-label={`Timer segments for ${procedure.procedure_name}`}>
      {segments.isLoading && <p className="muted">Loading segments…</p>}
      {segments.isError && <p className="error" role="alert">{apiErrorMessage(segments.error)}</p>}
      {segments.data?.length === 0 && <p className="muted">No timer segments recorded.</p>}
      {segments.data?.map(segment => <p key={segment.id}>{segment.segment_type === 'run' ? 'Run' : 'Pause'} · {clinicDateTime(segment.started_at, timezone)}{segment.ended_at ? ` – ${clinicDateTime(segment.ended_at, timezone)}` : ' – active'}{segment.duration_seconds !== null ? ` · ${durationLabel(segment.duration_seconds)}` : ''}</p>)}
    </section>}
    {showRevisions && <section className="clinical-history" aria-label={`Revisions for ${procedure.procedure_name}`}>
      {revisions.isLoading && <p className="muted">Loading revisions…</p>}
      {revisions.isError && <p className="error" role="alert">{apiErrorMessage(revisions.error)}</p>}
      {revisions.data?.length === 0 && <p className="muted">No revisions recorded.</p>}
      {revisions.data?.map(revision => <p key={revision.id}>{revision.field}: {revision.previous_value ?? '—'} → {revision.new_value ?? '—'} · {revision.reason || 'No reason recorded'} · {new Date(revision.occurred_at).toLocaleString()}</p>)}
    </section>}
  </article>;
}
