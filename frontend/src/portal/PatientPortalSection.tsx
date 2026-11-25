import { FormEvent, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiErrorMessage, Patient } from '../api/client';
import { listPatientDocuments } from '../documents/api';
import { listPublishedForms } from '../forms/api';
import { listPatientPlans } from '../treatment-plans/api';
import {
  getPortalSection, grantPortalAccess, publishPortalDocument, publishPortalPlan, reissuePortalInvitation, revokePortalAccount,
  revokePortalLink, sendPortalForm, withdrawPortalDocument, withdrawPortalPlan,
} from './staffApi';
import './portal-staff.css';

const RELATIONSHIPS: Array<[string, string]> = [['self', 'Pacientul'], ['parent', 'Părinte'], ['guardian', 'Tutore'], ['other', 'Reprezentant']];
const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString('ro-RO', { dateStyle: 'medium', timeStyle: 'short' }) : '');
const key = () => crypto.randomUUID();

function CopyLink({ label, path }: { label: string; path: string }) {
  const absolute = `${window.location.origin}${path}`;
  const [copied, setCopied] = useState(false);
  async function copy() {
    try { await navigator.clipboard.writeText(absolute); setCopied(true); } catch { setCopied(false); }
  }
  return <div className="pp-link"><label>{label}<input readOnly value={absolute} onFocus={event => event.target.select()} /></label>
    <button type="button" className="secondary" onClick={() => void copy()}>{copied ? 'Copiat' : 'Copiază linkul'}</button></div>;
}

