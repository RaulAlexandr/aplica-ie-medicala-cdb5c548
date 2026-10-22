import { describe, expect, it, vi } from 'vitest';
import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';
import { Odontogram } from './Odontogram';
import { ADULT_TOOTH_IDS } from './types';
import type { ClinicalFinding, OdontogramProps, ToothHistoryEvent } from './types';
import { demoFindings, demoHistory, demoTeeth } from './demoData';
import { DemoApp } from './DemoApp';
import type { ToothId } from './types';

function render(element: React.ReactElement) {
  let renderer!: ReactTestRenderer;
  act(() => { renderer = create(element); });
  return renderer;
}
function buttonByLabel(renderer: ReactTestRenderer, label: string): ReactTestInstance {
  return renderer.root.findAllByType('button').find((button) => button.props['aria-label'] === label)!;
}

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => { resolve = resolvePromise; reject = rejectPromise; });
  return { promise, resolve, reject };
}

function controlledHarness(options: {
  getFindings: () => ClinicalFinding[];
  getHistory?: () => ToothHistoryEvent[];
  onFindingCreate: OdontogramProps['onFindingCreate'];
  onFindingCorrect?: OdontogramProps['onFindingCorrect'];
}) {
  let selected: ToothId = '16';
  let renderer!: ReactTestRenderer;
  const onToothSelect = (toothId: ToothId) => { selected = toothId; renderer.update(element()); };
  const element = () => <Odontogram teeth={demoTeeth} findings={options.getFindings()} history={options.getHistory?.() ?? demoHistory}
    selectedToothId={selected} onToothSelect={onToothSelect} onFindingCreate={options.onFindingCreate}
    onFindingCorrect={options.onFindingCorrect ?? vi.fn()} onToothStatusChange={vi.fn()} />;
  renderer = render(element());
  return {
    renderer,
    select(toothId: ToothId) { act(() => { selected = toothId; renderer.update(element()); }); },
    refresh() { act(() => renderer.update(element())); },
  };
}

