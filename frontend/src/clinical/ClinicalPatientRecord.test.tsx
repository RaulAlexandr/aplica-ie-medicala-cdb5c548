import TestRenderer, { act } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { StrictMode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { User } from '../api/client';
import * as clientApi from '../api/client';
import * as clinicalApi from './api';
import type { PatientRecord } from './api';
import * as odontogramApi from '../odontogram/api';
import { ADULT_TOOTH_IDS } from '../odontogram/types';
import { ClinicalPatientRecord } from './ClinicalPatientRecord';
import { TreatmentPlansPanel } from '../treatment-plans/TreatmentPlansPanel';
import { PeriodontalSection } from '../periodontal/PeriodontalSection';
import * as periodontalApi from '../periodontal/api';
import { DocumentsPanel } from '../documents/DocumentsPanel';
import * as documentsApi from '../documents/api';
import * as treatmentPlansApi from '../treatment-plans/api';

const doctor: User = { id: 'doctor-1', clinic_id: 'clinic-1', full_name: 'Dr. Sample', email: 'doctor@example.test', role: 'doctor' };

function makeRecord(patientId: string): PatientRecord {
  return {
    patient: {
      id: patientId, first_name: 'Ana', last_name: 'Test', date_of_birth: null, sex: null, phone: '0700000000', email: null,
      allergies: null, medications: null, chronic_diseases: null, pregnancy_status: null, smoking_status: null,
      medical_alerts: null, relevant_medical_conditions: null,
    },
    alerts: [], clinical_notes: [], performed_procedures: [], timeline: [],
  };
}

function tree(patientId: string, client: QueryClient) {
  return <StrictMode><QueryClientProvider client={client}><ClinicalPatientRecord patientId={patientId} user={doctor} /></QueryClientProvider></StrictMode>;
}

function newClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
}

async function flush() { await new Promise(resolve => setTimeout(resolve, 10)); }
async function waitForOdontogram(renderer: TestRenderer.ReactTestRenderer) {
  for (let attempt = 0; attempt < 30; attempt += 1) {
    if (renderer.root.findAll(node => node.props.className?.split(' ').includes('patient-odontogram')).length) return;
    await act(async () => { await flush(); });
  }
  throw new Error('Patient record odontogram did not load');
}

afterEach(() => vi.restoreAllMocks());

