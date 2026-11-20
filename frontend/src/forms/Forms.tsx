import { useEffect, useState, type FormEvent } from 'react';
import { createPatientForm, createTemplate, createTemplateVersion, getFormRevisions, getPatientForm, listManagerTemplates, listPatientForms, listPublishedForms, publishVersion, reviewForm, saveAnswers, submitForm, updateVersion, type FormDefinition, type PatientForm, type Question } from './api';

const QUESTION_TYPES: Question['type'][] = ['short_text', 'long_text', 'yes_no', 'single_choice', 'multiple_choice', 'date', 'number'];

/** Minimal manager editor; the owner can refine sections/questions before publishing a version. */
export function FormTemplateEditor() {
  const [name, setName] = useState('');
  const [versionId, setVersionId] = useState('');
  const [definition, setDefinition] = useState<FormDefinition>({ sections: [{ id: 'section-1', title: 'Secțiune nouă', questions: [] }] });
  const [message, setMessage] = useState('');
  const [templates, setTemplates] = useState<Awaited<ReturnType<typeof listManagerTemplates>>>([]);
  const [selectedTemplate, setSelectedTemplate] = useState('');
  useEffect(() => { void listManagerTemplates().then(setTemplates).catch(() => undefined); }, []);
  async function start(event: FormEvent) {
    event.preventDefault();
    const result = await createTemplate(name) as { version_id: string };
    setVersionId(result.version_id);
    setMessage('Șablon creat ca versiune ciornă.');
  }
  async function editNextVersion() {
    const versions = templates.filter(template => template.id === selectedTemplate);
    const latest = versions[versions.length - 1];
    if (!latest) return;
    setDefinition(latest.definition?.sections?.length ? latest.definition : definition);
    if (latest.status === 'draft') setVersionId(latest.version_id);
    else {
      const created = await createTemplateVersion(latest.id, latest.definition);
      setVersionId((created as { id: string }).id);
    }
    setName(latest.name);
    setMessage(`Se editează versiunea următoare pentru ${latest.name}.`);
  }
  function addSection() {
    setDefinition(current => ({ ...current, sections: [...current.sections, { id: `section-${current.sections.length + 1}`, title: `Secțiunea ${current.sections.length + 1}`, questions: [] }] }));
  }
  function addQuestion(sectionIndex: number) {
    setDefinition(current => ({ ...current, sections: current.sections.map((section, index) => index === sectionIndex ? { ...section, questions: [...section.questions, { id: `question-${current.sections.reduce((total, item) => total + item.questions.length, 0) + 1}`, label: '', type: 'short_text', required: false }] } : section) }));
  }
  function changeQuestion(sectionIndex: number, index: number, patch: Partial<Question>) {
    setDefinition(current => ({ ...current, sections: current.sections.map((section, currentIndex) => currentIndex === sectionIndex ? { ...section, questions: section.questions.map((question, questionIndex) => questionIndex === index ? { ...question, ...patch } : question) } : section) }));
  }
  async function save() {
    await updateVersion(versionId, definition);
    setMessage('Versiunea a fost salvată.');
  }
  async function publish() {
    await save();
    await publishVersion(versionId);
    setMessage('Versiunea a fost publicată și este imuabilă.');
  }
  return <section aria-label="Editor șabloane formular">
    <h2>Șablon de formular</h2>
    {!versionId && <><form onSubmit={start}><label>Numele șablonului nou <input value={name} onChange={event => setName(event.target.value)} required /></label><button type="submit">Creează șablon</button></form>{templates.length > 0 && <div><label>Șablon existent <select value={selectedTemplate} onChange={event => setSelectedTemplate(event.target.value)}><option value="">Alegeți șablonul</option>{Array.from(new Map(templates.map(template => [template.id, template])).values()).map(template => <option key={template.id} value={template.id}>{template.name}</option>)}</select></label><button type="button" disabled={!selectedTemplate} onClick={() => void editNextVersion()}>Editează ciorna sau creează versiune nouă</button></div>}</>}
    {versionId && <>{definition.sections.map((section, sectionIndex) => <div key={section.id}><label>Titlul secțiunii <input value={section.title} onChange={event => setDefinition(current => ({ ...current, sections: current.sections.map((item, index) => index === sectionIndex ? { ...item, title: event.target.value } : item) }))} /></label>
      {section.questions.map((question, index) => {
        const prior = definition.sections.flatMap((item, index) => index < sectionIndex ? item.questions : index === sectionIndex ? item.questions.slice(0, item.questions.findIndex(entry => entry.id === question.id)) : []);
        const controller = prior.find(item => item.id === question.condition?.question_id);
        const values = controller?.type === 'yes_no' ? ['yes', 'no'] : controller?.options?.map(option => option.value) || [];
        return <fieldset key={question.id}><legend>Întrebarea {index + 1}</legend><label>Text <input value={question.label} onChange={event => changeQuestion(sectionIndex, index, { label: event.target.value })} /></label><label>Tip <select value={question.type} onChange={event => changeQuestion(sectionIndex, index, { type: event.target.value as Question['type'] })}>{QUESTION_TYPES.map(type => <option key={type} value={type}>{type}</option>)}</select></label><label><input type="checkbox" checked={question.required} onChange={event => changeQuestion(sectionIndex, index, { required: event.target.checked })} /> Obligatorie</label>
          {['single_choice', 'multiple_choice'].includes(question.type) && <label>Opțiuni (valoare = etichetă, separate prin virgulă) <input value={(question.options || []).map(option => `${option.value}=${option.label}`).join(', ')} onChange={event => changeQuestion(sectionIndex, index, { options: event.target.value.split(',').map(item => item.trim()).filter(Boolean).map(item => { const [value, ...label] = item.split('='); return { value: value.trim(), label: label.join('=').trim() || value.trim() }; }) })} /></label>}
          {prior.length > 0 && <><label>Afișează dacă <select value={question.condition?.question_id || ''} onChange={event => { const picked = prior.find(item => item.id === event.target.value); changeQuestion(sectionIndex, index, { condition: picked ? { question_id: picked.id, operator: 'equals', value: picked.type === 'yes_no' ? 'yes' : picked.options?.[0]?.value || '' } : undefined }); }}><option value="">Întotdeauna</option>{prior.filter(item => item.type === 'yes_no' || item.type === 'single_choice').map(item => <option key={item.id} value={item.id}>{item.label || item.id}</option>)}</select> are răspunsul</label>{controller && <select aria-label="Valoare condiție" value={question.condition?.value || ''} onChange={event => changeQuestion(sectionIndex, index, { condition: { ...question.condition!, value: event.target.value } })}>{values.map(value => <option key={value} value={value}>{value}</option>)}</select>}</>}
        </fieldset>;
      })}<button type="button" onClick={() => addQuestion(sectionIndex)}>Adaugă întrebare în secțiune</button></div>)}
      <button type="button" onClick={addSection}>Adaugă secțiune</button><button type="button" onClick={() => void save()}>Salvează</button><button type="button" onClick={() => void publish()}>Publică versiunea</button></>}
    <p role="status">{message}</p>
  </section>;
}

