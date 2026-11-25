import { FormEvent, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiErrorMessage, type User } from '../api/client';
import { listEquipment } from '../equipment/api';
import {
  canWriteSterilization, createPack, createSetType, cycleStatusLabel, decideCycle, downloadRecallCsv, finishCycle, formatDateTime, getCycle, getRecall,
  indicatorLabel, listCycles, listPacks, listSetTypes, packStateLabel, recordIndicator, startCycle,
  type Cycle, type IndicatorResult, type Pack,
} from './api';
import './sterilization.css';

type Tab = 'cycles' | 'packs' | 'recalls';
const SETTLED = new Set(['unsterile', 'used', 'expired', 'compromised']);
const isSterilizer = (type: string) => ['sterilizator', 'autoclav'].includes(type.trim().toLowerCase());
const stateTone = (state: string) => (state === 'sterile' || state === 'approved' || state === 'pass') ? 'ok' : (state === 'compromised' || state === 'rejected' || state === 'fail' || state === 'expired') ? 'bad' : state === 'in_cycle' || state === 'in_progress' || state === 'finished' || state === 'pending' ? 'warn' : '';
const Badge = ({ tone, children }: { tone: string; children: string }) => <span className={`ster-badge ${tone}`}>{children}</span>;

function Result({ label, value }: { label: string; value: IndicatorResult | null }) {
  return <p>{label}: {value ? <Badge tone={stateTone(value)}>{indicatorLabel[value]}</Badge> : '—'}</p>;
}

function NewCycleForm({ packs, onDone }: { packs: Pack[]; onDone: (cycle: Cycle) => void }) {
  const equipment = useQuery({ queryKey: ['equipment'], queryFn: listEquipment });
  const [equipmentId, setEquipmentId] = useState('');
  const [program, setProgram] = useState('134°C / 18 min');
  const [selected, setSelected] = useState<string[]>([]);
  const start = useMutation({ mutationFn: startCycle, onSuccess: cycle => { setSelected([]); onDone(cycle); } });
  const devices = (equipment.data ?? []).filter(item => isSterilizer(item.equipment_type) && item.is_active);
  const ready = packs.filter(pack => SETTLED.has(pack.state));
  const submit = (event: FormEvent) => { event.preventDefault(); if (equipmentId && selected.length) start.mutate({ equipment_id: equipmentId, program: program.trim(), pack_ids: selected }); };
  return <form className="card ster-form" aria-label="Ciclu nou" onSubmit={submit}>
    <h2 className="wide">Ciclu nou</h2>
    <label>Sterilizator<select value={equipmentId} onChange={event => setEquipmentId(event.target.value)} required><option value="">Alege aparatul</option>{devices.map(item => <option key={item.id} value={item.id}>{item.name}{item.state !== 'available' ? ` (${item.state})` : ''}</option>)}</select></label>
    <label>Program<input value={program} onChange={event => setProgram(event.target.value)} required maxLength={120} /></label>
    <fieldset className="wide"><legend>Pachete care intră în ciclu (înainte de început)</legend>
      <div className="ster-checks">{ready.length === 0 && <p className="muted">Nu există pachete de sterilizat. Adaugă pachete în tabul Pachete.</p>}
        {ready.map(pack => <label key={pack.id}><input type="checkbox" checked={selected.includes(pack.id)} onChange={event => setSelected(event.target.checked ? [...selected, pack.id] : selected.filter(id => id !== pack.id))} />{pack.code} · {pack.set_type_name} <span className="muted">({packStateLabel[pack.state]})</span></label>)}</div></fieldset>
    {start.isError && <p className="error wide" role="alert">{apiErrorMessage(start.error)}</p>}
    <div className="wide"><button type="submit" disabled={start.isPending || !equipmentId || selected.length === 0}>{start.isPending ? 'Se pornește…' : 'Pornește ciclul'}</button></div>
  </form>;
}

