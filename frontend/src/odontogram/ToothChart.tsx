import type { OdontogramTooth, ToothId } from './types';
import { ADULT_TOOTH_IDS, formatFdi } from './types';
import { useRef } from 'react';

interface ToothChartProps {
  teeth: OdontogramTooth[];
  selectedToothId: ToothId;
  onToothSelect: (toothId: ToothId) => void;
}

function ToothButton({ tooth, selected, onSelect, focusTooth }: { tooth: OdontogramTooth; selected: boolean; onSelect: (id: ToothId) => void; focusTooth: (id: ToothId) => void }) {
  const missing = tooth.status === 'missing';
  return <button type="button" className={`og-tooth${selected ? ' is-selected' : ''}${missing ? ' is-missing' : ''}`}
    aria-pressed={selected} aria-current={selected ? 'true' : undefined}
    aria-label={`Dinte ${formatFdi(tooth.id)}${missing ? ', lipsă' : tooth.status === 'unevaluated' ? ', neevaluat' : ', prezent'}`}
    onClick={() => onSelect(tooth.id)} data-tooth-id={tooth.id} onKeyDown={(event) => {
      const ordered = ADULT_TOOTH_IDS;
      const index = ordered.indexOf(tooth.id);
      const isUpper = index < 16;
      let nextIndex = index;
      if (event.key === 'ArrowRight') nextIndex = isUpper ? (index + 1) % 16 : 16 + ((index - 16 + 1) % 16);
      else if (event.key === 'ArrowLeft') nextIndex = isUpper ? (index + 15) % 16 : 16 + ((index - 16 + 15) % 16);
      else if (event.key === 'ArrowDown' && isUpper) nextIndex = index + 16;
      else if (event.key === 'ArrowUp' && !isUpper) nextIndex = index - 16;
      else return;
      event.preventDefault();
      const nextId = ordered[nextIndex];
      focusTooth(nextId);
      onSelect(nextId);
    }}>
    <svg aria-hidden="true" viewBox="0 0 44 52" className="og-tooth-shape">
      <path className="og-tooth-crown" d="M8 6 Q22 0 36 6 L34 22 Q32 26 30 30 L28 46 Q25 50 22 42 Q19 50 16 46 L14 30 Q12 26 10 22Z" />
      <path className="og-tooth-line" d="M10 21 Q22 25 34 21" />
      {missing && <path className="og-missing-mark" d="M12 12L32 36M32 12L12 36" />}
    </svg>
    <span className="og-tooth-number">{formatFdi(tooth.id)}</span>
    {missing && <span className="og-tooth-state">Lipsă</span>}
  </button>;
}

function arch(teeth: OdontogramTooth[], selected: ToothId, onSelect: (id: ToothId) => void, focusTooth: (id: ToothId) => void, ids: readonly ToothId[]) {
  return ids.map((id) => {
    const tooth = teeth.find((item) => item.id === id) ?? { id, status: 'unevaluated' as const };
    return <ToothButton key={id} tooth={tooth} selected={selected === id} onSelect={onSelect} focusTooth={focusTooth} />;
  });
}

export function ToothChart({ teeth, selectedToothId, onToothSelect }: ToothChartProps) {
  const chartRef = useRef<HTMLElement>(null);
  const focusTooth = (id: ToothId) => chartRef.current?.querySelector<HTMLButtonElement>(`button[data-tooth-id="${id}"]`)?.focus();
  const upperLeft = ADULT_TOOTH_IDS.slice(0, 8), upperRight = ADULT_TOOTH_IDS.slice(8, 16);
  const lowerLeft = ADULT_TOOTH_IDS.slice(16, 24), lowerRight = ADULT_TOOTH_IDS.slice(24, 32);
  return <section ref={chartRef} className="og-chart" aria-labelledby="og-chart-title">
    <div className="og-chart-heading"><div><p className="og-kicker">DENTIȚIE PERMANENTĂ</p><h2 id="og-chart-title">Odontogramă</h2></div>
      <p className="og-perspective">Orientare: <strong>din perspectiva pacientului</strong></p></div>
    <div className="og-orientation"><span>Dreapta pacientului</span><span>Linia mediană</span><span>Stânga pacientului</span></div>
    <div className="og-arch og-arch--upper" role="group" aria-label="Arcada superioară">
      <span className="og-arch-name">SUPERIOARĂ</span><div className="og-arch-side">{arch(teeth, selectedToothId, onToothSelect, focusTooth, upperLeft)}</div>
      <div className="og-midline" aria-hidden="true"/><div className="og-arch-side">{arch(teeth, selectedToothId, onToothSelect, focusTooth, upperRight)}</div>
    </div>
    <div className="og-arch og-arch--lower" role="group" aria-label="Arcada inferioară">
      <span className="og-arch-name">INFERIOARĂ</span><div className="og-arch-side">{arch(teeth, selectedToothId, onToothSelect, focusTooth, lowerLeft)}</div>
      <div className="og-midline" aria-hidden="true"/><div className="og-arch-side">{arch(teeth, selectedToothId, onToothSelect, focusTooth, lowerRight)}</div>
    </div>
    <div className="og-status-legend"><span><i className="og-dot og-dot--present"/>Prezent</span><span><i className="og-dot og-dot--missing"/>Lipsă</span><span><i className="og-dot og-dot--selected"/>Selectat</span></div>
  </section>;
}
