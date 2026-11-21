import { api } from '../api/client';

export type StockItem = { id:string; name:string; unit:string; minimum_quantity:string; quantity_total:string; below_minimum:boolean; is_active:boolean };
export type StockLocation = { id:string; name:string; is_active:boolean };
export type StockLot = { id:string; item_id:string; item:string; code:string; expires_on:string|null };
export type Balance = { item_id:string; item:string; unit:string; minimum_quantity:string; item_total:string; lot_id:string; lot:string; expires_on:string|null; location_id:string; location:string; quantity:string; below_minimum:boolean; expired:boolean; expiring_soon:boolean };
export type Movement = { id:string; item_id:string; item:string; lot_id:string; lot:string; location_id:string; location:string; author:string; kind:string; quantity:string; reason:string; transfer_id:string|null; reverses_movement_id:string|null; is_correction:boolean; is_corrected:boolean; created_at:string };
export type Overview = { items:StockItem[]; locations:StockLocation[]; lots:StockLot[]; balances:Balance[] };
export const inventoryOverview = async () => (await api.get<Overview>('/inventory')).data;
export const inventoryHistory = async () => (await api.get<Movement[]>('/inventory/movements')).data;
export const addInventoryItem = async (body:unknown) => api.post('/inventory/items',body);
export const addInventoryLocation = async (body:unknown) => api.post('/inventory/locations',body);
export const updateInventoryItem = async (id:string,body:unknown) => api.patch(`/inventory/items/${id}`,body);
export const updateInventoryLocation = async (id:string,body:unknown) => api.patch(`/inventory/locations/${id}`,body);
export const addInventoryLot = async (body:unknown) => api.post('/inventory/lots',body);
export const addInventoryMovement = async (kind:string,body:unknown) => api.post(`/inventory/movements/${kind}`,body);
export const reverseInventoryMovement = async (id:string,body:unknown) => api.post(`/inventory/movements/${id}/reverse`,body);