function visible(question: Question, answers: Record<string, unknown>) {
  if (!question.condition) return true;
  const answer = answers[question.condition.question_id];
  return (typeof answer === 'boolean' ? (answer ? 'yes' : 'no') : answer) === question.condition.value;
}
function answerControl(question: Question, value: unknown, onChange: (value: unknown) => void) {
  if (question.type === 'yes_no') return <select aria-label={question.label} value={value === true ? 'yes' : value === false ? 'no' : ''} onChange={event => onChange(event.target.value === 'yes')}><option value="">Alegeți</option><option value="yes">Da</option><option value="no">Nu</option></select>;
  if (question.type === 'single_choice') return <select aria-label={question.label} value={typeof value === 'string' ? value : ''} onChange={event => onChange(event.target.value)}><option value="">Alegeți</option>{question.options?.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</select>;
  if (question.type === 'multiple_choice') return <fieldset><legend>{question.label}</legend>{question.options?.map(option => <label key={option.value}><input type="checkbox" checked={Array.isArray(value) && value.includes(option.value)} onChange={event => { const values = Array.isArray(value) ? value as string[] : []; onChange(event.target.checked ? [...values, option.value] : values.filter(item => item !== option.value)); }} />{option.label}</label>)}</fieldset>;
  if (question.type === 'long_text') return <textarea aria-label={question.label} value={typeof value === 'string' ? value : ''} onChange={event => onChange(event.target.value)} />;
  return <input aria-label={question.label} type={question.type === 'number' ? 'number' : question.type === 'date' ? 'date' : 'text'} value={value == null ? '' : String(value)} onChange={event => onChange(question.type === 'number' ? (event.target.value === '' ? '' : Number(event.target.value)) : event.target.value)} />;
}

export function PatientFormsPanel({ patientId, canReview = false }: { patientId: string; canReview?: boolean }) {
  const [forms, setForms] = useState<PatientForm[]>([]);
  const [templates, setTemplates] = useState<Array<{ id: string; name: string; version_id: string; version_number: number }>>([]);
  const [selectedVersion, setSelectedVersion] = useState('');
  const [current, setCurrent] = useState<PatientForm | null>(null);
  const [answers, setAnswers] = useState<Record<string, unknown>>({});
  const [reason, setReason] = useState('');
  const [error, setError] = useState('');
  const [revisions, setRevisions] = useState<Array<{ revision_number: number; reason: string; created_at: string }>>([]);
  async function refresh() { const [items, available] = await Promise.all([listPatientForms(patientId), listPublishedForms()]); setForms(items); setTemplates(available); }
  useEffect(() => { void refresh(); }, [patientId]);
  async function startForm() { if (!selectedVersion) return; const created = await createPatientForm(patientId, selectedVersion); await refresh(); await open(created.id); }
  async function open(id: string) {
    const item = await getPatientForm(id);
    setCurrent(item);
    setAnswers(item.answers || {});
    setRevisions(canReview ? await getFormRevisions(id) : []);
  }
  function setAnswer(questionId: string, value: unknown) {
    setAnswers(currentAnswers => {
      const next = { ...currentAnswers, [questionId]: value };
      const questions = current?.definition?.sections.flatMap(section => section.questions) || [];
      for (const question of questions) {
        if (question.condition && !visible(question, next)) delete next[question.id];
      }
      return next;
    });
  }
  async function persist(): Promise<boolean> {
    if (!current) return false;
    try { const updated = await saveAnswers(current.id, answers); setCurrent(updated); await refresh(); setError(''); return true; }
    catch (e) { setError(e instanceof Error ? e.message : 'Nu s-a putut salva'); return false; }
  }
  async function submit() { if (!current || !(await persist())) return; try { const updated = await submitForm(current.id); setCurrent(updated); await refresh(); } catch (e) { setError(e instanceof Error ? e.message : 'Nu s-a putut trimite'); } }
  async function review(decision: 'validated' | 'needs_clarification') { if (!current) return; const updated = await reviewForm(current.id, decision, reason); setCurrent(updated); await refresh(); }
  return <section aria-label="Formulare pacient"><h2>Formulare</h2>
    {templates.length > 0 && <div><label>Formular nou <select value={selectedVersion} onChange={event => setSelectedVersion(event.target.value)}><option value="">Alegeți formularul</option>{templates.map(item => <option key={item.version_id} value={item.version_id}>{item.name} v{item.version_number}</option>)}</select></label><button type="button" disabled={!selectedVersion} onClick={() => void startForm()}>Începe completarea</button></div>}
    <ul>{forms.map(form => <li key={form.id}><button type="button" onClick={() => void open(form.id)}>{form.template_name} v{form.version_number} · {form.source === 'staff' ? 'introdus de personal' : 'introdus de pacient'} · {({ draft: 'ciornă', submitted: 'trimis', validated: 'validat', needs_clarification: 'necesită clarificări' } as const)[form.status]}</button></li>)}</ul>
    {current?.definition && <article><h3>{current.template_name} v{current.version_number}</h3>{current.definition.sections.map(section => <section key={section.id}><h4>{section.title}</h4>{section.questions.filter(question => visible(question, answers)).map(question => <label key={question.id}>{question.label}{question.required ? ' *' : ''}{answerControl(question, answers[question.id], value => setAnswer(question.id, value))}</label>)}</section>)}
      {(current.status === 'draft' || current.status === 'needs_clarification') && <><button type="button" onClick={() => void persist()}>Salvează răspunsurile</button><button type="button" onClick={() => void submit()}>Trimite</button></>}
      {canReview && current.status === 'submitted' && <><label>Motiv clarificare <input value={reason} onChange={event => setReason(event.target.value)} /></label><button type="button" onClick={() => void review('validated')}>Validează</button><button type="button" onClick={() => void review('needs_clarification')}>Cere clarificări</button></>}
      {current.clarification_reason && <p>{current.clarification_reason}</p>}{canReview && revisions.length > 0 && <aside aria-label="Istoricul corecțiilor"><h4>Istoricul corecțiilor</h4><ol>{revisions.map(revision => <li key={revision.revision_number}>Revizia {revision.revision_number}: {revision.reason} · {revision.created_at}</li>)}</ol></aside>}</article>}
    <p role="alert">{error}</p>
  </section>;
}
