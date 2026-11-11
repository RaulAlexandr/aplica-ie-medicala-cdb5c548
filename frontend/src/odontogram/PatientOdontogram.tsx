import { useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { apiErrorMessage, type User } from '../api/client';
import { Odontogram } from './Odontogram';
import {
  correctOdontogramFinding,
  createOdontogramFinding,
  findingWithRevisionHistory,
  getPatientOdontogram,
  finalizeOdontogramFinding,
  updateOdontogramFindingDraft,
  updateOdontogramTooth,
  type OdontogramFindingDto,
  type PatientOdontogram,
} from './api';
import type { ClinicalFinding, FindingDraftInput, ToothHistoryEvent, ToothId, ToothStatus } from './types';

const queryKey = (patientId: string) => ['patient-odontogram', patientId] as const;

function findingCreatedEvent(finding: ClinicalFinding): ToothHistoryEvent {
  return {
    id: `finding-${finding.id}`,
    toothId: finding.toothId,
    occurredAt: finding.createdAt,
    authorId: finding.authorId,
    author: finding.author,
    kind: 'finding_added',
    summary: `Constatare adăugată: ${finding.kind}`,
  };
}

export function PatientOdontogram({ patientId, user, timezone }: { patientId: string; user: User; timezone: string }) {
  const client = useQueryClient();
  const [selectedToothId, setSelectedToothId] = useState<ToothId>('16');
  const idempotencyKeys = useRef(new Map<string, string>());
  const query = useQuery({
    queryKey: queryKey(patientId),
    queryFn: ({ signal }) => getPatientOdontogram(patientId, signal),
    enabled: Boolean(patientId),
  });
  const canWrite = ['doctor', 'clinic_manager', 'administrator'].includes(user.role);
  const canManageCorrections = ['clinic_manager', 'administrator'].includes(user.role);

  async function updateCacheWithFinding(dto: OdontogramFindingDto, includeCreatedEvent = false) {
    const { finding, history: revisionEvents } = findingWithRevisionHistory(dto);
    const key = queryKey(patientId);
    await client.cancelQueries({ queryKey: key, exact: true });
    client.setQueryData<PatientOdontogram>(key, (current) => {
      if (!current || current.patientId !== patientId) return current;
      const existed = current.findings.some((item) => item.id === finding.id);
      const findings = existed
        ? current.findings.map((item) => item.id === finding.id ? finding : item)
        : [...current.findings, finding];
      const history = [...current.history];
      if (includeCreatedEvent && !history.some((event) => event.id === `finding-${finding.id}`)) history.push(findingCreatedEvent(finding));
      for (const event of revisionEvents) if (!history.some((item) => item.id === event.id)) history.push(event);
      return { ...current, findings, history };
    });
    void client.invalidateQueries({ queryKey: key, exact: true });
  }

  async function createFinding(toothId: ToothId, input: FindingDraftInput) {
    const retryKey = JSON.stringify({ patientId, toothId, ...input });
    let idempotencyKey = idempotencyKeys.current.get(retryKey);
    if (!idempotencyKey) {
      idempotencyKey = crypto.randomUUID();
      idempotencyKeys.current.set(retryKey, idempotencyKey);
    }
    const result = await createOdontogramFinding(patientId, toothId, input, idempotencyKey);
    await updateCacheWithFinding(result, true);
    idempotencyKeys.current.delete(retryKey);
  }

  async function updateDraft(findingId: string, input: FindingDraftInput) {
    await updateCacheWithFinding(await updateOdontogramFindingDraft(findingId, input));
  }

  async function finalizeDraft(findingId: string) {
    await updateCacheWithFinding(await finalizeOdontogramFinding(findingId));
  }

  async function correctFinding(findingId: string, input: FindingDraftInput & { reason: string }) {
    await updateCacheWithFinding(await correctOdontogramFinding(findingId, input));
  }

  async function changeToothStatus(toothId: ToothId, status: ToothStatus) {
    const response = await updateOdontogramTooth(patientId, toothId, status);
    const key = queryKey(patientId);
    await client.cancelQueries({ queryKey: key, exact: true });
    client.setQueryData<PatientOdontogram>(key, (current) => {
      if (!current || current.patientId !== patientId) return current;
      return { ...current, teeth: current.teeth.map((tooth) => tooth.id === response.toothId ? { ...tooth, status: response.status } : tooth) };
    });
    void client.invalidateQueries({ queryKey: key, exact: true });
  }

  if (query.isLoading) return <section className="card clinical-record-section" aria-busy="true"><p className="muted">Se încarcă odontograma din dosar…</p></section>;
  if (query.isError && !query.data) return <section className="card clinical-record-section">
    <p className="error" role="alert">Odontograma nu a putut fi încărcată: {apiErrorMessage(query.error)}</p>
    <button className="secondary" onClick={() => void query.refetch()}>Reîncearcă</button>
  </section>;
  if (!query.data || query.data.patientId !== patientId) return null;

  const editableDraft = (finding: ClinicalFinding) => canWrite && finding.status === 'draft' && finding.authorId === user.id;
  return <section className="card clinical-record-section patient-odontogram" aria-label="Odontograma pacientului">
    <p className="og-server-notice" role="status">Date clinice încărcate de la server pentru acest pacient.</p>
    {query.isError && <p className="error" role="alert">Reîmprospătarea odontogramei a eșuat: {apiErrorMessage(query.error)}. Sunt afișate ultimele date confirmate de server.</p>}
    <Odontogram
      key={patientId}
      teeth={query.data.teeth}
      findings={query.data.findings}
      history={query.data.history}
      selectedToothId={selectedToothId}
      timezone={timezone}
      canWrite={canWrite}
      canEditDraft={editableDraft}
      canFinalizeDraft={editableDraft}
      canCorrectFinding={(finding) => canWrite && finding.status === 'finalized' && (finding.authorId === user.id || canManageCorrections)}
      onToothSelect={setSelectedToothId}
      onFindingCreate={createFinding}
      onFindingDraftUpdate={updateDraft}
      onFindingFinalize={finalizeDraft}
      onFindingCorrect={correctFinding}
      onToothStatusChange={changeToothStatus}
    />
  </section>;
}
