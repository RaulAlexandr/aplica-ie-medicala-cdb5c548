import TestRenderer, { act } from 'react-test-renderer';
import { describe, expect, it, vi } from 'vitest';
import * as formsApi from './api';
import { FormTemplateEditor, PatientFormsPanel } from './Forms';

describe('T12 form UI', () => {
  it('starts a manager template and exposes section and question editing', async () => {
    vi.spyOn(formsApi, 'createTemplate').mockResolvedValue({ version_id: 'version-1' } as never);
    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = TestRenderer.create(<FormTemplateEditor />); });
    expect(renderer.root.findAllByType('h2')[0].children.join('')).toBe('Șablon de formular');
    expect(renderer.root.findAllByType('button').map(button => button.children.join(''))).toContain('Creează șablon');
    await act(async () => { renderer.unmount(); });
  });

  it('lists published patient forms and keeps the template version visible', async () => {
    vi.spyOn(formsApi, 'listPatientForms').mockResolvedValue([{ id: 'form-1', patient_id: 'p1', template_version_id: 'v1', template_name: 'Formular fictiv', version_number: 3, source: 'staff', status: 'validated', validated_at: null, clarification_reason: null }]);
    vi.spyOn(formsApi, 'listPublishedForms').mockResolvedValue([]);
    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = TestRenderer.create(<PatientFormsPanel patientId="p1" />); });
    expect(renderer.root.findAllByType('button').some(button => button.children.join('').includes('Formular fictiv v3 · introdus de personal · validat'))).toBe(true);
    await act(async () => { renderer.unmount(); });
  });

  it('shows conditional questions for a yes answer and exposes patient form creation', async () => {
    vi.spyOn(formsApi, 'listPatientForms').mockResolvedValue([{ id: 'form-2', patient_id: 'p2', template_version_id: 'v2', template_name: 'Fictiv', version_number: 1, source: 'staff', status: 'draft', validated_at: null, clarification_reason: null }]);
    vi.spyOn(formsApi, 'listPublishedForms').mockResolvedValue([{ id: 't1', name: 'Fictiv', version_id: 'v2', version_number: 1 }]);
    vi.spyOn(formsApi, 'getPatientForm').mockResolvedValue({ id: 'form-2', patient_id: 'p2', template_version_id: 'v2', template_name: 'Fictiv', version_number: 1, source: 'staff', status: 'draft', validated_at: null, clarification_reason: null, answers: { has_allergy: true }, definition: { sections: [{ id: 's1', title: 'Date', questions: [
      { id: 'has_allergy', label: 'Există alergie?', type: 'yes_no', required: true },
      { id: 'detail', label: 'Detaliu fictiv', type: 'long_text', required: true, condition: { question_id: 'has_allergy', operator: 'equals', value: 'yes' } },
    ] }] } });
    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = TestRenderer.create(<PatientFormsPanel patientId="p2" />); });
    const open = renderer.root.findAllByType('button').find(button => button.children.join('').includes('Fictiv v1'))!;
    await act(async () => { open.props.onClick(); await new Promise(resolve => setTimeout(resolve, 0)); });
    expect(renderer.root.findAllByType('textarea').length).toBe(1);
    expect(renderer.root.findAllByType('button').some(button => button.children.join('') === 'Începe completarea')).toBe(true);
    await act(async () => { renderer.unmount(); });
  });
});
