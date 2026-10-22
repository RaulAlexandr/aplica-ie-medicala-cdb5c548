import { describe, expect, it, vi } from 'vitest';
import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer';
import { Odontogram } from './Odontogram';
import { ADULT_TOOTH_IDS } from './types';
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

describe('odontograma adultă', () => {
  it('expune exact cele 32 poziții FDI fără duplicate', () => {
    expect(ADULT_TOOTH_IDS).toHaveLength(32);
    expect(new Set(ADULT_TOOTH_IDS).size).toBe(32);
    expect(new Set(ADULT_TOOTH_IDS)).toEqual(new Set([
      ...Array.from({ length: 8 }, (_, index) => `1${index + 1}`),
      ...Array.from({ length: 8 }, (_, index) => `2${index + 1}`),
      ...Array.from({ length: 8 }, (_, index) => `3${index + 1}`),
      ...Array.from({ length: 8 }, (_, index) => `4${index + 1}`),
    ]));
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

  it('corecția demonstrativă păstrează valoarea anterioară în istoric și notifică părintele corect', () => {
    const onFindingCorrect = vi.fn();
    const renderer = render(<Odontogram teeth={demoTeeth} findings={demoFindings} history={demoHistory} selectedToothId="16" onToothSelect={vi.fn()}
      onFindingCreate={vi.fn()} onFindingCorrect={onFindingCorrect} onToothStatusChange={vi.fn()} />);
    act(() => renderer.root.findByProps({ children: 'Corectează demonstrativ' }).props.onClick());
    expect(onFindingCorrect).toHaveBeenCalledWith('finding-demo-1601', expect.objectContaining({ description: expect.stringContaining('(corectat după verificare)'), surfaces: ['occlusal', 'mesial'] }));
  });

  it('afișează versiunea veche și cea curentă după o corecție în demonstrație', () => {
    const renderer = render(<DemoApp />);
    act(() => renderer.root.findByProps({ children: 'Corectează demonstrativ' }).props.onClick());
    expect(renderer.root.findAllByType('strong').some((node) => node.children.join('').includes('Constatare corectată demonstrativ'))).toBe(true);
    expect(renderer.root.findAll((node) => node.type === 'p' && node.children.join('').includes('Anterior: Obturație existentă: Restaurare veche'))).toHaveLength(1);
    expect(renderer.root.findAll((node) => node.type === 'p' && node.children.join('').includes('Valoare curentă: Obturație existentă: Restaurare veche'))).toHaveLength(1);
  });
});
