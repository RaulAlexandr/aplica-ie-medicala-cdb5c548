import { ChangeEvent, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiErrorMessage } from '../api/client';
import { confirmCsv, downloadCsv, ImportRun, listImportRuns, Preview, previewCsv } from './api';
import './import-export.css';

type Kind = 'patients' | 'appointments';
const fields: Record<Kind, { key: string; label: string; required?: boolean }[]> = {
  patients: [
    { key: 'last_name', label: 'Nume', required: true }, { key: 'first_name', label: 'Prenume', required: true },
    { key: 'date_of_birth', label: 'Data nașterii' }, { key: 'phone', label: 'Telefon' }, { key: 'email', label: 'Email' },
    { key: 'cnp', label: 'CNP' }, { key: 'external_id', label: 'Identificator extern' }, { key: 'notes', label: 'Note' },
  ],
  appointments: [
    { key: 'patient_last_name', label: 'Numele pacientului', required: true }, { key: 'patient_first_name', label: 'Prenumele pacientului', required: true },
    { key: 'patient_phone', label: 'Telefon pacient' }, { key: 'doctor', label: 'Medic', required: true }, { key: 'room', label: 'Cabinet', required: true },
    { key: 'date', label: 'Data', required: true }, { key: 'time', label: 'Ora', required: true }, { key: 'duration_minutes', label: 'Durata în minute', required: true },
    { key: 'appointment_type', label: 'Tipul programării', required: true }, { key: 'notes', label: 'Note' },
  ],
};
const keyNorm = (value: string) => value.toLocaleLowerCase('ro').normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^a-z0-9]/g, '');
const aliases: Record<string, string[]> = { last_name: ['nume', 'lastname'], first_name: ['prenume', 'firstname'], date_of_birth: ['datanașterii', 'datanasterii', 'dateofbirth'], phone: ['telefon', 'phone'], email: ['email'], cnp: ['cnp'], external_id: ['identificatorextern', 'externalid'], patient_last_name: ['numele pacientului', 'nume pacient'], patient_first_name: ['prenumele pacientului', 'prenume pacient'], patient_phone: ['telefon pacient'], doctor: ['medic', 'doctor'], room: ['cabinet', 'room'], date: ['data', 'date'], time: ['ora', 'time'], duration_minutes: ['durata in minute', 'durationminutes'], appointment_type: ['tipul programarii', 'tip programare', 'appointmenttype'], notes: ['note', 'notes'] };

function parseHeaders(file: File): Promise<string[]> {
  return file.text().then(text => {
    const first = text.replace(/^\uFEFF/, '').split(/\r?\n/, 1)[0] ?? '';
    const delimiter = first.split(';').length > first.split(',').length ? ';' : ',';
    return first.split(delimiter).map(value => value.trim().replace(/^"|"$/g, ''));
  });
}

