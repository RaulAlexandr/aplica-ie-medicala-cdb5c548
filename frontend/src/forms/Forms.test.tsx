import TestRenderer, { act } from 'react-test-renderer';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as formsApi from './api';
import { FormTemplateEditor, PatientFormsPanel } from './Forms';

afterEach(() => vi.restoreAllMocks());

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

  it('hides a dependent question and clears its draft answer when its condition turns false', async () => {
    vi.spyOn(formsApi, 'listPatientForms').mockResolvedValue([{ id: 'conditional-form', patient_id: 'p3', template_version_id: 'v3', template_name: 'Conditional', version_number: 1, source: 'staff', status: 'draft', validated_at: null, clarification_reason: null }]);
    vi.spyOn(formsApi, 'listPublishedForms').mockResolvedValue([]);
    vi.spyOn(formsApi, 'getPatientForm').mockResolvedValue({ id: 'conditional-form', patient_id: 'p3', template_version_id: 'v3', template_name: 'Conditional', version_number: 1, source: 'staff', status: 'draft', validated_at: null, clarification_reason: null, answers: { has_allergy: true, allergy_detail: 'Private draft' }, definition: { sections: [{ id: 's', title: 'Section', questions: [
      { id: 'has_allergy', label: 'Has allergy', type: 'yes_no', required: true },
      { id: 'allergy_detail', label: 'Allergy details', type: 'long_text', required: true, condition: { question_id: 'has_allergy', operator: 'equals', value: 'yes' } },
    ] }] } });
    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = TestRenderer.create(<PatientFormsPanel patientId="p3" />); });
    const open = renderer.root.findAllByType('button').find(button => button.children.join('').includes('Conditional v1'))!;
    await act(async () => { open.props.onClick(); await new Promise(resolve => setTimeout(resolve, 0)); });
    expect(renderer.root.findAllByType('textarea')).toHaveLength(1);
    expect(renderer.root.findByType('textarea').props.value).toBe('Private draft');
    const controller = renderer.root.findByProps({ 'aria-label': 'Has allergy' });
    act(() => { controller.props.onChange({ target: { value: 'no' } }); });
    expect(renderer.root.findAllByType('textarea')).toHaveLength(0);
    act(() => { controller.props.onChange({ target: { value: 'yes' } }); });
    expect(renderer.root.findByType('textarea').props.value).toBe('');
    await act(async () => { renderer.unmount(); });
  });

  it('lets a clinician validate a submitted form', async () => {
    vi.spyOn(formsApi, 'listPatientForms').mockResolvedValue([{ id: 'submitted-form', patient_id: 'p4', template_version_id: 'v4', template_name: 'Review', version_number: 1, source: 'staff', status: 'submitted', validated_at: null, clarification_reason: null }]);
    vi.spyOn(formsApi, 'listPublishedForms').mockResolvedValue([]);
    vi.spyOn(formsApi, 'getPatientForm').mockResolvedValue({ id: 'submitted-form', patient_id: 'p4', template_version_id: 'v4', template_name: 'Review', version_number: 1, source: 'staff', status: 'submitted', validated_at: null, clarification_reason: null, answers: { has_allergy: false }, definition: { sections: [{ id: 's', title: 'Section', questions: [{ id: 'has_allergy', label: 'Has allergy', type: 'yes_no', required: true }] }] } });
    vi.spyOn(formsApi, 'getFormRevisions').mockResolvedValue([]);
    const review = vi.spyOn(formsApi, 'reviewForm').mockResolvedValue({ id: 'submitted-form', patient_id: 'p4', template_version_id: 'v4', template_name: 'Review', version_number: 1, source: 'staff', status: 'validated', validated_at: '2026-11-20T12:00:00Z', clarification_reason: null } as never);
    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = TestRenderer.create(<PatientFormsPanel patientId="p4" canReview />); });
    const open = renderer.root.findAllByType('button').find(button => button.children.join('').includes('Review v1'))!;
    await act(async () => { open.props.onClick(); await new Promise(resolve => setTimeout(resolve, 0)); });
    const validate = renderer.root.findAllByType('button').find(button => button.children.join('') === 'Validează')!;
    await act(async () => { await validate.props.onClick(); });
    expect(review).toHaveBeenCalledWith('submitted-form', 'validated', '');
    await act(async () => { renderer.unmount(); });
  });

  it('shows portal submissions and staff corrections as separate histories in T12', async () => {
    vi.spyOn(formsApi, 'listPatientForms').mockResolvedValue([{ id: 'portal-form', patient_id: 'p6', template_version_id: 'v6', template_name: 'Istoric portal', version_number: 2, source: 'patient', status: 'submitted', validated_at: null, clarification_reason: null }]);
    vi.spyOn(formsApi, 'listPublishedForms').mockResolvedValue([]);
    vi.spyOn(formsApi, 'getPatientForm').mockResolvedValue({ id: 'portal-form', patient_id: 'p6', template_version_id: 'v6', template_name: 'Istoric portal', version_number: 2, source: 'patient', status: 'submitted', validated_at: null, clarification_reason: null, answers: { detail: 'Răspuns curent' }, portal_submissions: [
      { revision_number: 1, submitted_at: '2026-11-25T10:00:00Z', author: 'Ana Fictivă', source: 'patient', template_version_id: 'v6', answers: { detail: 'Prima trimitere' } },
      { revision_number: 2, submitted_at: '2026-11-25T11:00:00Z', author: 'Ana Fictivă', source: 'patient', template_version_id: 'v6', answers: { detail: 'A doua trimitere' } },
    ], definition: { sections: [{ id: 's', title: 'Section', questions: [{ id: 'detail', label: 'Detaliu', type: 'long_text', required: true }] }] } });
    vi.spyOn(formsApi, 'getFormRevisions').mockResolvedValue([{ revision_number: 1, answers: { detail: 'Răspuns curent' }, status: 'validated', reason: 'Corectare clinică', actor_id: 'staff-id', actor_name: 'Dr. Fictiv', created_at: '2026-11-25T12:00:00Z' }]);
    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = TestRenderer.create(<PatientFormsPanel patientId="p6" canReview />); });
    const open = renderer.root.findAllByType('button').find(button => button.children.join('').includes('Istoric portal v2'))!;
    await act(async () => { open.props.onClick(); await new Promise(resolve => setTimeout(resolve, 0)); });
    const text = renderer.root.findAllByType('strong').map(item => item.children.join('')).join(' ');
    expect(text).toContain('Completat de pacient · trimiterea #1');
    expect(text).toContain('Completat de pacient · trimiterea #2');
    expect(renderer.root.findAllByType('dd').map(item => item.children.join(''))).toEqual(['Prima trimitere', 'A doua trimitere']);
    expect(text).toContain('Corecția personalului #1');
    expect(JSON.stringify(renderer.toJSON())).toContain('Dr. Fictiv');
    expect(renderer.root.findAllByProps({ 'aria-label': 'Trimiterile pacientului' })).toHaveLength(1);
    expect(renderer.root.findAllByProps({ 'aria-label': 'Istoricul corecțiilor personalului' })).toHaveLength(1);
    await act(async () => { renderer.unmount(); });
  });

  it('shows reception status without loading answers or clinical revision history', async () => {
    vi.spyOn(formsApi, 'listPatientForms').mockResolvedValue([{ id: 'reception-form', patient_id: 'p5', template_version_id: 'v5', template_name: 'Reception view', version_number: 2, source: 'staff', status: 'draft', validated_at: null, clarification_reason: null }]);
    vi.spyOn(formsApi, 'listPublishedForms').mockResolvedValue([]);
    vi.spyOn(formsApi, 'getPatientForm').mockResolvedValue({ id: 'reception-form', patient_id: 'p5', template_version_id: 'v5', template_name: 'Reception view', version_number: 2, source: 'staff', status: 'draft', validated_at: null, clarification_reason: null, definition: { sections: [{ id: 's', title: 'Section', questions: [
      { id: 'has_allergy', label: 'Has allergy', type: 'yes_no', required: true },
      { id: 'allergy_detail', label: 'Allergy details', type: 'long_text', required: true, condition: { question_id: 'has_allergy', operator: 'equals', value: 'yes' } },
    ] }] } });
    const revisions = vi.spyOn(formsApi, 'getFormRevisions').mockResolvedValue([]);
    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = TestRenderer.create(<PatientFormsPanel patientId="p5" canReview={false} />); });
    expect(renderer.root.findAllByType('button').some(button => button.children.join('').includes('Reception view v2 · introdus de personal · ciornă'))).toBe(true);
    const open = renderer.root.findAllByType('button').find(button => button.children.join('').includes('Reception view v2'))!;
    await act(async () => { open.props.onClick(); await new Promise(resolve => setTimeout(resolve, 0)); });
    expect(renderer.root.findAllByType('textarea')).toHaveLength(0);
    const controller = renderer.root.findByProps({ 'aria-label': 'Has allergy' });
    act(() => { controller.props.onChange({ target: { value: 'yes' } }); });
    expect(renderer.root.findByType('textarea').props.value).toBe('');
    expect(renderer.root.findAllByProps({ 'aria-label': 'Istoricul corecțiilor' })).toHaveLength(0);
    expect(revisions).not.toHaveBeenCalled();
    await act(async () => { renderer.unmount(); });
  });

  it('does not submit if saving the answer snapshot fails', async () => {
    vi.spyOn(formsApi, 'listPatientForms').mockResolvedValue([{ id: 'save-fails', patient_id: 'p6', template_version_id: 'v6', template_name: 'Save first', version_number: 1, source: 'staff', status: 'draft', validated_at: null, clarification_reason: null }]);
    vi.spyOn(formsApi, 'listPublishedForms').mockResolvedValue([]);
    vi.spyOn(formsApi, 'getPatientForm').mockResolvedValue({ id: 'save-fails', patient_id: 'p6', template_version_id: 'v6', template_name: 'Save first', version_number: 1, source: 'staff', status: 'draft', validated_at: null, clarification_reason: null, answers: { has_allergy: false }, definition: { sections: [{ id: 's', title: 'Section', questions: [{ id: 'has_allergy', label: 'Has allergy', type: 'yes_no', required: true }] }] } });
    const save = vi.spyOn(formsApi, 'saveAnswers').mockRejectedValue(new Error('save failed'));
    const submit = vi.spyOn(formsApi, 'submitForm').mockResolvedValue({ id: 'save-fails' } as never);
    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = TestRenderer.create(<PatientFormsPanel patientId="p6" />); });
    const open = renderer.root.findAllByType('button').find(button => button.children.join('').includes('Save first v1'))!;
    await act(async () => { open.props.onClick(); await new Promise(resolve => setTimeout(resolve, 0)); });
    const send = renderer.root.findAllByType('button').find(button => button.children.join('') === 'Trimite')!;
    await act(async () => { await send.props.onClick(); });
    expect(save).toHaveBeenCalledTimes(1);
    expect(submit).not.toHaveBeenCalled();
    expect(renderer.root.findByProps({ role: 'alert' }).children.join('')).toContain('save failed');
    await act(async () => { renderer.unmount(); });
  });
});
