import TestRenderer, { act } from 'react-test-renderer';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { User } from '../api/client';
import * as periodontalApi from './api';
import { PeriodontalSection } from './PeriodontalSection';

const user: User = {
  id: 'doctor-1', clinic_id: 'clinic-1', full_name: 'Medic fictiv',
  email: 'doctor@example.test', role: 'doctor',
};
const draft: periodontalApi.Assessment = {
  id: 'assessment-1', patient_id: 'patient-1', author_id: user.id, author_name: user.full_name,
  clinical_date: '2026-11-19', status: 'draft', previous_assessment_id: null, relationship_kind: null,
  change_reason: null, created_at: '2026-11-19T10:00:00Z', finalized_at: null, teeth: [],
};

afterEach(() => vi.restoreAllMocks());

describe('PeriodontalSection save interaction', () => {
  it('disables the save action while one persistence request is still pending', async () => {
    vi.spyOn(periodontalApi, 'listAssessments').mockResolvedValue([]);
    vi.spyOn(periodontalApi, 'getAssessment').mockResolvedValue(draft);
    vi.spyOn(periodontalApi, 'createAssessment').mockResolvedValue(draft);
    let releaseSave!: (value: periodontalApi.Assessment) => void;
    const pendingSave = new Promise<periodontalApi.Assessment>(resolve => { releaseSave = resolve; });
    const save = vi.spyOn(periodontalApi, 'saveMeasurements').mockReturnValue(pendingSave);
    let renderer!: TestRenderer.ReactTestRenderer;

    await act(async () => {
      renderer = TestRenderer.create(<PeriodontalSection patientId="patient-1" user={user} />);
      await new Promise(resolve => setTimeout(resolve, 0));
    });
    await act(async () => {
      renderer.root.findAllByType('button').find(button => button.children.includes('Evaluare nouă'))!.props.onClick();
      await new Promise(resolve => setTimeout(resolve, 0));
    });

    await act(async () => {
      renderer.root.findAllByType('button').find(button => button.children.includes('Salvează ciorna'))!.props.onClick();
      await new Promise(resolve => setTimeout(resolve, 0));
    });
    const saveButton = renderer.root.findAllByType('button').find(button => button.children.includes('Salvează ciorna'))!;
    expect(saveButton.props.disabled).toBe(true);
    expect(save).toHaveBeenCalledTimes(1);

    await act(async () => {
      releaseSave(draft);
      await pendingSave;
      await new Promise(resolve => setTimeout(resolve, 0));
    });
    expect(save).toHaveBeenCalledTimes(1);
  });
});
