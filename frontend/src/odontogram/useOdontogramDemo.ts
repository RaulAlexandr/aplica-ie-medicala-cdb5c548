import { useState } from 'react';
import type { ClinicalFinding, OdontogramTooth, ToothHistoryEvent, ToothStatus } from './types';
import { demoFindings, demoHistory, demoTeeth } from './demoData';
import type { OdontogramProps } from './types';

const DEMO_AUTHOR = 'Dr. Mara Ionescu';
const now = () => new Date().toISOString();
let fallbackId = 0;
const demoId = (prefix: string) => `${prefix}-${globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${++fallbackId}`}`;

export function useOdontogramDemo() {
  const [teeth, setTeeth] = useState<OdontogramTooth[]>(() => demoTeeth.map((tooth) => ({ ...tooth })));
  const [findings, setFindings] = useState<ClinicalFinding[]>(() => demoFindings.map((finding) => ({ ...finding, surfaces: [...finding.surfaces] })));
  const [history, setHistory] = useState<ToothHistoryEvent[]>(() => demoHistory.map((event) => ({ ...event })));
  const [selectedToothId, setSelectedToothId] = useState<OdontogramProps['selectedToothId']>('16');

  const onFindingCreate: OdontogramProps['onFindingCreate'] = (draft) => {
    const timestamp = now();
    const id = demoId('finding');
    const finding: ClinicalFinding = { ...draft, surfaces: [...draft.surfaces], id, observedAt: timestamp, author: DEMO_AUTHOR };
    setFindings((current) => [...current, finding]);
    setHistory((current) => [{ id: `event-${id}`, toothId: draft.toothId, kind: 'finding_added', summary: `Constatare adăugată: ${draft.kind}`, occurredAt: timestamp, author: DEMO_AUTHOR }, ...current]);
  };

  const onFindingCorrect: OdontogramProps['onFindingCorrect'] = (findingId, replacement) => {
    const previous = findings.find((finding) => finding.id === findingId);
    if (!previous) return;
    const timestamp = now();
    const replacementText = `${replacement.kind}: ${replacement.description}`;
    const previousText = `${previous.kind}: ${previous.description}`;
    const eventId = demoId(`correction-${findingId}`);
    setFindings((current) => current.map((finding) => finding.id === findingId ? { ...finding, ...replacement, surfaces: [...replacement.surfaces], observedAt: timestamp, author: DEMO_AUTHOR } : finding));
    setHistory((current) => [{ id: eventId, toothId: previous.toothId, kind: 'finding_corrected', summary: 'Constatare corectată demonstrativ', previousValue: previousText, currentValue: replacementText, occurredAt: timestamp, author: DEMO_AUTHOR }, ...current]);
  };

  const onToothStatusChange: OdontogramProps['onToothStatusChange'] = (toothId, status: ToothStatus) => {
    const previous = teeth.find((tooth) => tooth.id === toothId)?.status;
    if (!previous || previous === status) return;
    const timestamp = now();
    const eventId = demoId(`status-${toothId}`);
    setTeeth((current) => current.map((tooth) => tooth.id === toothId ? { ...tooth, status } : tooth));
    setHistory((current) => [{ id: eventId, toothId, kind: 'status_changed', summary: `Stare modificată: ${previous} → ${status}`, previousValue: previous, currentValue: status, occurredAt: timestamp, author: DEMO_AUTHOR }, ...current]);
  };

  return { teeth, findings, history, selectedToothId, setSelectedToothId, onFindingCreate, onFindingCorrect, onToothStatusChange };
}
