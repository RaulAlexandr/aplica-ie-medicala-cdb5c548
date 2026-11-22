import { api } from '../api/client';

export type ProcedureLine = { procedure_catalog_id: string; code: string; name: string; quantity: number; manual_quantity: number | null; plan_quantity: number | null; source: 'manual' | 'plan' | 'both' };
export type AppointmentProcedureSet = { appointment_id: string; revision: number; procedures: ProcedureLine[] };
export type DesiredProcedure = { procedure_catalog_id: string; quantity: number };
export async function getAppointmentProcedures(id: string) { return (await api.get<AppointmentProcedureSet>(`/appointment-procedures/${id}`)).data; }
export async function replaceAppointmentProcedures(id: string, revision: number, procedures: DesiredProcedure[]) { return (await api.put<AppointmentProcedureSet>(`/appointment-procedures/${id}`, { expected_revision: revision, procedures })).data; }
