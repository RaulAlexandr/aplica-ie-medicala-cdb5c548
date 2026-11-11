import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '../api/client';
import {
  correctOdontogramFinding,
  createOdontogramFinding,
  finalizeOdontogramFinding,
  getPatientOdontogram,
  mapPatientOdontogram,
  updateOdontogramFindingDraft,
  updateOdontogramTooth,
  type OdontogramFindingDto,
  type OdontogramFindingRevisionDto,
  type PatientOdontogramDto,
} from './api';
import { ADULT_TOOTH_IDS } from './types';

const patientId = '11111111-1111-4111-8111-111111111111';
const doctor = { id: '22222222-2222-4222-8222-222222222222', name: 'Dr. Mara Ionescu' };
const revision: OdontogramFindingRevisionDto = {
  id: '44444444-4444-4444-8444-444444444444',
  previousValue: { kind: 'caries', description: 'Descriere inițială', surfaces: ['occlusal'] },
  currentValue: { kind: 'caries', description: 'Descriere corectată', surfaces: ['mesial'] },
  reason: 'Reevaluare la control', occurredAt: '2026-11-11T09:20:00Z', actor: doctor,
};
const finding: OdontogramFindingDto = {
  id: '33333333-3333-4333-8333-333333333333', toothId: '17', kind: 'caries',
  description: 'Suspiciune pe șanțul central', surfaces: ['occlusal'], status: 'finalized',
  observedAt: '2026-11-11T09:05:00Z', createdAt: '2026-11-11T09:05:00Z', author: doctor,
  revisions: [revision],
};
const payload: PatientOdontogramDto = {
  patientId,
  teeth: ADULT_TOOTH_IDS.map((toothId) => ({ toothId, status: toothId === '17' ? 'missing' as const : 'unevaluated' as const, updatedAt: null, updatedBy: null })),
  findings: [finding],
  history: [{ id: 'tooth-revision-1', toothId: '17', occurredAt: '2026-11-11T09:10:00Z', author: doctor, kind: 'status_changed', summary: 'Stare modificată', previousValue: 'present', currentValue: 'missing' }],
};

afterEach(() => vi.restoreAllMocks());

describe('odontogram API adapter', () => {
  it('maps FDI states, owner-visible finding, revisions, and server history', () => {
    const mapped = mapPatientOdontogram(patientId, payload);
    expect(mapped.teeth).toHaveLength(32);
    expect(mapped.teeth[0]).toEqual({ id: '18', status: 'unevaluated' });
    expect(mapped.teeth.find((tooth) => tooth.id === '17')).toEqual({ id: '17', status: 'missing' });
    expect(mapped.findings[0]).toMatchObject({ id: finding.id, toothId: '17', status: 'finalized', authorId: doctor.id, author: doctor.name });
    expect(mapped.history).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: 'tooth-revision-1', author: doctor.name, previousValue: 'present', currentValue: 'missing' }),
      expect.objectContaining({ id: revision.id, reason: revision.reason, previousValue: 'caries: Descriere inițială' }),
    ]));
  });

  it('derives correction history from finding revisions when the response omits the optional aggregate history', () => {
    const { history: _history, ...withoutAggregate } = payload;
    const mapped = mapPatientOdontogram(patientId, withoutAggregate);
    expect(mapped.history).toEqual([
      expect.objectContaining({ id: `finding-${finding.id}`, kind: 'finding_added', author: doctor.name }),
      expect.objectContaining({ id: revision.id, previousValue: 'caries: Descriere inițială', currentValue: 'caries: Descriere corectată', reason: revision.reason }),
    ]);
  });

  it('rejects a response that belongs to another patient', () => {
    expect(() => mapPatientOdontogram('patient-other', payload)).toThrow('nu aparține pacientului selectat');
  });

  it('uses the authenticated API instance and only contract routes and payloads', async () => {
    const get = vi.spyOn(api, 'get').mockResolvedValue({ data: payload } as never);
    const put = vi.spyOn(api, 'put').mockResolvedValue({ data: { toothId: '17', status: 'missing', previousStatus: 'present', updatedAt: '2026-11-11T09:10:00Z', updatedBy: doctor } } as never);
    const post = vi.spyOn(api, 'post').mockResolvedValue({ data: finding } as never);
    const patch = vi.spyOn(api, 'patch').mockResolvedValue({ data: finding } as never);

    await getPatientOdontogram(patientId);
    await updateOdontogramTooth(patientId, '17', 'missing');
    await createOdontogramFinding(patientId, '17', { kind: 'caries', description: 'Leziune', surfaces: ['occlusal'] }, '55555555-5555-4555-8555-555555555555');
    await updateOdontogramFindingDraft(finding.id, { kind: 'caries', description: 'Draft actualizat', surfaces: ['mesial'] });
    await finalizeOdontogramFinding(finding.id);
    await correctOdontogramFinding(finding.id, { kind: 'caries', description: 'Corectat', surfaces: ['mesial'], reason: 'Reevaluare' });

    expect(get).toHaveBeenCalledWith(`/odontogram/patients/${patientId}`, { signal: undefined });
    expect(put).toHaveBeenCalledWith(`/odontogram/patients/${patientId}/teeth/17`, { status: 'missing' });
    expect(post).toHaveBeenNthCalledWith(1, `/odontogram/patients/${patientId}/findings`, {
      toothId: '17', kind: 'caries', description: 'Leziune', surfaces: ['occlusal'], status: 'draft',
    }, { headers: { 'Idempotency-Key': '55555555-5555-4555-8555-555555555555' } });
    expect(patch).toHaveBeenCalledWith(`/odontogram/findings/${finding.id}`, { kind: 'caries', description: 'Draft actualizat', surfaces: ['mesial'] });
    expect(post).toHaveBeenNthCalledWith(2, `/odontogram/findings/${finding.id}/finalize`);
    expect(post).toHaveBeenNthCalledWith(3, `/odontogram/findings/${finding.id}/corrections`, {
      kind: 'caries', description: 'Corectat', surfaces: ['mesial'], reason: 'Reevaluare',
    });
  });
});
