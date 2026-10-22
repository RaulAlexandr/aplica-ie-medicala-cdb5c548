/** FDI two digit identifier. The 1–4 quadrants describe permanent adult dentition. */
export type ToothId = `${1 | 2 | 3 | 4}${1 | 2 | 3 | 4 | 5 | 6 | 7 | 8}`;

/** Surface identifiers match the vocabulary accepted by the in-progress T04/T05 backend. */
export type ToothSurface =
  | 'mesial' | 'distal' | 'buccal' | 'lingual' | 'palatal'
  | 'occlusal' | 'incisal' | 'vestibular' | 'labial'
  | 'approximal' | 'interproximal';

/** Tooth state is separate from findings and from any treatment/procedure. */
export type ToothStatus = 'present' | 'missing' | 'unevaluated';

/** A clinical finding describes an observation. It is not a planned or completed treatment. */
export interface ClinicalFinding {
  id: string;
  toothId: ToothId;
  kind: string;
  description: string;
  surfaces: ToothSurface[];
  observedAt: string;
  author: string;
}

export interface ToothHistoryEvent {
  id: string;
  toothId: ToothId;
  occurredAt: string;
  author: string;
  kind: 'finding_added' | 'finding_corrected' | 'status_changed';
  summary: string;
  /** Correction events retain both values so the demo never silently erases a prior value. */
  previousValue?: string;
  currentValue?: string;
}

export interface OdontogramTooth {
  id: ToothId;
  status: ToothStatus;
}

/**
 * Proposed integration contract only. T05 has no odontogram-specific contract yet.
 * A parent will own these records and persist them through the eventual T05 integration.
 */
export interface OdontogramProps {
  teeth: OdontogramTooth[];
  findings: ClinicalFinding[];
  history: ToothHistoryEvent[];
  selectedToothId: ToothId;
  onToothSelect: (toothId: ToothId) => void;
  onFindingCreate: (finding: Omit<ClinicalFinding, 'id' | 'observedAt' | 'author'>) => void;
  onFindingCorrect: (findingId: string, replacement: Pick<ClinicalFinding, 'kind' | 'description' | 'surfaces'>) => void;
  onToothStatusChange: (toothId: ToothId, status: ToothStatus) => void;
}

export const ADULT_TOOTH_IDS: readonly ToothId[] = [
  '18', '17', '16', '15', '14', '13', '12', '11',
  '21', '22', '23', '24', '25', '26', '27', '28',
  '48', '47', '46', '45', '44', '43', '42', '41',
  '31', '32', '33', '34', '35', '36', '37', '38',
];

export function formatFdi(toothId: ToothId): string {
  return `${toothId[0]}.${toothId[1]}`;
}
