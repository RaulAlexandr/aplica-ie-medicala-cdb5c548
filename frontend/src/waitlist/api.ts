import { api } from '../api/client';
export type Entry = { id:string; patient_id:string; desired_service:string|null; desired_duration_minutes:number; preferred_doctor_id:string|null; windows:Array<{weekday:number;start:string;end:string}>; valid_from:string;valid_until:string;priority:number;notes:string|null;status:string;created_at:string };
export type Slot = {doctor_id:string;room_id:string;starts_at:string;duration_minutes:number;expires_in_minutes?:number};
export const entries=async()=> (await api.get<Entry[]>('/waitlist/entries')).data;
export const createEntry=async(body:Omit<Entry,'id'|'status'|'created_at'|'patient_id'>&{patient_id:string})=>(await api.post<Entry>('/waitlist/entries',body)).data;
export const candidates=async(slot:Slot)=>(await api.post<Entry[]>('/waitlist/slots/candidates',slot)).data;
export const offer=async(id:string,slot:Slot)=>(await api.post(`/waitlist/entries/${id}/offers`,slot)).data;
export const confirm=async(id:string)=>(await api.post(`/waitlist/offers/${id}/confirm`)).data;
export const recalls=async(start?:string,end?:string)=>(await api.get('/waitlist/recalls',{params:{start,end}})).data;
