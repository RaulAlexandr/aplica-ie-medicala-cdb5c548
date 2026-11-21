import TestRenderer, { act } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { addInventoryMovement, inventoryHistory, inventoryOverview, reverseInventoryMovement } from './api';
import { InventoryPage } from './InventoryPage';

vi.mock('./api',()=>({
  inventoryOverview:vi.fn().mockResolvedValue({items:[{id:'item-1',name:'Material fictiv',unit:'buc',minimum_quantity:'2',quantity_total:'5',below_minimum:false,is_active:true}],locations:[{id:'loc-1',name:'Depozit fictiv',is_active:true}],balances:[{item_id:'item-1',item:'Material fictiv',unit:'buc',minimum_quantity:'2',item_total:'5',lot_id:'lot-1',lot:'LOT-FICTIV',expires_on:null,location_id:'loc-1',location:'Depozit fictiv',quantity:'5',below_minimum:false,expired:false,expiring_soon:false}]}),
  inventoryHistory:vi.fn().mockResolvedValue([]),addInventoryItem:vi.fn(),addInventoryLocation:vi.fn(),addInventoryLot:vi.fn(),addInventoryMovement:vi.fn(),reverseInventoryMovement:vi.fn(),updateInventoryItem:vi.fn(),updateInventoryLocation:vi.fn(),
}));
vi.mock('../api/client',()=>({apiErrorMessage:()=> 'Eroare',getClinic:async()=>({timezone:'Europe/Bucharest'})}));

function textOf(node:TestRenderer.ReactTestInstance):string{return node.children.map(x=>typeof x==='string'?x:textOf(x)).join('');}
async function page(role:string){const client=new QueryClient({defaultOptions:{queries:{retry:false}}});let renderer!:TestRenderer.ReactTestRenderer;await act(async()=>{renderer=TestRenderer.create(<QueryClientProvider client={client}><InventoryPage role={role}/></QueryClientProvider>);});for(let n=0;n<4;n++)await act(async()=>{await new Promise(r=>setTimeout(r,0));});return renderer;}
beforeEach(()=>{vi.clearAllMocks();vi.mocked(inventoryOverview).mockResolvedValue({items:[{id:'item-1',name:'Material fictiv',unit:'buc',minimum_quantity:'2',quantity_total:'5',below_minimum:false,is_active:true}],locations:[{id:'loc-1',name:'Depozit fictiv',is_active:true}],balances:[{item_id:'item-1',item:'Material fictiv',unit:'buc',minimum_quantity:'2',item_total:'5',lot_id:'lot-1',lot:'LOT-FICTIV',expires_on:null,location_id:'loc-1',location:'Depozit fictiv',quantity:'5',below_minimum:false,expired:false,expiring_soon:false}]});vi.mocked(inventoryHistory).mockResolvedValue([]);vi.mocked(addInventoryMovement).mockReset();vi.mocked(reverseInventoryMovement).mockReset();});
afterEach(()=>vi.unstubAllGlobals());