function CycleDetail({ id, user, onChanged }: { id: string; user: User; onChanged: () => void }) {
  const client = useQueryClient();
  const query = useQuery({ queryKey: ['sterilization', 'cycle', id], queryFn: () => getCycle(id) });
  const [form, setForm] = useState({ temperature_c: '134.0', pressure_kpa: '210', hold_minutes: '18', chemical_result: 'pass' as IndicatorResult, biological_result: 'pending' as IndicatorResult });
  const [note, setNote] = useState('');
  const [bioResult, setBioResult] = useState<IndicatorResult>('pass');
  const refresh = () => { void client.invalidateQueries({ queryKey: ['sterilization'] }); onChanged(); };
  const finish = useMutation({ mutationFn: () => finishCycle(id, { ...form, hold_minutes: Number(form.hold_minutes) }), onSuccess: refresh });
  const decide = useMutation({ mutationFn: (decision: 'approved' | 'rejected') => decideCycle(id, decision, note), onSuccess: () => { setNote(''); refresh(); } });
  const indicator = useMutation({ mutationFn: () => recordIndicator(id, { kind: 'biological', result: bioResult, note: note.trim() || null }), onSuccess: () => { setNote(''); refresh(); } });
  if (query.isLoading) return <p className="muted">Se încarcă ciclul…</p>;
  if (query.isError || !query.data) return <p className="error" role="alert">{apiErrorMessage(query.error)}</p>;
  const cycle = query.data;
  const writer = canWriteSterilization(user.role);
  const isOperator = cycle.operator_id === user.id;
  return <section className="card ster-detail" aria-label={`Ciclul ${cycle.cycle_number}`}>
    <h2>Ciclul {cycle.cycle_number} · {cycle.equipment_name}</h2>
    <p><Badge tone={stateTone(cycle.status)}>{cycleStatusLabel[cycle.status]}</Badge> {cycle.compromised && <Badge tone="bad">Compromis</Badge>}</p>
    <p>Program: {cycle.program} · început {formatDateTime(cycle.started_at)}{cycle.ended_at ? ` · sfârșit ${formatDateTime(cycle.ended_at)}` : ''}</p>
    <p>Operator: {cycle.operator_name ?? '—'}</p>
    {cycle.temperature_c && <p>Parametri: {cycle.temperature_c} °C · {cycle.pressure_kpa} kPa · {cycle.hold_minutes} min</p>}
    <Result label="Indicator chimic" value={cycle.chemical_result} />
    <Result label="Indicator biologic" value={cycle.biological_result} />
    <p>Pachete ({cycle.pack_count}): {cycle.pack_codes.join(', ')}</p>
    {cycle.decided_at && <p>{cycle.status === 'approved' ? 'Aprobat' : 'Respins'} de {cycle.decided_by_name} la {formatDateTime(cycle.decided_at)}{cycle.decision_note ? ` — ${cycle.decision_note}` : ''}</p>}
    {cycle.compromised && <p className="error" role="status">Ciclul a fost declarat compromis: {cycle.recall_reason}. Vezi tabul Rechemări.</p>}
    {writer && cycle.status === 'in_progress' && <form className="ster-form" aria-label="Încheie ciclul" onSubmit={event => { event.preventDefault(); finish.mutate(); }}>
      <h3 className="wide">Încheie ciclul</h3>
      <label>Temperatură (°C)<input inputMode="decimal" value={form.temperature_c} onChange={event => setForm({ ...form, temperature_c: event.target.value })} required /></label>
      <label>Presiune (kPa)<input inputMode="decimal" value={form.pressure_kpa} onChange={event => setForm({ ...form, pressure_kpa: event.target.value })} required /></label>
      <label>Timp de menținere (min)<input inputMode="numeric" value={form.hold_minutes} onChange={event => setForm({ ...form, hold_minutes: event.target.value })} required /></label>
      <label>Indicator chimic<select value={form.chemical_result} onChange={event => setForm({ ...form, chemical_result: event.target.value as IndicatorResult })}><option value="pass">conform</option><option value="fail">neconform</option><option value="pending">în așteptare</option></select></label>
      <label>Indicator biologic<select value={form.biological_result} onChange={event => setForm({ ...form, biological_result: event.target.value as IndicatorResult })}><option value="pending">în așteptare</option><option value="pass">conform</option><option value="fail">neconform</option></select></label>
      {finish.isError && <p className="error wide" role="alert">{apiErrorMessage(finish.error)}</p>}
      <div className="wide"><button type="submit" disabled={finish.isPending}>Încheie ciclul</button></div>
    </form>}
    {writer && cycle.status === 'finished' && <div className="ster-detail">
      <h3>Decizie</h3>
      {isOperator ? <p className="notice-text" role="status">Operatorul ciclului nu îl poate aproba. Aprobarea o face un alt asistent sau manager.</p>
        : <><label>Observații (opțional)<input value={note} onChange={event => setNote(event.target.value)} maxLength={2000} /></label>
          <div className="ster-actions"><button disabled={decide.isPending} onClick={() => decide.mutate('approved')}>Aprobă ciclul</button><button className="secondary" disabled={decide.isPending} onClick={() => decide.mutate('rejected')}>Respinge ciclul</button></div></>}
      {decide.isError && <p className="error" role="alert">{apiErrorMessage(decide.error)}</p>}
    </div>}
    {writer && cycle.status !== 'in_progress' && !cycle.compromised && <form className="ster-form" aria-label="Rezultat indicator biologic" onSubmit={event => { event.preventDefault(); indicator.mutate(); }}>
      <h3 className="wide">Rezultat indicator biologic</h3>
      <label>Rezultat<select value={bioResult} onChange={event => setBioResult(event.target.value as IndicatorResult)}><option value="pass">conform (negativ)</option><option value="fail">neconform (pozitiv) — declară ciclul compromis</option><option value="pending">în așteptare</option></select></label>
      <label>Observații<input value={note} onChange={event => setNote(event.target.value)} maxLength={2000} /></label>
      {indicator.isError && <p className="error wide" role="alert">{apiErrorMessage(indicator.error)}</p>}
      <div className="wide"><button type="submit" disabled={indicator.isPending}>Înregistrează rezultatul</button></div>
    </form>}
    {(cycle.indicators ?? []).length > 0 && <div><h3>Istoric indicatori</h3>{cycle.indicators!.map((row, index) => <p className="ster-item" key={index}>{row.kind === 'chemical' ? 'Chimic' : 'Biologic'}: {indicatorLabel[row.result]} · {row.recorded_by_name} · {formatDateTime(row.recorded_at)}{row.note ? ` — ${row.note}` : ''}</p>)}</div>}
  </section>;
}