export function ImportExportPage() {
  const [kind, setKind] = useState<Kind>('patients');
  const [file, setFile] = useState<File | null>(null);
  const [headers, setHeaders] = useState<string[]>([]);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<Preview | null>(null);
  const [checked, setChecked] = useState<number[]>([]);
  const [notice, setNotice] = useState('');
  const [start, setStart] = useState('2026-01-01');
  const [end, setEnd] = useState(new Date().toISOString().slice(0, 10));
  const client = useQueryClient();
  const runs = useQuery({ queryKey: ['import-export-runs'], queryFn: listImportRuns });
  const previewMutation = useMutation({ mutationFn: () => previewCsv(kind, file!, mapping), onSuccess: data => { setPreview(data); setChecked([]); setNotice('Verifică rândurile și confirmă importul.'); }, onError: error => setNotice(apiErrorMessage(error)) });
  const confirmMutation = useMutation({ mutationFn: () => confirmCsv(kind, file!, mapping, checked), onSuccess: (run: ImportRun) => { setNotice(`Import terminat: ${run.created} create, ${run.updated} actualizate.`); setPreview(null); setFile(null); setHeaders([]); client.invalidateQueries({ queryKey: ['import-export-runs'] }); }, onError: error => setNotice(apiErrorMessage(error)) });
  const canPreview = useMemo(() => !!file && fields[kind].filter(field => field.required).every(field => !!mapping[field.key]), [file, kind, mapping]);

  const selectFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const next = event.target.files?.[0] ?? null;
    setFile(next); setPreview(null); setNotice('');
    if (!next) return;
    const names = await parseHeaders(next);
    setHeaders(names);
    const nextMapping: Record<string, string> = {};
    for (const field of fields[kind]) {
      const candidates = [keyNorm(field.key), ...((aliases[field.key] ?? []).map(keyNorm)), keyNorm(field.label)];
      const header = names.find(item => candidates.includes(keyNorm(item)));
      if (header) nextMapping[field.key] = header;
    }
    setMapping(nextMapping);
  };
  const changeKind = (next: Kind) => { setKind(next); setFile(null); setHeaders([]); setPreview(null); setMapping({}); setNotice(''); };
  const exportFile = async (exportKind: Kind) => {
    try {
      const blob = await downloadCsv(exportKind, start, end);
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob); link.download = `${exportKind}-${start}-${end}.csv`; link.click(); URL.revokeObjectURL(link.href);
    } catch (error) { setNotice(apiErrorMessage(error)); }
  };

  return <main className="import-export-page">
    <header><div><p className="eyebrow">ADMINISTRARE DATE</p><h1>Import și export</h1></div></header>
    <section className="ie-card">
      <h2>Import CSV</h2>
      <div className="ie-tabs" role="group" aria-label="Tip import">
        <button type="button" className={kind === 'patients' ? 'selected' : 'secondary'} onClick={() => changeKind('patients')}>Pacienți</button>
        <button type="button" className={kind === 'appointments' ? 'selected' : 'secondary'} onClick={() => changeKind('appointments')}>Programări</button>
      </div>
      <label>1. Încarcă un fișier CSV<input type="file" accept=".csv,text/csv" onChange={event => void selectFile(event)} /></label>
      {file && <>
        <p className="muted">{file.name} · {(file.size / 1024).toFixed(1)} KB</p>
        <h3>2. Potrivește coloanele</h3>
        <div className="ie-mapping">{fields[kind].map(field => <label key={field.key}>{field.label}{field.required ? ' *' : ''}<select value={mapping[field.key] ?? ''} onChange={event => setMapping(current => ({ ...current, [field.key]: event.target.value }))}><option value="">— nu importa —</option>{headers.map(header => <option key={header} value={header}>{header}</option>)}</select></label>)}</div>
        <button disabled={!canPreview || previewMutation.isPending} onClick={() => previewMutation.mutate()}>{previewMutation.isPending ? 'Se verifică…' : '3. Previzualizează'}</button>
      </>}
      {preview && <div className="ie-preview">
        <h3>3. Previzualizare</h3><p>{preview.total} rânduri · {preview.new} noi · {preview.existing} existente/de verificat · {preview.errors} erori</p>
        <div className="ie-table-wrap"><table><thead><tr><th>Rând</th><th>Rezultat</th><th>Detalii</th>{kind === 'patients' && <th>Actualizează</th>}</tr></thead><tbody>{preview.rows.map(row => <tr key={row.row_number}><td>{row.row_number}</td><td>{row.status === 'new' ? 'Nou' : row.status === 'review' ? 'De verificat' : row.status === 'error' ? 'Eroare' : 'Există deja'}</td><td>{row.message ?? 'Pregătit pentru import'}</td>{kind === 'patients' && <td>{row.status === 'review' ? <input aria-label={`Actualizează rândul ${row.row_number}`} type="checkbox" checked={checked.includes(row.row_number)} onChange={() => setChecked(current => current.includes(row.row_number) ? current.filter(n => n !== row.row_number) : [...current, row.row_number])} /> : '—'}</td>}</tr>)}</tbody></table></div>
        <button disabled={confirmMutation.isPending || preview.errors === preview.total} onClick={() => confirmMutation.mutate()}>{confirmMutation.isPending ? 'Se importă…' : '4. Confirmă importul'}</button>
      </div>}
      {notice && <p role="status" className="ie-notice">{notice}</p>}
    </section>
    <section className="ie-card">
      <h2>Export CSV</h2><p>Exportul este separat pe perioadă și se înregistrează în audit.</p>
      <div className="ie-export"><label>De la<input type="date" value={start} onChange={event => setStart(event.target.value)} /></label><label>Până la<input type="date" value={end} onChange={event => setEnd(event.target.value)} /></label><button disabled={end < start} onClick={() => void exportFile('patients')}>Exportă pacienți</button><button className="secondary" disabled={end < start} onClick={() => void exportFile('appointments')}>Exportă programări</button></div>
    </section>
    <section className="ie-card"><h2>Importuri recente</h2>{runs.isLoading ? <p>Se încarcă…</p> : runs.isError ? <p role="alert">Importurile nu au putut fi încărcate.</p> : runs.data?.length ? <ul className="ie-runs">{runs.data.map(run => <li key={run.id}><strong>{run.filename}</strong><span>{run.kind === 'patients' ? 'Pacienți' : 'Programări'} · {new Date(run.created_at).toLocaleString('ro-RO')} · {run.created} create, {run.updated} actualizate</span><details><summary>Vezi rezultatul pe rând</summary><ul>{run.rows.map(row => <li key={row.row_number}>Rândul {row.row_number}: {row.status}{row.message ? ` · ${row.message}` : ''}{row.entity_id ? ` · ${row.entity_type} ${row.entity_id}` : ''}</li>)}</ul></details></li>)}</ul> : <p>Nu există importuri.</p>}</section>
  </main>;
}
