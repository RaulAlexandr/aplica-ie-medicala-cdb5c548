import type { ToothId, ToothSurface } from './types';
import { SURFACE_HELP, surfacesForTooth } from './surfaces';

interface ToothSurfacePickerProps {
  toothId: ToothId;
  selected: ToothSurface[];
  onToggle: (surface: ToothSurface) => void;
}

export function ToothSurfacePicker({ toothId, selected, onToggle }: ToothSurfacePickerProps) {
  const options = surfacesForTooth(toothId);
  return <div className="og-surface-picker">
    <div className="og-surface-map" role="group" aria-label={`Suprafețele dintelui ${toothId}`}>
      {options.map((surface) => <button
        className={`og-surface og-surface--${surface.position}${selected.includes(surface.id) ? ' is-selected' : ''}`}
        type="button" key={surface.id} aria-pressed={selected.includes(surface.id)}
        aria-label={`${surface.label} (${surface.id})`} onClick={() => onToggle(surface.id)}
      >{surface.position === 'center' ? surface.label : surface.label.slice(0, 1)}</button>)}
    </div>
    <div className="og-surface-legend" aria-label="Suprafețe disponibile">
      {options.map((surface) => <button type="button" key={surface.id}
        className={`og-surface-chip${selected.includes(surface.id) ? ' is-selected' : ''}`}
        aria-pressed={selected.includes(surface.id)} onClick={() => onToggle(surface.id)}>
        {surface.label}
      </button>)}
    </div>
    <p className="og-help">{SURFACE_HELP}</p>
  </div>;
}