describe('pagina Stocuri',()=>{
  it('afișează articole fără sold sub prag și formularele managerului',async()=>{
    vi.mocked(inventoryOverview).mockResolvedValue({items:[{id:'item-1',name:'Material fictiv',unit:'buc',minimum_quantity:'2',quantity_total:'0',below_minimum:true,is_active:true}],locations:[{id:'loc-1',name:'Depozit fictiv',is_active:true}],balances:[]});
    const renderer=await page('clinic_manager');const text=textOf(renderer.root);
    expect(text).toContain('Stocuri');expect(text).toContain('Articol nou');
    const filter=renderer.root.findAllByType('select').find(x=>x.parent?.children[0]==='Filtru');
    await act(async()=>filter?.props.onChange({target:{value:'low'}}));
    expect(textOf(renderer.root)).toContain('Material fictiv · total pe toate loturile și locațiile');
    await act(async()=>renderer.unmount());
  });
  it('arată doar consumul pentru medic',async()=>{
    const renderer=await page('doctor');const text=textOf(renderer.root);
    expect(text).toContain('Înregistrează mișcare');expect(text).not.toContain('Articol nou');
    const options=renderer.root.findAllByType('option').map(x=>textOf(x));expect(options).not.toContain('Transfer');expect(options).not.toContain('Ajustare');
    await act(async()=>renderer.unmount());
  });
  it('blochează dublul click și păstrează cheia la reîncercarea aceleiași mișcări',async()=>{
    const pending: Array<{resolve:(value:unknown)=>void;reject:(reason:Error)=>void}> = [];
    vi.mocked(addInventoryMovement).mockImplementation(()=>new Promise((resolve,reject)=>pending.push({resolve,reject})));
    const renderer=await page('doctor');
    const selects=renderer.root.findAllByType('select');
    await act(async()=>{selects[2].props.onChange({target:{value:'item-1'}});selects[3].props.onChange({target:{value:'lot-1'}});selects[4].props.onChange({target:{value:'loc-1'}});});
    const form=renderer.root.findByType('form'),submit=()=>form.props.onSubmit({preventDefault(){}});
    await act(async()=>{submit();submit();});
    expect(addInventoryMovement).toHaveBeenCalledTimes(1);
    expect(renderer.root.findAllByType('button').find(x=>textOf(x)==='Salvează mișcarea')?.props.disabled).toBe(true);
    const firstKey=vi.mocked(addInventoryMovement).mock.calls[0][1] as {client_request_id:string};
    await act(async()=>{pending.shift()?.reject(new Error('temporary network error'));await new Promise(r=>setTimeout(r,0));});
    await act(async()=>{submit();});
    expect(addInventoryMovement).toHaveBeenCalledTimes(2);
    expect((vi.mocked(addInventoryMovement).mock.calls[1][1] as {client_request_id:string}).client_request_id).toBe(firstKey.client_request_id);
    await act(async()=>{pending.shift()?.resolve({});await new Promise(r=>setTimeout(r,0));});
    await act(async()=>renderer.unmount());
  });
  it('blochează dublul click la corecție și păstrează cheia după eroare',async()=>{
    vi.stubGlobal('window',{prompt:()=> 'Motiv fictiv'});
    vi.mocked(inventoryHistory).mockResolvedValue([{id:'move-1',item_id:'item-1',lot_id:'lot-1',location_id:'loc-1',kind:'receipt',quantity:'1',reason:'',transfer_id:null,reverses_movement_id:null,created_at:'2026-11-21T10:00:00Z'}]);
    const pending:Array<{resolve:(v:unknown)=>void;reject:(e:Error)=>void}>=[];
    vi.mocked(reverseInventoryMovement).mockImplementation(()=>new Promise((resolve,reject)=>pending.push({resolve,reject})));
    const renderer=await page('clinic_manager');const button=renderer.root.findAllByType('button').find(x=>textOf(x)==='Corectează')!;
    await act(async()=>{button.props.onClick();button.props.onClick();});
    expect(reverseInventoryMovement).toHaveBeenCalledTimes(1);
    const first=vi.mocked(reverseInventoryMovement).mock.calls[0][1] as {client_request_id:string};
    await act(async()=>{pending.shift()?.reject(new Error('temporary network error'));await new Promise(r=>setTimeout(r,0));});
    const retryButton=renderer.root.findAllByType('button').find(x=>textOf(x)==='Corectează')!;
    await act(async()=>retryButton.props.onClick());
    expect(reverseInventoryMovement).toHaveBeenCalledTimes(2);
    expect((vi.mocked(reverseInventoryMovement).mock.calls[1][1] as {client_request_id:string}).client_request_id).toBe(first.client_request_id);
    await act(async()=>{pending.shift()?.resolve({});await new Promise(r=>setTimeout(r,0));});
    await act(async()=>renderer.unmount());
  });
});
