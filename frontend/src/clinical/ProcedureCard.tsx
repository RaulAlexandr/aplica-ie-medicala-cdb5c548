import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
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

/** Display-only ticking clock: server totals stay the source of truth, this only adds the open run segment. */
export function liveSeconds(total: number | null, openRunStartedAt: string | null, now: number): number | null {
  if (!openRunStartedAt) return total;
  const elapsed = Math.max(0, Math.floor((now - new Date(openRunStartedAt).getTime()) / 1000));
  return (total ?? 0) + elapsed;
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
  const queryClient = useQueryClient();
  const [showSegments, setShowSegments] = useState(false);
  const [showRevisions, setShowRevisions] = useState(false);
  const revisions = useQuery({ queryKey: ['procedure-revisions', procedure.id], queryFn: () => getPerformedProcedureRevisions(procedure.id), enabled: showRevisions });
  const isRunning = procedure.status === 'running';
  const segments = useQuery({ queryKey: ['procedure-segments', procedure.id], queryFn: () => getProcedureSegments(procedure.id), enabled: showSegments || isRunning });
  const openRun = isRunning ? segments.data?.find(segment => segment.segment_type === 'run' && segment.ended_at === null) : undefined;
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!openRun) return undefined;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [openRun?.id]);
  const action = useMutation({
    mutationFn: (next: Action) => {
      if (next === 'start') return startPerformedProcedure(procedure.id);
      if (next === 'pause') return pausePerformedProcedure(procedure.id);
      if (next === 'resume') return resumePerformedProcedure(procedure.id);
      if (next === 'complete') return completePerformedProcedure(procedure.id);
      return cancelPerformedProcedure(procedure.id);
    },
    onSuccess: async () => {
      // Open history panels must follow the server after every successful transition.
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['procedure-segments', procedure.id] }),
        queryClient.invalidateQueries({ queryKey: ['procedure-revisions', procedure.id] }),
        onChanged(),
      ]);
    },
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
    {openRun && <p className="procedure-live-timer" role="timer" aria-label="Elapsed active time"><strong>Running now:</strong> {durationLabel(liveSeconds(procedure.total_duration_seconds, openRun.started_at, now))} (display only; the server total above is the record)</p>}
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
