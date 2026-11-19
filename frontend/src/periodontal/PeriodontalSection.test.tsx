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

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

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
      renderer = TestRenderer.create(<PeriodontalSection patientId="patient-1" user={user} timezone="Europe/Bucharest" />);
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

  it('uses the clinic time zone for the default clinical date and the finalization time', async () => {
    // 2026-11-19T23:30Z is already 20 Nov in Bucharest but still 19 Nov in Los Angeles and in UTC.
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-11-19T23:30:00Z'));
    const finalized: periodontalApi.Assessment = { ...draft, status: 'finalized', finalized_at: '2026-11-19T23:15:00Z' };
    vi.spyOn(periodontalApi, 'listAssessments').mockResolvedValue([finalized]);
    vi.spyOn(periodontalApi, 'getAssessment').mockResolvedValue(finalized);
    const textOf = (node: TestRenderer.ReactTestInstance): string => node.children.map(child => typeof child === 'string' ? child : textOf(child)).join('');
    const mount = async (timezone: string) => {
      let renderer!: TestRenderer.ReactTestRenderer;
      await act(async () => {
        renderer = TestRenderer.create(<PeriodontalSection patientId="patient-1" user={user} timezone={timezone} />);
        await new Promise(resolve => setTimeout(resolve, 0));
      });
      return renderer;
    };
    const expectedTime = (timezone: string) => new Intl.DateTimeFormat('ro-RO', { dateStyle: 'medium', timeStyle: 'short', timeZone: timezone }).format(new Date('2026-11-19T23:15:00Z'));

    const bucharest = await mount('Europe/Bucharest');
    expect(bucharest.root.findByProps({ type: 'date' }).props.value).toBe('2026-11-20');
    const bucharestText = textOf(bucharest.root);
    expect(bucharestText).toContain(`Finalizată ${expectedTime('Europe/Bucharest')}`);
    expect(bucharestText).not.toContain('2026-11-19T23:15:00Z');
    bucharest.unmount();

    const losAngeles = await mount('America/Los_Angeles');
    expect(losAngeles.root.findByProps({ type: 'date' }).props.value).toBe('2026-11-19');
    expect(textOf(losAngeles.root)).toContain(`Finalizată ${expectedTime('America/Los_Angeles')}`);
    expect(expectedTime('America/Los_Angeles')).not.toBe(expectedTime('Europe/Bucharest'));
  });
});