export function PatientPortalSection({ patient, canClinical }: { patient: Patient; canClinical: boolean }) {
  const client = useQueryClient();
  const section = useQuery({ queryKey: ['portal-section', patient.id], queryFn: () => getPortalSection(patient.id) });
  const refresh = () => client.invalidateQueries({ queryKey: ['portal-section', patient.id] });
  const [invite, setInvite] = useState({ email: patient.email ?? '', full_name: `${patient.first_name} ${patient.last_name}`, relationship: 'self' });
  const [link, setLink] = useState<{ path: string; expires: string | null } | null>(null);
  const [notice, setNotice] = useState('');
  const grant = useMutation({
    mutationFn: () => grantPortalAccess(patient.id, { ...invite, email: invite.email.trim() }),
    onSuccess: result => {
      setLink(result.invitation_url ? { path: result.invitation_url, expires: result.invitation_expires_at } : null);
      setNotice(result.invitation_url ? 'Invitația a fost creată. Copiază linkul și trimite-l pacientului. Nu se trimite niciun email.' : 'Contul are deja parolă: accesul la acest pacient a fost adăugat.');
      void refresh();
    },
  });
  const reissue = useMutation({ mutationFn: reissuePortalInvitation, onSuccess: result => { setLink({ path: result.invitation_url, expires: result.invitation_expires_at }); setNotice('Invitație nouă creată. Linkul vechi nu mai funcționează.'); void refresh(); } });
  const [revoking, setRevoking] = useState<string | null>(null);
  const [reason, setReason] = useState('');
  const revoke = useMutation({ mutationFn: (accountId: string) => revokePortalAccount(accountId, reason.trim()), onSuccess: () => { setRevoking(null); setReason(''); setNotice('Accesul contului a fost revocat. Sesiunile deschise s-au închis.'); void refresh(); } });
  const unlink = useMutation({ mutationFn: revokePortalLink, onSuccess: () => { setNotice('Legătura cu acest pacient a fost eliminată.'); void refresh(); } });
  const error = [grant, reissue, revoke, unlink].find(mutation => mutation.isError)?.error;

  if (section.isLoading) return <section className="card pp-section"><h2>Portal pacient</h2><p className="muted">Se încarcă…</p></section>;
  if (section.isError || !section.data) return <section className="card pp-section"><h2>Portal pacient</h2><p className="error" role="alert">{apiErrorMessage(section.error)}</p></section>;
  const data = section.data;
  return <section className="card pp-section" aria-label="Portal pacient">
    <h2>Portal pacient</h2>
    <p className="muted">Pacientul vede numai ce publici explicit aici: programările, documentele și planurile publicate și formularele trimise.</p>
    {notice && <p className="notice-text" role="status">{notice}</p>}
    {error !== undefined && <p className="error" role="alert">{apiErrorMessage(error)}</p>}
    {link && <><CopyLink label="Link de invitație (o singură folosire)" path={link.path} />{link.expires && <p className="muted">Expiră la {when(link.expires)}.</p>}</>}

    <h3>Conturi cu acces</h3>
    {data.accounts.length === 0 && <p className="muted">Niciun cont de portal pentru acest pacient.</p>}
    <div className="pp-list">{data.accounts.map(row => <article key={row.link_id} className="pp-row">
      <div><strong>{row.full_name}</strong><span>{row.email} · {row.relationship_label}</span>
        <span>{row.link_active ? row.status_label : 'Legătură eliminată'}{row.invitation_expires_at ? ` · invitație valabilă până la ${when(row.invitation_expires_at)}` : ''}</span></div>
      {data.can.manage_access && row.link_active && <div className="pp-actions">
        {row.status === 'invited' && <button type="button" className="secondary" disabled={reissue.isPending} onClick={() => reissue.mutate(row.account_id)}>Invitație nouă</button>}
        {row.status !== 'revoked' && <button type="button" className="secondary" onClick={() => { setRevoking(row.account_id); setReason(''); }}>Revocă contul</button>}
        <button type="button" className="secondary" disabled={unlink.isPending} onClick={() => unlink.mutate(row.link_id)}>Elimină accesul la pacient</button>
      </div>}
      {revoking === row.account_id && <form className="pp-inline" onSubmit={(event: FormEvent) => { event.preventDefault(); if (reason.trim()) revoke.mutate(row.account_id); }}>
        <label>Motivul revocării<input required maxLength={500} value={reason} onChange={event => setReason(event.target.value)} /></label>
        <button disabled={revoke.isPending || !reason.trim()}>Confirmă revocarea</button><button type="button" className="secondary" onClick={() => setRevoking(null)}>Renunță</button>
      </form>}
    </article>)}</div>

    {data.can.manage_access && <form className="pp-invite" onSubmit={(event: FormEvent) => { event.preventDefault(); setNotice(''); setLink(null); grant.mutate(); }}>
      <h3>Invită în portal</h3>
      <div className="pp-grid">
        <label>Email<input type="email" required value={invite.email} onChange={event => setInvite({ ...invite, email: event.target.value })} /></label>
        <label>Numele titularului contului<input required minLength={2} maxLength={160} value={invite.full_name} onChange={event => setInvite({ ...invite, full_name: event.target.value })} /></label>
        <label>Relația cu pacientul<select value={invite.relationship} onChange={event => setInvite({ ...invite, relationship: event.target.value })}>{RELATIONSHIPS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      </div>
      <p className="muted">Un părinte poate avea un singur cont pentru mai mulți copii: folosește același email la fiecare copil.</p>
      <button disabled={grant.isPending}>{grant.isPending ? 'Se creează…' : 'Creează invitația'}</button>
    </form>}
    <CopyLink label="Link de autentificare în portal" path={data.login_path} />

    {canClinical && data.can.publish && <PortalPublications patientId={patient.id} documents={data.documents ?? []} plans={data.plans ?? []} onChange={refresh} />}
    <PortalForms patientId={patient.id} forms={data.forms} canSeeAnswers={canClinical} onChange={refresh} />
  </section>;
}

function PortalPublications({ patientId, documents, plans, onChange }: { patientId: string; documents: NonNullable<Awaited<ReturnType<typeof getPortalSection>>['documents']>; plans: NonNullable<Awaited<ReturnType<typeof getPortalSection>>['plans']>; onChange: () => void }) {
  const patientDocuments = useQuery({ queryKey: ['portal-candidate-documents', patientId], queryFn: () => listPatientDocuments(patientId) });
  const patientPlans = useQuery({ queryKey: ['portal-candidate-plans', patientId], queryFn: () => listPatientPlans(patientId) });
  const publishDocument = useMutation({ mutationFn: (id: string) => publishPortalDocument(patientId, id), onSuccess: onChange });
  const withdrawDocument = useMutation({ mutationFn: withdrawPortalDocument, onSuccess: onChange });
  const publishPlan = useMutation({ mutationFn: (id: string) => publishPortalPlan(patientId, id), onSuccess: onChange });
  const withdrawPlan = useMutation({ mutationFn: withdrawPortalPlan, onSuccess: onChange });
  const error = [publishDocument, withdrawDocument, publishPlan, withdrawPlan].find(mutation => mutation.isError)?.error;
  const activeDocument = useMemo(() => new Map(documents.filter(row => !row.withdrawn_at).map(row => [row.document_id, row])), [documents]);
  const activePlan = useMemo(() => new Map(plans.filter(row => !row.withdrawn_at).map(row => [row.plan_id, row])), [plans]);
  return <>
    {error !== undefined && <p className="error" role="alert">{apiErrorMessage(error)}</p>}
    <h3>Documente în portal</h3>
    {patientDocuments.data?.length === 0 && <p className="muted">Pacientul nu are documente încărcate.</p>}
    <div className="pp-list">{patientDocuments.data?.filter(row => row.status === 'active').map(row => {
      const published = activeDocument.get(row.id);
      return <article key={row.id} className="pp-row"><div><strong className="pp-break">{row.original_filename}</strong>
        <span>{published ? `Publicat la ${when(published.published_at)}` : 'Nepublicat: pacientul nu îl vede'}</span></div>
        <div className="pp-actions">{published
          ? <button type="button" className="secondary" disabled={withdrawDocument.isPending} onClick={() => withdrawDocument.mutate(published.id)}>Retrage din portal</button>
          : <button type="button" className="secondary" disabled={publishDocument.isPending} onClick={() => publishDocument.mutate(row.id)}>Publică în portal</button>}</div>
      </article>;
    })}</div>
    <h3>Planuri în portal</h3>
    {patientPlans.data?.length === 0 && <p className="muted">Pacientul nu are planuri de tratament.</p>}
    <div className="pp-list">{patientPlans.data?.filter(row => row.status !== 'cancelled').map(row => {
      const published = activePlan.get(row.id);
      return <article key={row.id} className="pp-row"><div><strong>{row.title}</strong>
        <span>{published ? `Publicată versiunea ${published.version} la ${when(published.published_at)}. Modificările ulterioare apar după o nouă publicare.` : 'Nepublicat: pacientul nu îl vede'}</span></div>
        <div className="pp-actions">
          <button type="button" className="secondary" disabled={publishPlan.isPending} onClick={() => publishPlan.mutate(row.id)}>{published ? 'Publică varianta curentă' : 'Publică în portal'}</button>
          {published && <button type="button" className="secondary" disabled={withdrawPlan.isPending} onClick={() => withdrawPlan.mutate(published.id)}>Retrage din portal</button>}
        </div>
      </article>;
    })}</div>
  </>;
}

function PortalForms({ patientId, forms, canSeeAnswers, onChange }: { patientId: string; forms: Awaited<ReturnType<typeof getPortalSection>>['forms']; canSeeAnswers: boolean; onChange: () => void }) {
  const templates = useQuery({ queryKey: ['forms', 'published'], queryFn: listPublishedForms });
  const [version, setVersion] = useState('');
  const [requestKey, setRequestKey] = useState(key);
  const send = useMutation({ mutationFn: () => sendPortalForm(patientId, version, requestKey), onSuccess: () => { setRequestKey(key()); setVersion(''); onChange(); } });
  const published = templates.data?.filter(row => (row as { status?: string }).status !== 'draft') ?? [];
  return <>
    <h3>Formulare trimise în portal</h3>
    <form className="pp-inline" onSubmit={(event: FormEvent) => { event.preventDefault(); if (version) send.mutate(); }}>
      <label>Formular<select value={version} onChange={event => setVersion(event.target.value)}><option value="">Alege un formular publicat</option>
        {published.map(row => <option key={row.version_id} value={row.version_id}>{row.name} (v{row.version_number})</option>)}</select></label>
      <button disabled={!version || send.isPending}>{send.isPending ? 'Se trimite…' : 'Trimite în portal'}</button>
    </form>
    {send.isError && <p className="error" role="alert">{apiErrorMessage(send.error)}</p>}
    {forms.length === 0 && <p className="muted">Niciun formular trimis în portal.</p>}
    <div className="pp-list">{forms.map(form => <article key={form.id} className="pp-row pp-column">
      <div><strong>{form.name}</strong><span>Trimis la {when(form.sent_at)} · stare: {form.status}</span></div>
      {form.submissions.map(submission => <details key={submission.revision_number} className="pp-submission">
        <summary><span className="pp-badge">{submission.label}</span> revizia {submission.revision_number}, {when(submission.submitted_at)}, de {submission.completed_by}</summary>
        {canSeeAnswers && submission.answers ? <dl>{Object.entries(submission.answers).map(([question, answer]) => <div key={question}><dt>{question}</dt><dd>{Array.isArray(answer) ? answer.join(', ') : answer === true ? 'Da' : answer === false ? 'Nu' : String(answer)}</dd></div>)}</dl>
          : <p className="muted">Răspunsurile se văd în formularele pacientului, de către medic.</p>}
      </details>)}
    </article>)}</div>
  </>;
}
