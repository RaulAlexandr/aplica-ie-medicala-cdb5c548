import { useEffect, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { apiErrorMessage, createProcedure, updateProcedure, type ProcedureCatalog } from '../api/client';
import { ALLOWED_LOCATION_OPTIONS, type AllowedLocation, ruleOf } from './locations';

function ErrorState({ error }: { error: unknown }) { return <p className="error" role="alert">{apiErrorMessage(error)}</p>; }

export function CatalogProcedureForm({ initial, onSaved, onCancel }: { initial?: ProcedureCatalog; onSaved: (procedure: ProcedureCatalog) => void; onCancel: () => void }) {
  const [form, setForm] = useState<{ code: string; name: string; description: string; category: string; default_duration_minutes: number; base_price: string; currency: string; allowed_location: AllowedLocation }>({
    code: initial?.code ?? '',
    name: initial?.name ?? '',
    description: initial?.description ?? '',
    category: initial?.category ?? '',
    default_duration_minutes: initial?.default_duration_minutes ?? 30,
    base_price: initial?.base_price ?? '',
    currency: initial?.currency ?? 'RON',
    allowed_location: ruleOf(initial)
  });
  useEffect(() => {
    if (initial) {
      setForm({
        code: initial.code ?? '',
        name: initial.name ?? '',
        description: initial.description ?? '',
        category: initial.category ?? '',
        default_duration_minutes: initial.default_duration_minutes ?? 30,
        base_price: initial.base_price ?? '',
        currency: initial.currency ?? 'RON',
        allowed_location: ruleOf(initial)
      });
    } else {
      setForm({
        code: '',
        name: '',
        description: '',
        category: '',
        default_duration_minutes: 30,
        base_price: '',
        currency: 'RON',
        allowed_location: 'any'
      });
    }
  }, [initial]);
  const mutation = useMutation({ mutationFn: async () => { const payload = { ...form, base_price: form.base_price }; return initial ? updateProcedure(initial.id, payload) : createProcedure(payload); }, onSuccess: onSaved });
  const set = (key: keyof typeof form, value: string | number) => setForm(current => ({ ...current, [key]: value }));
  return <form className="card" onSubmit={e => { e.preventDefault(); mutation.mutate(); }}>
    <h2>{initial ? 'Edit procedure' : 'New procedure'}</h2>
    <div className="grid two">
      <label>Code<input required value={form.code} onChange={e => set('code', e.target.value)} /></label>
      <label>Name<input required value={form.name} onChange={e => set('name', e.target.value)} /></label>
      <label>Category<input value={form.category} onChange={e => set('category', e.target.value)} /></label>
      <label>Currency<input required value={form.currency} onChange={e => set('currency', e.target.value)} /></label>
      <label>Duration (minutes)<input type="number" min={1} max={1440} required value={form.default_duration_minutes} onChange={e => set('default_duration_minutes', Number(e.target.value))} /></label>
      <label>Base price<input type="text" inputMode="decimal" pattern="[0-9]*\.?[0-9]*" required value={form.base_price} onChange={e => set('base_price', e.target.value)} /></label>
      <label>Localizări permise<select aria-label="Localizări permise" value={form.allowed_location} onChange={e => set('allowed_location', e.target.value)}>
        {ALLOWED_LOCATION_OPTIONS.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select></label>
    </div>
    {initial && ruleOf(initial) !== form.allowed_location && <p className="muted">Regula nouă se aplică doar manoperelor și planurilor create de acum înainte; înregistrările existente rămân neschimbate.</p>}
    <label>Description<textarea value={form.description} onChange={e => set('description', e.target.value)} /></label>
    {mutation.isError && <ErrorState error={mutation.error} />}
    <div className="form-actions">
      <button type="button" className="secondary" onClick={onCancel} disabled={mutation.isPending}>Cancel</button>
      <button disabled={mutation.isPending}>{mutation.isPending ? 'Saving\u2026' : 'Save procedure'}</button>
    </div>
  </form>;
}
