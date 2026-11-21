import { useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiErrorMessage, listProcedures } from '../api/client';
import { getAppointmentProcedures, replaceAppointmentProcedures } from './api';

const CLOSED = new Set(['completed', 'cancelled', 'no_show']);
export function AppointmentProceduresPanel({ appointmentId, status, canManage }: { appointmentId: string; status: string; canManage: boolean }) {
  const client = useQueryClient(); const queryKey = ['appointment-procedures', appointmentId]; const query = useQuery({ queryKey, queryFn: () => getAppointmentProcedures(appointmentId) });
  const [search, setSearch] = useState(''); const catalog = useQuery({ queryKey: ['procedures', 'active', 'appointment-panel', search], queryFn: () => listProcedures(search || undefined, true, 100) });
  const [draft, setDraft] = useState<Record<string, number> | null>(null); const [selected, setSelected] = useState(''); const [error, setError] = useState(''); const locked = useRef(false);
  const base = query.data?.procedures ?? []; const editable = draft ?? Object.fromEntries(base.filter(line => line.manual_quantity !== null).map(line => [line.procedure_catalog_id, line.manual_quantity as number]));
  const mutation = useMutation({ mutationFn: () => replaceAppointmentProcedures(appointmentId, query.data!.revision, Object.entries(editable).map(([procedure_catalog_id, quantity]) => ({ procedure_catalog_id, quantity }))), onSuccess: async () => { setDraft(null); setError(''); await client.invalidateQueries({ queryKey }); }, onError: (cause: unknown) => setError(apiErrorMessage(cause)) });
  const readonly = !canManage || CLOSED.has(status); const options = useMemo(() => (catalog.data ?? []).filter(item => item.is_active && !Object.prototype.hasOwnProperty.call(editable, item.id)), [catalog.data, editable]);
  const displayed = base.flatMap(line => {
    const manual = Object.prototype.hasOwnProperty.call(editable, line.procedure_catalog_id) ? editable[line.procedure_catalog_id] : null;
    if (manual === null && line.plan_quantity === null) return [];
    return [{ ...line, quantity: Math.max(manual ?? 0, line.plan_quantity ?? 0), manual_quantity: manual,
      source: manual !== null && line.plan_quantity !== null ? 'both' as const : manual !== null ? 'manual' as const : 'plan' as const }];
  });
  for (const [id, quantity] of Object.entries(editable)) if (!base.some(line => line.procedure_catalog_id === id)) {
    const item = catalog.data?.find(procedure => procedure.id === id);
    if (item) displayed.push({ procedure_catalog_id: id, code: item.code, name: item.name, quantity, manual_quantity: quantity, plan_quantity: null, source: 'manual' });
  }
  const add = () => { if (!selected) return; setDraft({ ...editable, [selected]: 1 }); setSelected(''); };
  const save = () => { if (locked.current || !query.data) return; locked.current = true; setError(''); mutation.mutate(undefined, { onSettled: () => { locked.current = false; } }); };
  return <section className="card" aria-label="Proceduri planificate"><h3>Proceduri planificate</h3>{query.isLoading && <p className="muted">Se încarcă procedurile…</p>}{query.isError && <p className="error" role="alert">{apiErrorMessage(query.error)}</p>}{query.data && <><ul>{displayed.map(line => <li key={line.procedure_catalog_id}>{line.name} ({line.code}) — {line.quantity} · {line.source === 'both' ? 'programare și plan' : line.source === 'plan' ? 'plan' : 'programare'}{!readonly && line.manual_quantity !== null && <><label>Cantitate<input aria-label={`Cantitate ${line.name}`} type="number" min={1} max={32} value={editable[line.procedure_catalog_id] ?? ''} onChange={event => setDraft({ ...editable, [line.procedure_catalog_id]: Number(event.target.value) })} /></label><button type="button" className="secondary" onClick={() => { const next = { ...editable }; delete next[line.procedure_catalog_id]; setDraft(next); }}>Elimină</button></>}</li>)}</ul>{!readonly && <><div className="inline-form"><input aria-label="Caută procedura" value={search} onChange={event => setSearch(event.target.value)} placeholder="Caută în catalog" /><select aria-label="Procedură activă" value={selected} onChange={event => setSelected(event.target.value)}><option value="">Alege procedura</option>{options.map(item => <option key={item.id} value={item.id}>{item.name} ({item.code})</option>)}</select><button type="button" className="secondary" onClick={add} disabled={!selected}>Adaugă</button></div><button type="button" disabled={mutation.isPending || !query.isSuccess} onClick={save}>{mutation.isPending ? 'Se salvează…' : 'Salvează procedurile'}</button>{error && <p className="error" role="alert">{error}{(mutation.error as { response?: { status?: number } } | null)?.response?.status === 409 && <button type="button" className="secondary" onClick={() => { setDraft(null); setError(''); void query.refetch(); }}>Reîncarcă programarea</button>}</p>}</>}</>}</section>;
}
