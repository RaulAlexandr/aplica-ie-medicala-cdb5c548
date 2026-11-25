import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getClinic, listProcedures, listRooms, listStaff } from '../api/client';
import { clinicLocalToUtc } from '../timezone';
import { AutomationCondition, AutomationDefinition, AutomationExecution, AutomationRule, AUTOMATION_FIELDS, AUTOMATION_TRIGGERS, activateAutomation, createAutomationRule, decideAutomation, evaluateAutomations, listAutomationExecutions, listAutomationRules, listPendingAutomations, simulateAutomation, stopAutomation, updateAutomationRule } from './api';
import './automations.css';

const roles = [['doctor', 'Medic'], ['assistant', 'Asistent'], ['reception', 'Recepție'], ['clinic_manager', 'Manager clinică'], ['administrator', 'Administrator']] as const;
const operators = [['eq', 'este'], ['ne', 'nu este'], ['gt', 'mai mare decât'], ['gte', 'cel puțin'], ['lt', 'mai mic decât'], ['lte', 'cel mult']] as const;
const blank = (): AutomationDefinition => ({ name: '', trigger: 'appointment_created', conditions: [], action: { target_type: 'role', target_value: 'doctor', title_template: 'Verifică {{appointment.type}}', due_in_days: 2 }, mode: 'approval' });
const triggerName = (trigger: string) => AUTOMATION_TRIGGERS.find(([value]) => value === trigger)?.[1] ?? trigger;

function ErrorText({ error }: { error: unknown }) { return <p className="error" role="alert">{error instanceof Error ? error.message : 'Operația nu a reușit.'}</p>; }