function CyclesTab({ user, packs }: { user: User; packs: Pack[] }) {
  const client = useQueryClient();
  const cycles = useQuery({ queryKey: ['sterilization', 'cycles'], queryFn: listCycles });
  const [selected, setSelected] = useState('');
  const refresh = () => { void client.invalidateQueries({ queryKey: ['sterilization'] }); };
  return <>
    {canWriteSterilization(user.role) && <NewCycleForm packs={packs} onDone={cycle => { setSelected(cycle.id); refresh(); }} />}
    <div className="ster-layout">
      <div className="ster-list" aria-label="Cicluri">
        {cycles.isLoading && <p className="muted">Se încarcă ciclurile…</p>}
        {cycles.isError && <p className="error" role="alert">{apiErrorMessage(cycles.error)}</p>}
        {cycles.data?.length === 0 && <p className="muted">Niciun ciclu înregistrat.</p>}
        {cycles.data?.map(cycle => <button key={cycle.id} type="button" className="ster-row" aria-current={selected === cycle.id} onClick={() => setSelected(cycle.id)}>
          <strong>Ciclul {cycle.cycle_number} · {cycle.equipment_name}</strong>
          <span>{cycleStatusLabel[cycle.status]}{cycle.compromised ? ' · COMPROMIS' : ''} · {formatDateTime(cycle.started_at)}</span>
          <span>{cycle.pack_count} pachete · operator {cycle.operator_name}</span></button>)}
      </div>
      {selected ? <CycleDetail key={selected} id={selected} user={user} onChanged={refresh} /> : <p className="muted">Alege un ciclu pentru detalii.</p>}
    </div>
  </>;
}

