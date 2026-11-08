import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '../api/client';
import { cancelPerformedProcedure, createClinicalNote, createPerformedProcedure } from './api';

afterEach(() => vi.restoreAllMocks());

describe('clinical API payloads', () => {
  it('creates a performed procedure with catalog reference and optional location as strings', async () => {
    const response = { id: 'performed-1', procedure_base_price: '125.50', procedure_currency: 'RON' };
    const post = vi.spyOn(api, 'post').mockResolvedValue({ data: response } as never);
    const payload = { appointment_id: 'appointment-1', procedure_catalog_id: 'catalog-1', tooth_fdi: '26', tooth_surface: 'occlusal,mesial', notes: 'Check restoration' };

    await expect(createPerformedProcedure(payload)).resolves.toEqual(response);
    expect(post).toHaveBeenCalledWith('/performed-procedures', payload);
    expect(post.mock.calls[0]?.[1]).not.toHaveProperty('price');
  });

  it('creates a draft with the exact patient and appointment association', async () => {
    const response = { id: 'note-1', status: 'draft' };
    const post = vi.spyOn(api, 'post').mockResolvedValue({ data: response } as never);
    const payload = { patient_id: 'patient-B', appointment_id: 'appointment-B', note_type: 'finding', content: 'Persisted finding', tooth_fdi: '36', tooth_surface: 'occlusal', is_alert: false };

    await expect(createClinicalNote(payload)).resolves.toEqual(response);
    expect(post).toHaveBeenCalledWith('/clinical-notes', payload);
  });

  it('sends the required cancel transition body', async () => {
    const response = { id: 'performed-1', status: 'cancelled' };
    const post = vi.spyOn(api, 'post').mockResolvedValue({ data: response } as never);

    await expect(cancelPerformedProcedure('performed-1')).resolves.toEqual(response);
    expect(post).toHaveBeenCalledWith('/performed-procedures/performed-1/cancel', { transition: 'cancel' });
  });
});