export function AutomationsPage() {
  const queryClient = useQueryClient();
  const rules = useQuery({ queryKey: ['automations'], queryFn: listAutomationRules });
  const pending = useQuery({ queryKey: ['automations', 'pending'], queryFn: listPendingAutomations });
  const staff = useQuery({ queryKey: ['staff', 'automations'], queryFn: listStaff });
  const clinic = useQuery({ queryKey: ['clinic'], queryFn: getClinic });
  const procedures = useQuery({ queryKey: ['procedures', 'active', 'automations'], queryFn: () => listProcedures(undefined, true, 100) });
  const rooms = useQuery({ queryKey: ['rooms'], queryFn: listRooms });
  const [form, setForm] = useState<AutomationDefinition>(blank());
  const [editing, setEditing] = useState<AutomationRule | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [simulation, setSimulation] = useState<{ matches: number; events: Array<{ event_id: string; event_type: string; occurred_at: string; title: string; conditions: AutomationExecution['conditions'] }>; days: number } | null>(null);
  const [startAt, setStartAt] = useState('');
  const [startFold, setStartFold] = useState('');
  const [error, setError] = useState<unknown>(null);
  const changed = () => { setError(null); void queryClient.invalidateQueries({ queryKey: ['automations'] }); };
  const save = useMutation({ mutationFn: () => editing ? updateAutomationRule(editing.id, form) : createAutomationRule(form), onSuccess: rule => { setEditing(null); setForm(blank()); setSelected(rule.id); changed(); } });
  const evaluate = useMutation({ mutationFn: evaluateAutomations, onSuccess: result => { setError(null); void queryClient.invalidateQueries({ queryKey: ['automations'] }); setNotice(`Au fost create ${result.created} execuții din ${result.active_rules} reguli active.`); } });
  const [notice, setNotice] = useState('');
  const simulate = useMutation({ mutationFn: () => simulateAutomation({ ...form, days: 30 }), onSuccess: setSimulation });
  const decide = useMutation({ mutationFn: ({ id, decision }: { id: string; decision: 'approve' | 'reject' }) => decideAutomation(id, decision), onSuccess: changed });
  const activePeople = useMemo(() => (staff.data ?? []).filter(person => person.is_active), [staff.data]);
  const selectRule = (rule: AutomationRule) => { setSelected(rule.id); setEditing(rule); setForm({ name: rule.name, trigger: rule.trigger, conditions: rule.conditions, action: { ...rule.action }, mode: rule.mode }); setSimulation(null); setError(null); };
  const updateCondition = (index: number, patch: Partial<AutomationCondition>) => setForm(current => ({ ...current, conditions: current.conditions.map((condition, at) => at === index ? { ...condition, ...patch } : condition) }));
  const ruleExecutions = useQuery({ queryKey: ['automations', selected, 'executions'], queryFn: () => listAutomationExecutions(selected!), enabled: Boolean(selected) });
  const start = (rule: AutomationRule) => {
    try {
      const startUtc = startAt ? clinicLocalToUtc(startAt, clinic.data?.timezone ?? 'Europe/Bucharest', startFold === '' ? undefined : Number(startFold) as 0 | 1) : undefined;
      void activateAutomation(rule.id, startUtc).then(changed).catch(setError);
    } catch (cause) { setError(cause); }
  };
  const stop = (rule: AutomationRule) => stopAutomation(rule.id).then(changed).catch(setError);
  const clearEditor = () => { setEditing(null); setForm(blank()); setSelected(null); setSimulation(null); setError(null); };

  return <div className="automations-page">
    <header><div><p className="eyebrow">AUTOMATIZĂRI CLINICĂ</p><h1>Automatizări</h1></div><button disabled={evaluate.isPending} onClick={() => evaluate.mutate()}>{evaluate.isPending ? 'Se evaluează…' : 'Evaluează evenimente noi'}</button></header>
    <p className="muted">Regulile procesează evenimente noi când managerul pornește evaluarea. Nu există procesare retroactivă implicită.</p>
    {notice && <p className="automation-notice" role="status">{notice}</p>}{error !== null && <ErrorText error={error} />}{rules.isError && <ErrorText error={rules.error} />}
    <div className="automation-grid">
      <section className="card automation-list"><div className="automation-heading"><h2>Reguli</h2><button className="secondary" onClick={clearEditor}>Regulă nouă</button></div>
        {rules.data?.length === 0 && <p className="muted">Nu există reguli salvate.</p>}
        {rules.data?.map(rule => <article className="automation-rule" key={rule.id}><button className="automation-rule-select" aria-pressed={selected === rule.id} onClick={() => selectRule(rule)}><strong>{rule.name}</strong><span>{triggerName(rule.trigger)} · v{rule.version}</span><small>{rule.active ? 'Activă' : 'Oprită'} · {rule.mode === 'approval' ? 'cu aprobare' : 'automată'}</small></button><div className="automation-row-actions"><button className="secondary" onClick={() => selectRule(rule)}>Editează</button>{rule.active ? <button className="secondary" onClick={() => stop(rule)}>Oprește</button> : <button onClick={() => start(rule)}>Activează</button>}</div></article>)}
      </section>
      <section className="card automation-editor"><h2>{editing ? `Editează v${editing.version} (salvarea creează o versiune nouă)` : 'Regulă nouă'}</h2>
        <form onSubmit={event => { event.preventDefault(); save.mutate(); }}>
          <label>Numele regulii<input required maxLength={160} value={form.name} onChange={event => setForm({ ...form, name: event.target.value })} /></label>
          <label>Declanșator<select value={form.trigger} onChange={event => { const trigger = event.target.value; setForm(current => ({ ...current, trigger, conditions: trigger === 'plan_unbooked' && !current.conditions.some(condition => condition.field === 'days') ? [...current.conditions, { field: 'days', operator: 'gte', value: 3 }] : current.conditions })); }}>{AUTOMATION_TRIGGERS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <fieldset><legend>Condiții (toate trebuie să fie adevărate)</legend>
            {form.conditions.map((condition, index) => <div className="automation-condition" key={index}><select aria-label={`Câmp condiție ${index + 1}`} value={condition.field} onChange={event => updateCondition(index, { field: event.target.value, operator: 'eq', value: '' })}>{AUTOMATION_FIELDS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select><select aria-label={`Operator condiție ${index + 1}`} value={condition.operator} onChange={event => updateCondition(index, { operator: event.target.value })}>{operators.filter(([op]) => condition.field === 'days' || ['eq', 'ne'].includes(op)).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>{condition.field === 'doctor_role' ? <select aria-label={`Valoare condiție ${index + 1}`} value={condition.value} onChange={event => updateCondition(index, { value: event.target.value })}>{roles.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select> : condition.field === 'procedure_id' ? <select aria-label={`Valoare condiție ${index + 1}`} value={condition.value} onChange={event => updateCondition(index, { value: event.target.value })}><option value="">Alege procedura</option>{procedures.data?.map(procedure => <option key={procedure.id} value={procedure.id}>{procedure.name} · {procedure.code}</option>)}</select> : condition.field === 'room_id' ? <select aria-label={`Valoare condiție ${index + 1}`} value={condition.value} onChange={event => updateCondition(index, { value: event.target.value })}><option value="">Alege cabinetul</option>{rooms.data?.map(room => <option key={room.id} value={room.id}>{room.name}</option>)}</select> : <input aria-label={`Valoare condiție ${index + 1}`} type={condition.field === 'days' ? 'number' : 'text'} min={condition.field === 'days' ? 0 : undefined} value={condition.value} onChange={event => updateCondition(index, { value: condition.field === 'days' ? Number(event.target.value) : event.target.value })} />}<button className="secondary" type="button" onClick={() => setForm(current => ({ ...current, conditions: current.conditions.filter((_, at) => at !== index) }))}>Elimină</button></div>)}
            {form.conditions.length < 12 && <button className="secondary" type="button" onClick={() => setForm(current => ({ ...current, conditions: [...current.conditions, { field: 'appointment_type', operator: 'eq', value: '' }] }))}>Adaugă condiție</button>}
          </fieldset>
          <fieldset><legend>Acțiune internă</legend><label>Responsabil<select value={form.action.target_type === 'role' ? `role:${form.action.target_value}` : `user:${form.action.target_value}`} onChange={event => { const [target_type, target_value] = event.target.value.split(':'); setForm(current => ({ ...current, action: { ...current.action, target_type: target_type as 'role' | 'user', target_value } })); }}><optgroup label="Rol">{roles.map(([value, label]) => <option key={value} value={`role:${value}`}>{label}</option>)}</optgroup><optgroup label="Persoană">{activePeople.map(person => <option key={person.id} value={`user:${person.id}`}>{person.full_name} · {person.role}</option>)}</optgroup></select></label><label>Titlul sarcinii<input required maxLength={200} value={form.action.title_template} onChange={event => setForm(current => ({ ...current, action: { ...current.action, title_template: event.target.value } }))} /><small>Variabile: {'{{appointment.type}}, {{patient.name}}, {{procedure.name}}, {{room.name}}, {{days}}, {{plan.title}}, {{item.count}}, {{stock.item}}'}</small></label><label>Termen după declanșare (zile)<input type="number" min={0} max={365} value={form.action.due_in_days} onChange={event => setForm(current => ({ ...current, action: { ...current.action, due_in_days: Number(event.target.value) } }))} /></label><label>Mod<select value={form.mode} onChange={event => setForm({ ...form, mode: event.target.value as AutomationDefinition['mode'] })}><option value="approval">Cu aprobarea managerului</option><option value="automatic">Automată</option></select></label><p className="muted">Acțiunea creează o sarcină internă în Echipă. Mesajele externe nu sunt trimise.</p></fieldset>
          {save.isError && <ErrorText error={save.error} />}{simulate.isError && <ErrorText error={simulate.error} />}<div className="automation-row-actions"><button disabled={save.isPending}>{save.isPending ? 'Se salvează…' : 'Salvează versiunea'}</button><button className="secondary" type="button" disabled={simulate.isPending} onClick={() => simulate.mutate()}>{simulate.isPending ? 'Se simulează…' : 'Simulează ultimele 30 de zile'}</button></div>
        </form>
        {editing && <div className="automation-activation"><label>Data de start opțională ({clinic.data?.timezone ?? 'fusul clinicii'})<input type="datetime-local" value={startAt} onChange={event => setStartAt(event.target.value)} /><small>Lasă goală pentru a procesa doar evenimentele viitoare.</small></label><label>Apariția orei (dacă se repetă)<select value={startFold} onChange={event => setStartFold(event.target.value)}><option value="">Automat</option><option value="0">Prima apariție</option><option value="1">A doua apariție</option></select></label></div>}
        {simulation && <section className="automation-simulation" aria-live="polite"><h3>Simulare: {simulation.matches} potriviri în {simulation.days} zile</h3>{simulation.events.map(event => <p key={event.event_id}><strong>{event.title}</strong><br /><small>{triggerName(event.event_type)} · {new Date(event.occurred_at).toLocaleString()}</small></p>)}</section>}
        {selected && <section className="automation-log"><h3>Jurnal execuții</h3>{ruleExecutions.isLoading && <p className="muted">Se încarcă jurnalul…</p>}{ruleExecutions.data?.map(run => <ExecutionRow key={run.id} run={run} />)}</section>}
      </section>
    </div>
    <section className="card automation-approvals"><h2>Aprobări în așteptare ({pending.data?.length ?? '…'})</h2>{pending.isError && <ErrorText error={pending.error} />}{pending.data?.length === 0 && <p className="muted">Nu există execuții în așteptare.</p>}{pending.data?.map(run => <article className="automation-pending" key={run.id}><div><strong>{run.action.title_template}</strong><p>{triggerName(run.event_type)} · {new Date(run.occurred_at).toLocaleString()}</p><small>Versiunea {run.version_id.slice(0, 8)} · {run.event_id.slice(0, 8)}</small></div><div className="automation-row-actions"><button disabled={decide.isPending} onClick={() => decide.mutate({ id: run.id, decision: 'approve' })}>Aprobă sarcina</button><button className="secondary" disabled={decide.isPending} onClick={() => decide.mutate({ id: run.id, decision: 'reject' })}>Respinge</button></div></article>)}</section>
  </div>;
}

function ExecutionRow({ run }: { run: AutomationExecution }) {
  return <article className="automation-execution"><div><strong>{run.status}</strong> · {triggerName(run.event_type)}<small>{new Date(run.occurred_at).toLocaleString()} · v{run.version_id.slice(0, 8)}</small>{run.reason && <p className="error">{run.reason}</p>}{run.result?.title && <p>{run.result.title}</p>}</div><span>{run.attempt_count}/3 încercări</span></article>;
}
