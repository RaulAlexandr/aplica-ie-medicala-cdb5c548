import type { ToothId, ToothSurface } from './types';

export interface SurfaceOption { id: ToothSurface; label: string; position: 'top' | 'bottom' | 'left' | 'right' | 'center'; }

/**
 * Stable IDs intentionally match the accepted T04/T05 vocabulary. The middle surface
 * is incisal for anterior teeth (FDI positions 1–3), and occlusal for posterior teeth.
 */
export function surfacesForTooth(toothId: ToothId): SurfaceOption[] {
  const isUpper = toothId[0] === '1' || toothId[0] === '2';
  const isPatientRight = toothId[0] === '1' || toothId[0] === '4';
  const isAnterior = Number(toothId[1]) <= 3;
  const medialPosition = isPatientRight ? 'right' as const : 'left' as const;
  const distalPosition = isPatientRight ? 'left' as const : 'right' as const;
  return [
    { id: isUpper ? 'palatal' as const : 'lingual' as const, label: isUpper ? 'Palatinală' : 'Linguală', position: 'top' as const },
    { id: 'mesial' as const, label: 'Mezială', position: medialPosition },
    { id: isAnterior ? 'incisal' as const : 'occlusal' as const, label: isAnterior ? 'Incizală' : 'Ocluzală', position: 'center' as const },
    { id: 'distal' as const, label: 'Distală', position: distalPosition },
    { id: 'vestibular' as const, label: 'Vestibulară', position: 'bottom' as const },
  ];
}

export const SURFACE_HELP = 'Mezială = spre linia mediană; distală = spre spatele arcadei; vestibulară = spre obraz/buze; palatinală sau linguală = spre interior; incizală/ocluzală = marginea de tăiere sau suprafața de masticație.';
