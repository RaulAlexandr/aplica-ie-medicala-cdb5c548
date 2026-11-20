import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '../api/client';
import { compareAssessments, createAssessment, emptyTooth, finalizeAssessment, getAssessment, listAssessments, saveMeasurements, sites } from './api';

afterEach(() => vi.restoreAllMocks());

describe('periodontal API contract', () => {
  it('uses six canonical sites and keeps missing values distinct from false and zero', () => {
    const row = emptyTooth('16');
    expect(sites).toHaveLength(6);
    expect(row.sites).toHaveLength(6);
    expect(row.sites[0]).toMatchObject({ probing_depth_mm: null, recession_mm: null, bop: null });
    row.sites[0].probing_depth_mm = 0;
    row.sites[0].bop = false;
    expect(row.sites[0]).toMatchObject({ probing_depth_mm: 0, bop: false });
  });

  it('routes create, reload, save, finalize, history and comparison through the authenticated API', async () => {
    const get = vi.spyOn(api, 'get').mockResolvedValue({ data: [] } as never);
    const post = vi.spyOn(api, 'post').mockResolvedValue({ data: { id: 'evaluation-1' } } as never);
    const put = vi.spyOn(api, 'put').mockResolvedValue({ data: { id: 'evaluation-1' } } as never);
    await listAssessments('patient-1');
    await getAssessment('evaluation-1');
    await createAssessment('patient-1', { clinical_date: '2026-11-19' });
    await saveMeasurements('evaluation-1', [emptyTooth('16')]);
    await finalizeAssessment('evaluation-1');
    await compareAssessments('patient-1', 'evaluation-1', 'evaluation-2');
    expect(get).toHaveBeenNthCalledWith(1, '/periodontal/patients/patient-1/assessments');
    expect(get).toHaveBeenNthCalledWith(2, '/periodontal/assessments/evaluation-1');
    expect(post).toHaveBeenNthCalledWith(1, '/periodontal/patients/patient-1/assessments', { clinical_date: '2026-11-19' });
    expect(put).toHaveBeenCalledWith('/periodontal/assessments/evaluation-1/measurements', { teeth: [emptyTooth('16')] });
    expect(post).toHaveBeenNthCalledWith(2, '/periodontal/assessments/evaluation-1/finalize');
    expect(get).toHaveBeenNthCalledWith(3, '/periodontal/patients/patient-1/compare', { params: { first_id: 'evaluation-1', second_id: 'evaluation-2' } });
  });
});