function PacksTab({ user, packs }: { user: User; packs: Pack[] }) {
  const client = useQueryClient();
  const setTypes = useQuery({ queryKey: ['sterilization', 'set-types'], queryFn: listSetTypes });
  const [typeForm, setTypeForm] = useState({ name: '', description: '', expiry_days: '30' });
  const [packForm, setPackForm] = useState({ set_type_id: '', code: '' });
  const refresh = () => { void client.invalidateQueries({ queryKey: ['sterilization'] }); };
  const addType = useMutation({ mutationFn: () => createSetType({ name: typeForm.name.trim(), description: typeForm.description.trim() || null, expiry_days: Number(typeForm.expiry_days) }), onSuccess: () => { setTypeForm({ name: '', description: '', expiry_days: '30' }); refresh(); } });
  const addPack = useMutation({ mutationFn: () => createPack({ set_type_id: packForm.set_type_id, code: packForm.code.trim() }), onSuccess: () => { setPackForm({ ...packForm, code: '' }); refresh(); } });
  const writer = canWriteSterilization(user.role);
  return <div className="ster-layout">
    <div className="ster-detail">
      <section className="card ster-card"><h2>Tipuri de set</h2>
        {setTypes.data?.length === 0 && <p className="muted">Niciun tip de set. Creează întâi un tip (de exemplu „Set consultație”).</p>}
        {setTypes.data?.map(item => <p className="ster-item" key={item.id}><strong>{item.name}</strong><span className="muted">{item.description ?? 'fără descriere'} · expiră după {item.expiry_days} zile</span></p>)}
        {writer && <form className="ster-form" aria-label="Tip de set nou" onSubmit={event => { event.preventDefault(); addType.mutate(); }}>
          <label>Nume<input value={typeForm.name} onChange={event => setTypeForm({ ...typeForm, name: event.target.value })} required maxLength={120} /></label>
          <label>Conținut (descriere)<input value={typeForm.description} onChange={event => setTypeForm({ ...typeForm, description: event.target.value })} /></label>
          <label>Valabilitate (zile)<input inputMode="numeric" value={typeForm.expiry_days} onChange={event => setTypeForm({ ...typeForm, expiry_days: event.target.value })} required /></label>
          {addType.isError && <p className="error wide" role="alert">{apiErrorMessage(addType.error)}</p>}
          <div className="wide"><button type="submit" disabled={addType.isPending}>Adaugă tipul</button></div></form>}
      </section>
      {writer && <form className="card ster-form" aria-label="Pachet nou" onSubmit={event => { event.preventDefault(); if (packForm.set_type_id) addPack.mutate(); }}>
        <h2 className="wide">Pachet fizic nou</h2>
        <label>Tip de set<select value={packForm.set_type_id} onChange={event => setPackForm({ ...packForm, set_type_id: event.target.value })} required><option value="">Alege tipul</option>{setTypes.data?.filter(item => item.is_active).map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        <label>Cod unic în clinică<input value={packForm.code} onChange={event => setPackForm({ ...packForm, code: event.target.value })} required maxLength={60} /></label>
        {addPack.isError && <p className="error wide" role="alert">{apiErrorMessage(addPack.error)}</p>}
        <div className="wide"><button type="submit" disabled={addPack.isPending || !packForm.set_type_id}>Adaugă pachetul</button></div></form>}
    </div>
    <section className="card ster-card" aria-label="Pachete"><h2>Pachete</h2>
      <p className="muted">Starea fiecărui pachet rezultă din istoricul lui (ciclu, aprobare, folosire), nu dintr-o bifă.</p>
      {packs.length === 0 && <p className="muted">Niciun pachet.</p>}
      {packs.map(pack => <p className="ster-item" key={pack.id}><strong>{pack.code} · {pack.set_type_name}</strong>
        <span><Badge tone={stateTone(pack.state)}>{packStateLabel[pack.state]}</Badge>{pack.cycle_number ? ` ciclul ${pack.cycle_number} · ${pack.equipment_name}` : ''}{pack.expires_at ? ` · expiră ${formatDateTime(pack.expires_at)}` : ''}</span></p>)}
    </section>
  </div>;
}

function RecallsTab() {
  const cycles = useQuery({ queryKey: ['sterilization', 'cycles'], queryFn: listCycles });
  const [selected, setSelected] = useState('');
  const [csvError, setCsvError] = useState('');
  const compromised = (cycles.data ?? []).filter(cycle => cycle.compromised);
  const recall = useQuery({ queryKey: ['sterilization', 'recall', selected], queryFn: () => getRecall(selected), enabled: Boolean(selected) });
  const exportCsv = async () => {
    setCsvError('');
    try {
      const blob = await downloadRecallCsv(selected);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url; link.download = 'rechemare.csv'; link.click();
      URL.revokeObjectURL(url);
    } catch (error) { setCsvError(apiErrorMessage(error)); }
  };
  return <div className="ster-layout">
    <div className="ster-list" aria-label="Cicluri compromise">
      {cycles.isLoading && <p className="muted">Se încarcă…</p>}
      {!cycles.isLoading && compromised.length === 0 && <p className="muted">Niciun ciclu compromis. Un ciclu devine compromis când indicatorul biologic iese pozitiv după aprobare.</p>}
      {compromised.map(cycle => <button key={cycle.id} type="button" className="ster-row" aria-current={selected === cycle.id} onClick={() => setSelected(cycle.id)}>
        <strong>Ciclul {cycle.cycle_number} · {cycle.equipment_name}</strong><span>{cycle.recall_reason}</span></button>)}
    </div>
    {selected && recall.data ? <section className="card ster-detail" aria-label="Rechemare">
      <h2>Rechemare: ciclul {recall.data.cycle.cycle_number}</h2>
      <p className="error" role="status">Motiv: {recall.data.reason}</p>
      <p>{recall.data.items.length} {recall.data.items.length === 1 ? 'programare' : 'programări'} cu pachete din acest ciclu. {recall.data.unused_packs.length > 0 && `Pachete nefolosite, acum compromise: ${recall.data.unused_packs.join(', ')}.`}</p>
      <div className="ster-actions"><button type="button" onClick={() => void exportCsv()} disabled={recall.data.items.length === 0}>Exportă CSV</button></div>
      {csvError && <p className="error" role="alert">{csvError}</p>}
      {recall.data.items.map(item => <article className="ster-item" key={`${item.pack_id}:${item.appointment_id}`}>
        <strong><Link to={`/patients/${item.patient_id}`}>{item.patient_name}</Link></strong>
        <span>Programare {formatDateTime(item.appointment_starts_at)} · {item.doctor_name}</span><span>Pachet {item.pack_code} ({item.set_type})</span><span className="muted">{item.reason}</span></article>)}
    </section> : <p className="muted">{selected ? (recall.isError ? apiErrorMessage(recall.error) : 'Se încarcă…') : 'Alege un ciclu compromis pentru lista de pacienți.'}</p>}
  </div>;
}

export function SterilizationPage({ user }: { user: User }) {
  const [tab, setTab] = useState<Tab>('cycles');
  const packs = useQuery({ queryKey: ['sterilization', 'packs'], queryFn: listPacks });
  const tabs: Array<[Tab, string]> = [['cycles', 'Cicluri'], ['packs', 'Pachete'], ['recalls', 'Rechemări']];
  return <div className="ster-page">
    <header><p className="eyebrow">STERILIZARE</p><h1>Sterilizare</h1></header>
    <p className="ster-note">Trasabilitatea vine din ciclurile aprobate (aparat, parametri, indicatori și persoana care a aprobat), nu din bife de checklist. Un pachet este steril numai după aprobarea ciclului lui de către altă persoană decât operatorul.</p>
    <div className="ster-tabs" role="group" aria-label="Secțiuni sterilizare">{tabs.map(([key, label]) => <button key={key} type="button" aria-pressed={tab === key} onClick={() => setTab(key)}>{label}</button>)}</div>
    {packs.isError && <p className="error" role="alert">{apiErrorMessage(packs.error)}</p>}
    {tab === 'cycles' && <CyclesTab user={user} packs={packs.data ?? []} />}
    {tab === 'packs' && <PacksTab user={user} packs={packs.data ?? []} />}
    {tab === 'recalls' && <RecallsTab />}
  </div>;
}