describe('patient clinical record integration', () => {
  it('mounts one odontogram and one finding form, and resets them when the patient changes', async () => {
    vi.spyOn(clientApi, 'getClinic').mockResolvedValue({ id: 'clinic-1', name: 'Clinic', timezone: 'Europe/Bucharest', created_at: '', updated_at: '' });
    vi.spyOn(clinicalApi, 'getPatientRecord').mockImplementation(async patientId => makeRecord(patientId));
    vi.spyOn(clinicalApi, 'getPatientRevisions').mockResolvedValue([]);
    vi.spyOn(clinicalApi, 'listPatientTimeline').mockResolvedValue([]);
    vi.spyOn(clinicalApi, 'listPatientClinicalNotes').mockResolvedValue([]);
    vi.spyOn(odontogramApi, 'getPatientOdontogram').mockImplementation(async patientId => odontogramApi.mapPatientOdontogram(patientId, {
      patientId,
      teeth: ADULT_TOOTH_IDS.map(toothId => ({ toothId, status: 'unevaluated', updatedAt: null, updatedBy: null })),
      findings: [],
    }));

    const client = newClient();
    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => { renderer = TestRenderer.create(tree('patient-A', client)); await flush(); });
    await waitForOdontogram(renderer);

    const expectSingleOdontogram = () => {
      expect(renderer.root.findAll(node => node.props.className?.split(' ').includes('patient-odontogram'))).toHaveLength(1);
      expect(renderer.root.findAllByType('form').filter(form => form.props.className === 'og-new-finding')).toHaveLength(1);
      expect(renderer.root.findAllByType(TreatmentPlansPanel)).toHaveLength(1);
    };
    expectSingleOdontogram();
    const selectedTooth = renderer.root.findAllByType('button').find(button => button.props['aria-pressed'] === true);
    expect(selectedTooth?.props['data-tooth-id']).toBe('16');

    await act(async () => { renderer.update(tree('patient-B', client)); await flush(); });
    await waitForOdontogram(renderer);
    expectSingleOdontogram();
    expect(odontogramApi.getPatientOdontogram).toHaveBeenCalledWith('patient-B', expect.anything());
    const selectedAfterSwitch = renderer.root.findAllByType('button').find(button => button.props['aria-pressed'] === true);
    expect(selectedAfterSwitch?.props['data-tooth-id']).toBe('16');
  });

  it('does not render clinic-local clinical features or guessed UTC timestamps when clinic context fails', async () => {
    vi.spyOn(clientApi, 'getClinic').mockRejectedValue(new Error('clinic context unavailable'));
    vi.spyOn(clinicalApi, 'getPatientRecord').mockResolvedValue({
      ...makeRecord('patient-A'),
      alerts: [{ id: 'alert-1', title: 'Alert', note_type: 'medical', content: 'Fictional alert', author_name: 'Doctor', created_at: '2027-01-04T23:30:00Z' }],
    } as PatientRecord);
    vi.spyOn(clinicalApi, 'getPatientRevisions').mockResolvedValue([]);

    const renderer = TestRenderer.create(tree('patient-A', newClient()));
    for (let attempt = 0; attempt < 20; attempt += 1) {
      if (JSON.stringify(renderer.toJSON()).includes('Fictional alert')) break;
      await act(async () => { await flush(); });
    }
    const rendered = renderer.toJSON();
    expect(JSON.stringify(rendered)).toContain('Clinic time unavailable');
    expect(JSON.stringify(rendered)).toContain('Clinic time zone could not be loaded');
    expect(renderer.root.findAll(node => node.props.className?.split(' ').includes('patient-odontogram'))).toHaveLength(0);
    expect(JSON.stringify(rendered)).not.toContain('Jan 4, 2027');
    renderer.unmount();
  });

  it('mounts T08 plans, T09 periodontal and T11 documents once beside one odontogram, all in the clinic time zone', async () => {
    vi.spyOn(clientApi, 'getClinic').mockResolvedValue({ id: 'clinic-1', name: 'Clinic', timezone: 'America/Los_Angeles', created_at: '', updated_at: '' });
    vi.spyOn(clinicalApi, 'getPatientRecord').mockImplementation(async patientId => makeRecord(patientId));
    vi.spyOn(clinicalApi, 'getPatientRevisions').mockResolvedValue([]);
    vi.spyOn(clinicalApi, 'listPatientTimeline').mockResolvedValue([]);
    vi.spyOn(clinicalApi, 'listPatientClinicalNotes').mockResolvedValue([]);
    vi.spyOn(odontogramApi, 'getPatientOdontogram').mockImplementation(async patientId => odontogramApi.mapPatientOdontogram(patientId, {
      patientId, teeth: ADULT_TOOTH_IDS.map(toothId => ({ toothId, status: 'unevaluated', updatedAt: null, updatedBy: null })), findings: [],
    }));
    vi.spyOn(treatmentPlansApi, 'listPatientPlans').mockResolvedValue([]);
    vi.spyOn(periodontalApi, 'listAssessments').mockResolvedValue([]);
    vi.spyOn(documentsApi, 'getDocumentLimits').mockResolvedValue({
      accepted_types: ['image/jpeg', 'image/png', 'application/pdf'], accepted_extensions: ['.jpg', '.jpeg', '.png', '.pdf'],
      max_bytes: 20 * 1024 * 1024, max_image_side: 12000, max_image_pixels: 50_000_000,
      categories: ['photo', 'radiograph', 'document', 'other'], image_only_categories: ['photo', 'radiograph'], not_supported: ['DICOM'], antivirus_scan: false,
    });
    vi.spyOn(documentsApi, 'listPatientDocuments').mockResolvedValue([]);
    vi.spyOn(documentsApi, 'listVisitOptions').mockResolvedValue([]);

    const renderer = TestRenderer.create(tree('patient-A', newClient()));
    await waitForOdontogram(renderer);
    expect(renderer.root.findAll(node => node.props.className?.split(' ').includes('patient-odontogram'))).toHaveLength(1);
    expect(renderer.root.findAllByType(TreatmentPlansPanel)).toHaveLength(1);
    expect(renderer.root.findAllByType(PeriodontalSection)).toHaveLength(1);
    expect(renderer.root.findAllByType(DocumentsPanel)).toHaveLength(1);
    for (const panel of [TreatmentPlansPanel, PeriodontalSection, DocumentsPanel]) {
      expect(renderer.root.findByType(panel).props.timezone).toBe('America/Los_Angeles');
    }
    renderer.unmount();
  });

  it('hides the periodontal and documents panels with the other clinic-local features when clinic context fails', async () => {
    vi.spyOn(clientApi, 'getClinic').mockRejectedValue(new Error('clinic context unavailable'));
    vi.spyOn(clinicalApi, 'getPatientRecord').mockImplementation(async patientId => makeRecord(patientId));
    vi.spyOn(clinicalApi, 'getPatientRevisions').mockResolvedValue([]);
    const listAssessments = vi.spyOn(periodontalApi, 'listAssessments').mockResolvedValue([]);
    const listDocuments = vi.spyOn(documentsApi, 'listPatientDocuments').mockResolvedValue([]);

    const renderer = TestRenderer.create(tree('patient-A', newClient()));
    for (let attempt = 0; attempt < 20; attempt += 1) {
      if (JSON.stringify(renderer.toJSON()).includes('Clinic time zone could not be loaded')) break;
      await act(async () => { await flush(); });
    }
    expect(JSON.stringify(renderer.toJSON())).toContain('Clinic time zone could not be loaded');
    expect(renderer.root.findAllByType(PeriodontalSection)).toHaveLength(0);
    expect(renderer.root.findAllByType(DocumentsPanel)).toHaveLength(0);
    expect(listAssessments).not.toHaveBeenCalled();
    expect(listDocuments).not.toHaveBeenCalled();
    renderer.unmount();
  });
});
