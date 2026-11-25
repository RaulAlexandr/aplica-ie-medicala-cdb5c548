import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiErrorMessage, getStaff, reactivateStaff, unlockStaffLogin } from '../api/client';

const managementRoles = ['clinic_manager', 'administrator'];

function ErrorState({ error }: { error: unknown }) { return <p className="error" role="alert">{apiErrorMessage(error)}</p>; }

const STAFF_HISTORY_LABELS: Record<string, string> = { created_from_invitation: 'Cont creat din invitație', deactivated: 'Dezactivat', reactivated: 'Reactivat' };

export function StaffDetail({ staffId }: { staffId?: string } = {}) {
  const params = useParams();
  const id = staffId ?? params.id ?? '';
  const client = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [message, setMessage] = useState('');
  const query = useQuery({ queryKey: ['staff', id], queryFn: () => getStaff(id) });
  const reactivate = useMutation({
    mutationFn: () => reactivateStaff(id),
    onSuccess: data => { setConfirming(false); setMessage(`${data.full_name} a fost reactivat. Se va autentifica din nou cu parola existentă.`); client.invalidateQueries({ queryKey: ['staff'] }); },
    onError: error => { setConfirming(false); setMessage(apiErrorMessage(error)); },
  });
  const unlock = useMutation({
    mutationFn: () => unlockStaffLogin(id),
    onSuccess: () => setMessage(`Autentificarea pentru ${personName} a fost deblocată. Acțiunea a fost înregistrată.`),
    onError: error => setMessage(apiErrorMessage(error)),
  });
  if (query.isLoading) return <p className="muted">Loading staff member…</p>;
  if (query.isError || !query.data) return <ErrorState error={query.error} />;
  const person = query.data;
  const personName = person.full_name;
  const canReactivate = !person.is_active && !managementRoles.includes(person.role);
  return <>
    <Link to="/staff">← Staff</Link>
    <section className="card">
      <p className="eyebrow">STAFF DETAIL</p>
      <h1>{person.full_name}</h1>
      <p>{person.email} · {person.role} · {person.is_active ? 'Active' : 'Inactive'}</p>
      <p>{person.phone || 'No phone recorded'} · {person.specialization || 'No specialization recorded'}</p>
      <p>Upcoming active appointments: {person.upcoming_appointment_count}</p>
      {canReactivate && !confirming && <button type="button" onClick={() => { setMessage(''); setConfirming(true); }}>Reactivează</button>}
      {managementRoles.includes(person.role) && person.is_active && <button type="button" className="secondary" disabled={unlock.isPending} onClick={() => { setMessage(''); unlock.mutate(); }}>Deblochează autentificarea</button>}
      {canReactivate && confirming && <div role="group" aria-label="Confirmare reactivare">
        <p>Reactivezi contul lui {person.full_name}? Sesiunile închise la dezactivare nu se redeschid; persoana se autentifică din nou.</p>
        <button type="button" onClick={() => reactivate.mutate()} disabled={reactivate.isPending}>Confirmă reactivarea</button>
        <button type="button" className="secondary" onClick={() => setConfirming(false)} disabled={reactivate.isPending}>Renunță</button>
      </div>}
      {message && <p role="status">{message}</p>}
    </section>
    {person.history && person.history.length > 0 && <section className="card">
      <h2>Istoric cont</h2>
      <ul>{person.history.map(entry => <li key={`${entry.action}-${entry.occurred_at}`}>{STAFF_HISTORY_LABELS[entry.action] ?? entry.action} · {new Date(entry.occurred_at).toLocaleString()} · {entry.actor_name ?? entry.actor_id}</li>)}</ul>
    </section>}
  </>;
}
