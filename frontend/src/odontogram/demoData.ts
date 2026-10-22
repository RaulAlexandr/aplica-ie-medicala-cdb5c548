import type { ClinicalFinding, OdontogramTooth, ToothHistoryEvent } from './types';
import { ADULT_TOOTH_IDS } from './types';

export const demoTeeth: OdontogramTooth[] = ADULT_TOOTH_IDS.map((id) => ({ id, status: id === '18' ? 'missing' : 'present' }));
export const demoFindings: ClinicalFinding[] = [
  { id: 'finding-demo-1601', toothId: '16', kind: 'Obturație existentă', description: 'Restaurare veche, margini de urmărit la control.', surfaces: ['occlusal', 'mesial'], observedAt: '2026-02-12T09:30:00+02:00', author: 'Dr. Mara Ionescu' },
];
export const demoHistory: ToothHistoryEvent[] = [
  { id: 'event-demo-1601', toothId: '16', kind: 'finding_added', summary: 'Constatare adăugată: obturație existentă', occurredAt: '2026-02-12T09:30:00+02:00', author: 'Dr. Mara Ionescu' },
];
