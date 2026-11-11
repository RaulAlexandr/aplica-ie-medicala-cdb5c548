/** FDI two digit identifier for permanent adult dentition. */
export type ToothId = `${1 | 2 | 3 | 4}${1 | 2 | 3 | 4 | 5 | 6 | 7 | 8}`;

export type ToothSurface =
  | 'mesial' | 'distal' | 'buccal' | 'lingual' | 'palatal'
  | 'occlusal' | 'incisal' | 'vestibular' | 'labial'
  | 'approximal' | 'interproximal';

export type ToothStatus = 'present' | 'missing' | 'unevaluated';
export type FindingStatus = 'draft' | 'finalized';

export interface ClinicalFinding {
  id: string;
  toothId: ToothId;
  kind: string;
  description: string;
  surfaces: ToothSurface[];
  status: FindingStatus;
  observedAt: string;
  createdAt: string;
  authorId: string;
  author: string;
}

export interface ToothHistoryEvent {
  id: string;
  toothId: ToothId;
  occurredAt: string;
  authorId?: string;
  author: string;
  kind: 'finding_added' | 'finding_corrected' | 'status_changed';
  summary: string;
  previousValue?: string;
  currentValue?: string;
  reason?: string;
}

export interface OdontogramTooth {
  id: ToothId;
  status: ToothStatus;
}

export interface FindingDraftInput {
  kind: string;
  description: string;
  surfaces: ToothSurface[];
}

export interface OdontogramProps {
  teeth: OdontogramTooth[];
  findings: ClinicalFinding[];
  history: ToothHistoryEvent[];
  selectedToothId: ToothId;
  timezone: string;
  canWrite: boolean;
  canEditDraft: (finding: ClinicalFinding) => boolean;
  canFinalizeDraft: (finding: ClinicalFinding) => boolean;
  canCorrectFinding: (finding: ClinicalFinding) => boolean;
  onToothSelect: (toothId: ToothId) => void;
  onFindingCreate: (toothId: ToothId, finding: FindingDraftInput) => void | Promise<void>;
  onFindingDraftUpdate: (findingId: string, finding: FindingDraftInput) => void | Promise<void>;
  onFindingFinalize: (findingId: string) => void | Promise<void>;
  onFindingCorrect: (findingId: string, replacement: FindingDraftInput & { reason: string }) => void | Promise<void>;
  onToothStatusChange: (toothId: ToothId, status: ToothStatus) => void | Promise<void>;
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