describe('odontograma adultă', () => {
  it('expune exact cele 32 poziții FDI fără duplicate', () => {
    expect(ADULT_TOOTH_IDS).toHaveLength(32);
    expect(new Set(ADULT_TOOTH_IDS).size).toBe(32);
    expect(ADULT_TOOTH_IDS).toEqual([
      '18', '17', '16', '15', '14', '13', '12', '11', '21', '22', '23', '24', '25', '26', '27', '28',
      '48', '47', '46', '45', '44', '43', '42', '41', '31', '32', '33', '34', '35', '36', '37', '38',
    ]);
  });

  it('selectează dintele, actualizează panoul și navighează cu săgețile', () => {
    let selected: ToothId = '16';
    let renderer!: ReactTestRenderer;
    const selection = vi.fn((id: ToothId) => { selected = id; act(() => renderer.update(<Odontogram {...props} selectedToothId={selected} onToothSelect={selection} />)); });
    const props = { teeth: demoTeeth, findings: demoFindings, history: demoHistory, selectedToothId: selected, onToothSelect: selection,
      onFindingCreate: vi.fn(), onFindingCorrect: vi.fn(), onToothStatusChange: vi.fn() };
    renderer = render(<Odontogram {...props} />);
    act(() => buttonByLabel(renderer, 'Dinte 1.7, prezent').props.onClick());
    expect(selected).toBe('17');
    expect(renderer.root.findByProps({ children: '1.7' })).toBeTruthy();
    act(() => buttonByLabel(renderer, 'Dinte 1.7, prezent').props.onKeyDown({ key: 'ArrowRight', preventDefault: vi.fn() }));
    expect(selected).toBe('16');
  });

  it('păstrează dintele lipsă selectabil pentru istoricul său', () => {
    const select = vi.fn();
    const renderer = render(<Odontogram teeth={demoTeeth} findings={demoFindings} history={demoHistory} selectedToothId="16" onToothSelect={select}
      onFindingCreate={vi.fn()} onFindingCorrect={vi.fn()} onToothStatusChange={vi.fn()} />);
    const missing = buttonByLabel(renderer, 'Dinte 1.8, lipsă');
    expect(missing).toBeTruthy();
    act(() => missing.props.onClick());
    expect(select).toHaveBeenCalledWith('18');
  });

  it('trimite schimbarea stării cu ID-ul dintelui selectat', () => {
    const onToothStatusChange = vi.fn();
    const renderer = render(<Odontogram teeth={demoTeeth} findings={demoFindings} history={demoHistory} selectedToothId="17" onToothSelect={vi.fn()}
      onFindingCreate={vi.fn()} onFindingCorrect={vi.fn()} onToothStatusChange={onToothStatusChange} />);
    act(() => renderer.root.findByProps({ 'aria-label': 'Starea dintelui 1.7' }).props.onChange({ target: { value: 'missing' } }));
    expect(onToothStatusChange).toHaveBeenCalledWith('17', 'missing');
  });

  it('trimite dintele și suprafețele selectate fără să înlocuiască constatările', () => {
    const onFindingCreate = vi.fn();
    const renderer = render(<Odontogram teeth={demoTeeth} findings={demoFindings} history={demoHistory} selectedToothId="17" onToothSelect={vi.fn()}
      onFindingCreate={onFindingCreate} onFindingCorrect={vi.fn()} onToothStatusChange={vi.fn()} />);
    const textarea = renderer.root.findByType('textarea');
    act(() => textarea.props.onChange({ target: { value: 'Leziune carioasă observată clinic.' } }));
    act(() => renderer.root.findByProps({ 'aria-label': 'Mezială (mesial)' }).props.onClick());
    act(() => renderer.root.findByProps({ 'aria-label': 'Ocluzală (occlusal)' }).props.onClick());
    act(() => renderer.root.findByType('form').props.onSubmit({ preventDefault: vi.fn() }));
    expect(onFindingCreate).toHaveBeenCalledWith({ toothId: '17', kind: 'Carie', description: 'Leziune carioasă observată clinic.', surfaces: ['mesial', 'occlusal'] });
    expect(renderer.root.findAll((node) => node.props['data-finding-id'] === 'finding-demo-1601')).toHaveLength(0);
  });

  it('acceptă constatări multiple pe același dinte și aceeași suprafață', () => {
    const renderer = render(<DemoApp />);
    act(() => renderer.root.findByType('textarea').props.onChange({ target: { value: 'Margine de restaurare de urmărit.' } }));
    act(() => renderer.root.findByProps({ 'aria-label': 'Mezială (mesial)' }).props.onClick());
    act(() => renderer.root.findByType('form').props.onSubmit({ preventDefault: vi.fn() }));
    expect(renderer.root.findAll((node) => node.props['data-finding-id'])).toHaveLength(2);
    expect(renderer.root.findByProps({ 'data-finding-id': 'finding-demo-1601' })).toBeTruthy();
  });

  it('demonstrează fluxul 1.6 → 1.7 → 1.6 cu datele ambilor dinți păstrate', () => {
    const renderer = render(<DemoApp />);
    act(() => buttonByLabel(renderer, 'Dinte 1.7, prezent').props.onClick());
    act(() => renderer.root.findByType('textarea').props.onChange({ target: { value: 'Carie ocluzală de verificat.' } }));
    act(() => renderer.root.findByType('form').props.onSubmit({ preventDefault: vi.fn() }));
    expect(renderer.root.findAll((node) => node.props['data-finding-id'] && node.findAllByType('p').some((paragraph) => paragraph.children.join('').includes('Carie ocluzală de verificat.')))).toHaveLength(1);
    act(() => buttonByLabel(renderer, 'Dinte 1.6, prezent').props.onClick());
    expect(renderer.root.findAll((node) => node.props['data-finding-id'] === 'finding-demo-1601')).toHaveLength(1);
    act(() => buttonByLabel(renderer, 'Dinte 1.7, prezent').props.onClick());
    expect(renderer.root.findAll((node) => node.props['data-finding-id'] && node.findAllByType('p').some((paragraph) => paragraph.children.join('').includes('Carie ocluzală de verificat.')))).toHaveLength(1);
  });

  it('ține ciornele separate pe dinte și trimite fiecare constatare la poziția corectă', () => {
    const renderer = render(<DemoApp />);
    act(() => renderer.root.findByType('textarea').props.onChange({ target: { value: 'Ciornă pentru dintele 1.6.' } }));
    act(() => renderer.root.findByProps({ 'aria-label': 'Mezială (mesial)' }).props.onClick());
    act(() => buttonByLabel(renderer, 'Dinte 1.7, prezent').props.onClick());

    expect(renderer.root.findByType('textarea').props.value).toBe('');
    expect(renderer.root.findByProps({ 'aria-label': 'Mezială (mesial)' }).props['aria-pressed']).toBe(false);
    act(() => renderer.root.findByType('textarea').props.onChange({ target: { value: 'Carie pe dintele 1.7.' } }));
    act(() => renderer.root.findByProps({ 'aria-label': 'Ocluzală (occlusal)' }).props.onClick());
    act(() => renderer.root.findByType('form').props.onSubmit({ preventDefault: vi.fn() }));
    act(() => buttonByLabel(renderer, 'Dinte 1.6, prezent').props.onClick());

    expect(renderer.root.findByType('textarea').props.value).toBe('Ciornă pentru dintele 1.6.');
    expect(renderer.root.findByProps({ 'aria-label': 'Mezială (mesial)' }).props['aria-pressed']).toBe(true);
    act(() => renderer.root.findByType('form').props.onSubmit({ preventDefault: vi.fn() }));
    expect(renderer.root.findAll((node) => node.props['data-finding-id'] && node.findAllByType('p').some((paragraph) => paragraph.children.join('').includes('Ciornă pentru dintele 1.6.')))).toHaveLength(1);

    act(() => buttonByLabel(renderer, 'Dinte 1.7, prezent').props.onClick());
    expect(renderer.root.findByType('textarea').props.value).toBe('');
    expect(renderer.root.findAll((node) => node.props['data-finding-id'] && node.findAllByType('p').some((paragraph) => paragraph.children.join('').includes('Carie pe dintele 1.7.')))).toHaveLength(1);
  });

  it('păstrează ciorna dacă părintele nu confirmă salvarea', async () => {
    const onFindingCreate = vi.fn(() => Promise.reject(new Error('offline')));
    const renderer = render(<Odontogram teeth={demoTeeth} findings={demoFindings} history={demoHistory} selectedToothId="16" onToothSelect={vi.fn()}
      onFindingCreate={onFindingCreate} onFindingCorrect={vi.fn()} onToothStatusChange={vi.fn()} />);
    act(() => renderer.root.findByType('textarea').props.onChange({ target: { value: 'Constatare păstrată local.' } }));
    await act(async () => {
      renderer.root.findByType('form').props.onSubmit({ preventDefault: vi.fn() });
      await Promise.resolve();
    });
    expect(renderer.root.findByType('textarea').props.value).toBe('Constatare păstrată local.');
    expect(renderer.root.findByProps({ role: 'alert' }).children.join('')).toContain('Ciorna acelui dinte a fost păstrată');
  });

  it('la confirmarea întârziată salvează pe dintele A și păstrează ciorna dintelui B', async () => {
    const request = deferred<void>();
    let findings = [...demoFindings];
    const onFindingCreate = vi.fn((draft: Omit<ClinicalFinding, 'id' | 'observedAt' | 'author'>) => request.promise.then(() => {
      findings = [...findings, { ...draft, id: 'finding-confirmed-a', observedAt: '2026-10-23T10:00:00Z', author: 'Dr. Demo' }];
    }));
    const harness = controlledHarness({ getFindings: () => findings, onFindingCreate });
    const { renderer } = harness;

    harness.select('17');
    act(() => renderer.root.findByType('textarea').props.onChange({ target: { value: 'Ciornă păstrată pentru 1.7.' } }));
    act(() => renderer.root.findByProps({ 'aria-label': 'Mezială (mesial)' }).props.onClick());
    harness.select('16');
    act(() => renderer.root.findByType('textarea').props.onChange({ target: { value: 'Constatare de salvat pe 1.6.' } }));
    act(() => renderer.root.findByProps({ 'aria-label': 'Ocluzală (occlusal)' }).props.onClick());
    act(() => renderer.root.findByType('form').props.onSubmit({ preventDefault: vi.fn() }));
    const save = onFindingCreate.mock.results[0]!.value;
    expect(renderer.root.findByProps({ children: 'Se confirmă…' }).props.disabled).toBe(true);
    expect(onFindingCreate).toHaveBeenCalledWith({ toothId: '16', kind: 'Carie', description: 'Constatare de salvat pe 1.6.', surfaces: ['occlusal'] });

    harness.select('17');
    expect(renderer.root.findByType('textarea').props.value).toBe('Ciornă păstrată pentru 1.7.');
    expect(renderer.root.findByProps({ 'aria-label': 'Mezială (mesial)' }).props['aria-pressed']).toBe(true);
    request.resolve();
    await act(async () => { await save; harness.refresh(); });

    expect(renderer.root.findByType('textarea').props.value).toBe('Ciornă păstrată pentru 1.7.');
    expect(renderer.root.findByProps({ children: 'Adaugă constatarea' }).props.disabled).toBe(false);
    expect(onFindingCreate).toHaveBeenCalledTimes(1);
    harness.select('16');
    expect(renderer.root.findByType('textarea').props.value).toBe('');
    expect(renderer.root.findAll((node) => node.props['data-finding-id'] === 'finding-confirmed-a')).toHaveLength(1);
  });

  it('la respingerea întârziată păstrează ciorna A, ciorna B și identifică dintele A în eroare', async () => {
    const request = deferred<void>();
    const onFindingCreate = vi.fn(() => request.promise);
    const harness = controlledHarness({ getFindings: () => demoFindings, onFindingCreate });
    const { renderer } = harness;

    harness.select('17');
    act(() => renderer.root.findByType('textarea').props.onChange({ target: { value: 'Ciornă pentru 1.7.' } }));
    harness.select('16');
    act(() => renderer.root.findByType('textarea').props.onChange({ target: { value: 'Ciornă nesalvată pentru 1.6.' } }));
    act(() => renderer.root.findByType('form').props.onSubmit({ preventDefault: vi.fn() }));
    const save = onFindingCreate.mock.results[0]!.value;
    harness.select('17');
    request.reject(new Error('offline'));
    await act(async () => { await save.catch(() => undefined); await Promise.resolve(); });

    const error = renderer.root.findByProps({ role: 'alert' }).children.join('');
    expect(error).toContain('dintele 1.6');
    expect(error).not.toContain('dintele 1.7');
    expect(renderer.root.findByType('textarea').props.value).toBe('Ciornă pentru 1.7.');
    expect(renderer.root.findByProps({ children: 'Adaugă constatarea' }).props.disabled).toBe(false);
    harness.select('16');
    expect(renderer.root.findByType('textarea').props.value).toBe('Ciornă nesalvată pentru 1.6.');
    expect(onFindingCreate).toHaveBeenCalledTimes(1);
  });

  it('corecția trimite valoarea editată și identificatorul stabil al constatării', () => {
    const onFindingCorrect = vi.fn();
    const renderer = render(<Odontogram teeth={demoTeeth} findings={demoFindings} history={demoHistory} selectedToothId="16" onToothSelect={vi.fn()}
      onFindingCreate={vi.fn()} onFindingCorrect={onFindingCorrect} onToothStatusChange={vi.fn()} />);
    act(() => renderer.root.findAllByType('button').find((button) => button.children.join('') === 'Corectează constatarea')!.props.onClick());
    const textarea = renderer.root.findByProps({ 'aria-label': 'Descriere corectată pentru constatarea finding-demo-1601' });
    act(() => textarea.props.onChange({ target: { value: 'Marginea restaurării este netedă.' } }));
    act(() => renderer.root.findByProps({ 'data-correction-id': 'finding-demo-1601' }).props.onSubmit({ preventDefault: vi.fn() }));
    expect(onFindingCorrect).toHaveBeenCalledWith('finding-demo-1601', { kind: 'Obturație existentă', description: 'Marginea restaurării este netedă.', surfaces: ['occlusal', 'mesial'] });
  });

  it('afișează versiunea veche și cea curentă după o corecție în demonstrație', () => {
    const renderer = render(<DemoApp />);
    act(() => renderer.root.findByProps({ children: 'Corectează constatarea' }).props.onClick());
    const textarea = renderer.root.findByProps({ 'aria-label': 'Descriere corectată pentru constatarea finding-demo-1601' });
    act(() => textarea.props.onChange({ target: { value: 'Marginea restaurării este netedă după verificare.' } }));
    act(() => renderer.root.findByProps({ 'data-correction-id': 'finding-demo-1601' }).props.onSubmit({ preventDefault: vi.fn() }));
    expect(renderer.root.findAllByType('strong').some((node) => node.children.join('').includes('Constatare corectată demonstrativ'))).toBe(true);
    expect(renderer.root.findAll((node) => node.type === 'p' && node.children.join('').includes('Anterior: Obturație existentă: Restaurare veche'))).toHaveLength(1);
    expect(renderer.root.findAll((node) => node.type === 'p' && node.children.join('').includes('Valoare curentă: Obturație existentă: Marginea restaurării este netedă după verificare.'))).toHaveLength(1);
  });

  it('confirmă corecția întârziată prin ID și nu afectează o altă constatare după schimbarea selecției', async () => {
    const request = deferred<void>();
    const otherFinding: ClinicalFinding = { id: 'finding-demo-other', toothId: '16', kind: 'Discromie', description: 'Constatare separată.', surfaces: ['distal'], observedAt: '2026-02-12T09:35:00+02:00', author: 'Dr. Mara Ionescu' };
    let findings = [...demoFindings, otherFinding];
    const onFindingCorrect = vi.fn((findingId: string, replacement: Pick<ClinicalFinding, 'kind' | 'description' | 'surfaces'>) => request.promise.then(() => {
      findings = findings.map((finding) => finding.id === findingId ? { ...finding, ...replacement } : finding);
    }));
    const harness = controlledHarness({ getFindings: () => findings, onFindingCreate: vi.fn(), onFindingCorrect });
    const { renderer } = harness;

    act(() => renderer.root.findAllByType('button').find((button) => button.children.join('') === 'Corectează constatarea')!.props.onClick());
    const correction = renderer.root.findByProps({ 'aria-label': 'Descriere corectată pentru constatarea finding-demo-1601' });
    act(() => correction.props.onChange({ target: { value: 'Restaurarea a fost reevaluată.' } }));
    act(() => renderer.root.findByProps({ 'data-correction-id': 'finding-demo-1601' }).props.onSubmit({ preventDefault: vi.fn() }));
    const save = onFindingCorrect.mock.results[0]!.value;
    harness.select('17');
    request.resolve();
    await act(async () => { await save; harness.refresh(); });
    harness.select('16');

    expect(onFindingCorrect).toHaveBeenCalledWith('finding-demo-1601', { kind: 'Obturație existentă', description: 'Restaurarea a fost reevaluată.', surfaces: ['occlusal', 'mesial'] });
    expect(renderer.root.findByProps({ 'data-finding-id': 'finding-demo-1601' }).findAllByType('p').some((node) => node.children.join('') === 'Restaurarea a fost reevaluată.')).toBe(true);
    expect(renderer.root.findByProps({ 'data-finding-id': 'finding-demo-other' }).findAllByType('p').some((node) => node.children.join('') === 'Constatare separată.')).toBe(true);
    expect(renderer.root.findAll((node) => node.props['data-correction-id'] === 'finding-demo-1601')).toHaveLength(0);
  });

  it('păstrează textul corecției după eșec întârziat și schimbarea selecției', async () => {
    const request = deferred<void>();
    const onFindingCorrect = vi.fn(() => request.promise);
    const harness = controlledHarness({ getFindings: () => demoFindings, onFindingCreate: vi.fn(), onFindingCorrect });
    const { renderer } = harness;

    act(() => renderer.root.findByProps({ children: 'Corectează constatarea' }).props.onClick());
    const correction = renderer.root.findByProps({ 'aria-label': 'Descriere corectată pentru constatarea finding-demo-1601' });
    act(() => correction.props.onChange({ target: { value: 'Text corectat, în așteptare.' } }));
    act(() => renderer.root.findByProps({ 'data-correction-id': 'finding-demo-1601' }).props.onSubmit({ preventDefault: vi.fn() }));
    const save = onFindingCorrect.mock.results[0]!.value;
    harness.select('17');
    request.reject(new Error('offline'));
    await act(async () => { await save.catch(() => undefined); await Promise.resolve(); });

    expect(renderer.root.findByProps({ role: 'alert' }).children.join('')).toContain('dintele 1.6');
    harness.select('16');
    expect(renderer.root.findByProps({ 'aria-label': 'Descriere corectată pentru constatarea finding-demo-1601' }).props.value).toBe('Text corectat, în așteptare.');
    expect(onFindingCorrect).toHaveBeenCalledTimes(1);
  });
});
