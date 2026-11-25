import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { apiErrorMessage, changePassword, closeOtherSessions, closeSession, listSessions } from '../api/client';

export function SessionsPage() {
  const client = useQueryClient();
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [passwordMessage, setPasswordMessage] = useState('');
  const query = useQuery({ queryKey: ['my-sessions'], queryFn: listSessions });
  async function close(action: () => Promise<void>) { await action(); await client.invalidateQueries({ queryKey: ['my-sessions'] }); }
  return <section className="card" aria-label="Sesiunile mele">
    <header><div><p className="eyebrow">CONTUL MEU</p><h1>Sesiunile mele</h1></div>
      <button className="secondary" disabled={!query.data?.some(session => !session.current)} onClick={() => void close(closeOtherSessions)}>Închide celelalte sesiuni</button></header>
    {query.isLoading && <p className="muted">Se încarcă…</p>}
    {query.isError && <p className="error" role="alert">{apiErrorMessage(query.error)}</p>}
    {query.data?.map(session => <article className="card" key={session.session_id}>
      <strong>{session.current ? 'Sesiunea curentă' : 'Sesiune activă'}</strong>
      <p>Creată: {new Date(session.created_at).toLocaleString('ro-RO')}</p>
      <p>Ultima folosire: {new Date(session.last_used_at).toLocaleString('ro-RO')}</p>
      {!session.current && <button onClick={() => void close(() => closeSession(session.session_id))}>Închide sesiunea</button>}
    </article>)}
    {query.data?.length === 0 && <p className="muted">Nu există sesiuni active.</p>}
    <form className="card" onSubmit={event => { event.preventDefault(); void changePassword(currentPassword, newPassword).then(() => {
      setCurrentPassword(''); setNewPassword(''); setPasswordMessage('Parola a fost schimbată. Celelalte sesiuni au fost închise.');
      return client.invalidateQueries({ queryKey: ['my-sessions'] });
    }).catch(error => setPasswordMessage(apiErrorMessage(error))); }}>
      <h2>Schimbă parola</h2>
      <label>Parola curentă<input type="password" required value={currentPassword} onChange={event => setCurrentPassword(event.target.value)} /></label>
      <label>Parola nouă (minimum 12 caractere)<input type="password" required minLength={12} value={newPassword} onChange={event => setNewPassword(event.target.value)} /></label>
      {passwordMessage && <p role="status">{passwordMessage}</p>}
      <button disabled={newPassword.length < 12}>Schimbă parola</button>
    </form>
  </section>;
}
