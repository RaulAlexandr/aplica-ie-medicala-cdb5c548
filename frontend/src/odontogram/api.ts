import { api } from '../api/client';
import type { ClinicalFinding, FindingDraftInput, OdontogramTooth, ToothHistoryEvent, ToothId, ToothStatus, ToothSurface } from './types';

export interface OdontogramActor {
  id: string;
  name: string;
}

export interface OdontogramFindingRevisionDto {
  id: string;
  previousValue: { kind: string; description: string; surfaces: ToothSurface[] };
  currentValue: { kind: string; description: string; surfaces: ToothSurface[] };
  reason: string;
  occurredAt: string;
  actor: OdontogramActor;
}

export interface OdontogramFindingDto {
  id: string;
  toothId: ToothId;
  kind: string;
  description: string;
  surfaces: ToothSurface[];
  status: 'draft' | 'finalized';
  observedAt: string;
  createdAt: string;
  author: OdontogramActor;
  revisions: OdontogramFindingRevisionDto[];
}

export interface OdontogramToothDto {
  toothId: ToothId;
  status: ToothStatus;
  updatedAt: string | null;
  updatedBy: OdontogramActor | null;
}

export interface OdontogramHistoryDto {
  id: string;
  toothId: ToothId;
  occurredAt: string;
  author: OdontogramActor | string;
  kind: ToothHistoryEvent['kind'];
  summary: string;
  previousValue?: string;
  currentValue?: string;
  reason?: string | null;
}

export interface PatientOdontogramDto {
  patientId: string;
  teeth: OdontogramToothDto[];
  findings: OdontogramFindingDto[];
  /** The API may provide the combined tooth-state and finding audit history. */
  history?: OdontogramHistoryDto[];
}

export interface PatientOdontogram {
  patientId: string;
  teeth: OdontogramTooth[];
  findings: ClinicalFinding[];
  history: ToothHistoryEvent[];
}

export type OdontogramMutationFinding = Pick<OdontogramFindingDto, 'id' | 'toothId' | 'kind' | 'description' | 'surfaces' | 'status' | 'observedAt' | 'createdAt' | 'author' | 'revisions'>;
export type ToothStateResponse = Pick<OdontogramToothDto, 'toothId' | 'status' | 'updatedAt' | 'updatedBy'> & { previousStatus: ToothStatus };

function mapFinding(finding: OdontogramFindingDto): ClinicalFinding {
  return {
    id: finding.id,
    toothId: finding.toothId,
    kind: finding.kind,
    description: finding.description,
    surfaces: [...finding.surfaces],
    status: finding.status,
    observedAt: finding.observedAt,
    createdAt: finding.createdAt,
    authorId: finding.author.id,
    author: finding.author.name,
  };
}

function mapHistoryEvent(event: OdontogramHistoryDto): ToothHistoryEvent {
  return {
    id: event.id,
    toothId: event.toothId,
    occurredAt: event.occurredAt,
    author: typeof event.author === 'string' ? event.author : event.author.name,
    kind: event.kind,
    summary: event.summary,
    previousValue: event.previousValue,
    currentValue: event.currentValue,
    reason: event.reason ?? undefined,
  };
}

function revisionHistory(finding: OdontogramFindingDto): ToothHistoryEvent[] {
  const events: ToothHistoryEvent[] = [{
    id: `finding-${finding.id}`,
    toothId: finding.toothId,
    occurredAt: finding.createdAt,
    author: finding.author.name,
    kind: 'finding_added',
    summary: `Constatare adăugată: ${finding.kind}`,
  }];
  return events.concat(finding.revisions.map((revision) => ({
    id: revision.id,
    toothId: finding.toothId,
    occurredAt: revision.occurredAt,
    author: revision.actor.name,
    kind: 'finding_corrected' as const,
    summary: 'Constatare corectată',
    previousValue: `${revision.previousValue.kind}: ${revision.previousValue.description}`,
    currentValue: `${revision.currentValue.kind}: ${revision.currentValue.description}`,
    reason: revision.reason,
  })));
}

export function mapPatientOdontogram(patientId: string, response: PatientOdontogramDto): PatientOdontogram {
  if (response.patientId !== patientId) throw new Error('Odontograma răspunsă nu aparține pacientului selectat.');
  const findings = response.findings.map(mapFinding);
  const historyById = new Map((response.history ?? []).map((event) => {
    const mapped = mapHistoryEvent(event);
    return [mapped.id, mapped] as const;
  }));
  for (const event of response.findings.flatMap(revisionHistory)) {
    historyById.set(event.id, { ...historyById.get(event.id), ...event });
  }
  return {
    patientId,
    teeth: response.teeth.map((tooth) => ({ id: tooth.toothId, status: tooth.status })),
    findings,
    history: [...historyById.values()],
  };
}

export async function getPatientOdontogram(patientId: string, signal?: AbortSignal): Promise<PatientOdontogram> {
  const { data } = await api.get<PatientOdontogramDto>(`/odontogram/patients/${patientId}`, { signal });
  return mapPatientOdontogram(patientId, data);
}

export async function updateOdontogramTooth(patientId: string, toothId: ToothId, state: ToothStatus): Promise<ToothStateResponse> {
  const { data } = await api.put<ToothStateResponse>(`/odontogram/patients/${patientId}/teeth/${toothId}`, { status: state });
  return data;
}

export async function createOdontogramFinding(patientId: string, toothId: ToothId, input: FindingDraftInput, idempotencyKey: string): Promise<OdontogramFindingDto> {
  const { data } = await api.post<OdontogramFindingDto>(`/odontogram/patients/${patientId}/findings`, {
    toothId, ...input, status: 'draft',
  }, { headers: { 'Idempotency-Key': idempotencyKey } });
  return data;
}

export async function updateOdontogramFindingDraft(findingId: string, input: FindingDraftInput): Promise<OdontogramFindingDto> {
  const { data } = await api.patch<OdontogramFindingDto>(`/odontogram/findings/${findingId}`, input);
  return data;
}

export async function finalizeOdontogramFinding(findingId: string): Promise<OdontogramFindingDto> {
  const { data } = await api.post<OdontogramFindingDto>(`/odontogram/findings/${findingId}/finalize`);
  return data;
}

export async function correctOdontogramFinding(
  findingId: string,
  input: FindingDraftInput & { reason: string },
): Promise<OdontogramFindingDto> {
  const { data } = await api.post<OdontogramFindingDto>(`/odontogram/findings/${findingId}/corrections`, input);
  return data;
}

export function findingWithRevisionHistory(finding: OdontogramFindingDto): { finding: ClinicalFinding; history: ToothHistoryEvent[] } {
  return { finding: mapFinding(finding), history: revisionHistory(finding).slice(1) };
}
