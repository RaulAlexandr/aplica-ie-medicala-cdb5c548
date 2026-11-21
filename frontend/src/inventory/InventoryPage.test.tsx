import TestRenderer, { act } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { addInventoryItem, addInventoryLocation, addInventoryMovement, inventoryHistory, inventoryOverview, reverseInventoryMovement } from './api';
import { InventoryPage } from './InventoryPage';

vi.mock('./api',()=>({
  inventoryOverview:vi.fn().mockResolvedValue({items:[{id:'item-1',name:'Material fictiv',unit:'buc',minimum_quantity:'2',quantity_total:'5',below_minimum:false,is_active:true}],locations:[{id:'loc-1',name:'Depozit fictiv',is_active:true}],lots:[{id:'lot-1',item_id:'item-1',item:'Material fictiv',code:'LOT-FICTIV',expires_on:null}],balances:[{item_id:'item-1',item:'Material fictiv',unit:'buc',minimum_quantity:'2',item_total:'5',lot_id:'lot-1',lot:'LOT-FICTIV',expires_on:null,location_id:'loc-1',location:'Depozit fictiv',quantity:'5',below_minimum:false,expired:false,expiring_soon:false}]}),
  inventoryHistory:vi.fn().mockResolvedValue([]),addInventoryItem:vi.fn(),addInventoryLocation:vi.fn(),addInventoryLot:vi.fn(),addInventoryMovement:vi.fn(),reverseInventoryMovement:vi.fn(),updateInventoryItem:vi.fn(),updateInventoryLocation:vi.fn(),
}));
vi.mock('../api/client',()=>({apiErrorMessage:(e:unknown)=>e instanceof Error&&e.message==='duplicate'?'Articolul există deja.':'Eroare',getClinic:async()=>({timezone:'Europe/Bucharest'})}));

function textOf(node:TestRenderer.ReactTestInstance):string{return node.children.map(x=>typeof x==='string'?x:textOf(x)).join('');}
async function page(role:string){const client=new QueryClient({defaultOptions:{queries:{retry:false}}});let renderer!:TestRenderer.ReactTestRenderer;await act(async()=>{renderer=TestRenderer.create(<QueryClientProvider client={client}><InventoryPage role={role}/></QueryClientProvider>);});for(let n=0;n<4;n++)await act(async()=>{await new Promise(r=>setTimeout(r,0));});return renderer;}
beforeEach(()=>{vi.clearAllMocks();vi.mocked(inventoryOverview).mockResolvedValue({items:[{id:'item-1',name:'Material fictiv',unit:'buc',minimum_quantity:'2',quantity_total:'5',below_minimum:false,is_active:true}],locations:[{id:'loc-1',name:'Depozit fictiv',is_active:true}],lots:[{id:'lot-1',item_id:'item-1',item:'Material fictiv',code:'LOT-FICTIV',expires_on:null}],balances:[{item_id:'item-1',item:'Material fictiv',unit:'buc',minimum_quantity:'2',item_total:'5',lot_id:'lot-1',lot:'LOT-FICTIV',expires_on:null,location_id:'loc-1',location:'Depozit fictiv',quantity:'5',below_minimum:false,expired:false,expiring_soon:false}]});vi.mocked(inventoryHistory).mockResolvedValue([]);vi.mocked(addInventoryItem).mockReset();vi.mocked(addInventoryLocation).mockReset();vi.mocked(addInventoryMovement).mockReset();vi.mocked(reverseInventoryMovement).mockReset();});
afterEach(()=>vi.unstubAllGlobals());

describe('pagina Stocuri',()=>{
  it('afișează articole fără sold sub prag și formularele managerului',async()=>{
    vi.mocked(inventoryOverview).mockResolvedValue({items:[{id:'item-1',name:'Material fictiv',unit:'buc',minimum_quantity:'2',quantity_total:'0',below_minimum:true,is_active:true}],locations:[{id:'loc-1',name:'Depozit fictiv',is_active:true}],lots:[],balances:[]});
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
    const pending: Array<{resolve:(value:any)=>void;reject:(reason:Error)=>void}> = [];
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
  it('nu repetă mișcarea după un succes rapid și golește cantitatea',async()=>{
    vi.mocked(addInventoryMovement).mockResolvedValue({} as never);
    const renderer=await page('clinic_manager');const forms=renderer.root.findAllByType('form');const movementForm=forms[forms.length-1],selects=movementForm.findAllByType('select');
    await act(async()=>{selects.find(x=>x.parent?.children[0]==='Operație')?.props.onChange({target:{value:'adjustment'}});selects.find(x=>x.parent?.children[0]==='Articol')?.props.onChange({target:{value:'item-1'}});selects.find(x=>x.parent?.children[0]==='Lot')?.props.onChange({target:{value:'lot-1'}});selects.find(x=>x.parent?.children[0]==='Locație')?.props.onChange({target:{value:'loc-1'}});});
    const quantity=movementForm.findAllByType('input').find(x=>x.props.type==='number')!,reason=movementForm.findAllByType('input').find(x=>x.parent?.children[0]==='Motiv')!;
    await act(async()=>{quantity.props.onChange({target:{value:'4'}});reason.props.onChange({target:{value:'Inventar fictiv'}});});
    const form=movementForm,submit=()=>form.props.onSubmit({preventDefault(){}});
    await act(async()=>{submit();await new Promise(r=>setTimeout(r,0));});
    expect(movementForm.findAllByType('input').find(x=>x.props.type==='number')?.props.value).toBe('');expect(movementForm.findAllByType('input').find(x=>x.parent?.children[0]==='Motiv')?.props.value).toBe('');
    await act(async()=>submit());expect(addInventoryMovement).toHaveBeenCalledTimes(1);
    await act(async()=>renderer.unmount());
  });
  it('blochează dublul click la corecție și păstrează cheia după eroare',async()=>{
    vi.stubGlobal('window',{prompt:()=> 'Motiv fictiv'});
    vi.mocked(inventoryHistory).mockResolvedValue([{id:'move-1',item_id:'item-1',item:'Material fictiv',lot_id:'lot-1',lot:'LOT-FICTIV',location_id:'loc-1',location:'Depozit fictiv',author:'Manager fictiv',kind:'receipt',quantity:'1',reason:'',transfer_id:null,reverses_movement_id:null,is_correction:false,is_corrected:false,created_at:'2026-11-21T10:00:00Z'}]);
    const pending:Array<{resolve:(v:any)=>void;reject:(e:Error)=>void}>=[];
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
  it('afișează eroarea la articol duplicat',async()=>{
    vi.mocked(addInventoryItem).mockRejectedValue(new Error('duplicate'));
    const renderer=await page('clinic_manager');const form=renderer.root.findAllByType('form')[0];
    await act(async()=>{form.props.onSubmit({preventDefault(){}});await new Promise(r=>setTimeout(r,0));});
    expect(textOf(renderer.root)).toContain('Articolul există deja.');
    await act(async()=>renderer.unmount());
  });
  it('afișează mesajul de succes potrivit la adăugarea unei locații',async()=>{
    vi.mocked(addInventoryLocation).mockResolvedValue({} as never);
    const renderer=await page('clinic_manager');const form=renderer.root.findAllByType('form')[1];
    await act(async()=>{form.props.onSubmit({preventDefault(){}});await new Promise(r=>setTimeout(r,0));});
    expect(textOf(renderer.root)).toContain('Locația a fost adăugată.');
    await act(async()=>renderer.unmount());
  });
  it('afișează istoricul în română, autorul și filtrele',async()=>{
    vi.mocked(inventoryHistory).mockResolvedValue([
      {id:'move-1',item_id:'item-1',item:'Material fictiv',lot_id:'lot-1',lot:'LOT-FICTIV',location_id:'loc-1',location:'Depozit fictiv',author:'Dr. Ana Fictiv',kind:'receipt',quantity:'2',reason:'',transfer_id:null,reverses_movement_id:null,is_correction:false,is_corrected:false,created_at:'2026-11-21T10:00:00Z'},
      {id:'move-2',item_id:'item-1',item:'Material fictiv',lot_id:'lot-1',lot:'LOT-FICTIV',location_id:'loc-1',location:'Depozit fictiv',author:'Asistent Fictiv',kind:'consumption',quantity:'1',reason:'',transfer_id:null,reverses_movement_id:null,is_correction:false,is_corrected:false,created_at:'2026-11-21T11:00:00Z'},
    ]);
    const renderer=await page('reception');let text=textOf(renderer.root);
    expect(text).toContain('Recepție');expect(text).toContain('Consum');expect(text).toContain('Dr. Ana Fictiv');expect(text).toContain('LOT-FICTIV');
    expect(text).toContain('Filtru istoric articol');expect(text).toContain('Filtru istoric lot');expect(text).toContain('Filtru istoric locație');expect(text).toContain('Filtru istoric tip');
    const type=renderer.root.findAllByType('select').find(x=>x.parent?.children[0]==='Filtru istoric tip')!;
    await act(async()=>type.props.onChange({target:{value:'consumption'}}));text=textOf(renderer.root);
    expect(text).toContain('Consum');expect(renderer.root.findAllByType('article').map(textOf).join(' ')).not.toContain('Recepție');
    await act(async()=>renderer.unmount());
  });
  it('cere istoricul filtrat de la server și separă cache-ul după filtre',async()=>{
    const client=new QueryClient({defaultOptions:{queries:{retry:false}}});let renderer!:TestRenderer.ReactTestRenderer;
    await act(async()=>{renderer=TestRenderer.create(<QueryClientProvider client={client}><InventoryPage role="doctor"/></QueryClientProvider>);});
    for(let n=0;n<4;n++)await act(async()=>{await new Promise(r=>setTimeout(r,0));});
    const selects=renderer.root.findAllByType('select');
    await act(async()=>selects.find(x=>x.parent?.children[0]==='Filtru istoric articol')?.props.onChange({target:{value:'item-1'}}));
    for(let n=0;n<3;n++)await act(async()=>{await new Promise(r=>setTimeout(r,0));});
    expect(vi.mocked(inventoryHistory)).toHaveBeenLastCalledWith({item_id:'item-1',lot_id:'',location_id:'',kind:''});
    expect(client.getQueryCache().findAll().some(x=>JSON.stringify(x.queryKey)===JSON.stringify(['inventory-history','item-1','','','']))).toBe(true);
    await act(async()=>renderer.unmount());
  });
  it('nu afișează cifra zero când istoricul este gol',async()=>{
    const renderer=await page('doctor');expect(textOf(renderer.root)).toContain('Nu există mișcări înregistrate.');expect(textOf(renderer.root)).not.toMatch(/înregistrate\.\s*0/);
    await act(async()=>renderer.unmount());
  });
  it('folosește marcajul de expirare calculat de server chiar la limita zilei',async()=>{
    vi.mocked(inventoryOverview).mockResolvedValue({items:[{id:'item-1',name:'Material fictiv',unit:'buc',minimum_quantity:'2',quantity_total:'0',below_minimum:false,is_active:true}],locations:[{id:'loc-1',name:'Depozit fictiv',is_active:true}],lots:[{id:'lot-1',item_id:'item-1',item:'Material fictiv',code:'LOT-EXPIRAT',expires_on:'2026-11-21',expired:true}],balances:[]});
    const renderer=await page('doctor');const labels=renderer.root.findAllByType('label');
    const itemSelect=renderer.root.findAllByType('select').find(x=>x.parent?.children[0]==='Articol');await act(async()=>itemSelect?.props.onChange({target:{value:'item-1'}}));
    expect(renderer.root.findAllByType('option').map(textOf)).toContain('LOT-EXPIRAT · expiră 2026-11-21 · EXPIRAT');expect(labels.length).toBeGreaterThan(0);
    await act(async()=>renderer.unmount());
  });
  it('nu oferă corectarea pentru înregistrări corectate sau pentru mișcări de corecție',async()=>{
    vi.mocked(inventoryHistory).mockResolvedValue([
      {id:'active',item_id:'item-1',item:'Material fictiv',lot_id:'lot-1',lot:'LOT-FICTIV',location_id:'loc-1',location:'Depozit fictiv',author:'Manager fictiv',kind:'receipt',quantity:'1',reason:'',transfer_id:null,reverses_movement_id:null,is_correction:false,is_corrected:false,created_at:'2026-11-21T10:00:00Z'},
      {id:'done',item_id:'item-1',item:'Material fictiv',lot_id:'lot-1',lot:'LOT-FICTIV',location_id:'loc-1',location:'Depozit fictiv',author:'Manager fictiv',kind:'receipt',quantity:'1',reason:'',transfer_id:null,reverses_movement_id:null,is_correction:false,is_corrected:true,created_at:'2026-11-21T10:00:00Z'},
      {id:'correction',item_id:'item-1',item:'Material fictiv',lot_id:'lot-1',lot:'LOT-FICTIV',location_id:'loc-1',location:'Depozit fictiv',author:'Manager fictiv',kind:'reversal_out',quantity:'1',reason:'Corecție',transfer_id:null,reverses_movement_id:'done',is_correction:true,is_corrected:false,created_at:'2026-11-21T10:00:00Z'},
    ]);
    const renderer=await page('clinic_manager');
    expect(renderer.root.findAllByType('button').filter(x=>textOf(x)==='Corectează')).toHaveLength(1);
    await act(async()=>renderer.unmount());
  });
  it('resetează lotul la schimbarea articolului și ascunde opțiunile inactive, marcând lotul expirat la consum',async()=>{
    vi.mocked(inventoryOverview).mockResolvedValue({items:[{id:'item-1',name:'Material fictiv',unit:'buc',minimum_quantity:'2',quantity_total:'5',below_minimum:false,is_active:true},{id:'item-2',name:'Inactiv',unit:'buc',minimum_quantity:'0',quantity_total:'0',below_minimum:false,is_active:false}],locations:[{id:'loc-1',name:'Depozit fictiv',is_active:true},{id:'loc-2',name:'Depozit inactiv',is_active:false}],lots:[{id:'lot-1',item_id:'item-1',item:'Material fictiv',code:'LOT-EXPIRAT',expires_on:'2020-01-01'},{id:'lot-2',item_id:'item-2',item:'Inactiv',code:'LOT-INACTIV',expires_on:null}],balances:[]});
    const renderer=await page('clinic_manager');const forms=renderer.root.findAllByType('form');const movementForm=forms[forms.length-1];const article=movementForm.findAllByType('select').find(x=>x.parent?.children[0]==='Articol')!,lot=movementForm.findAllByType('select').find(x=>x.parent?.children[0]==='Lot')!;
    await act(async()=>article.props.onChange({target:{value:'item-1'}}));await act(async()=>lot.props.onChange({target:{value:'lot-1'}}));
    await act(async()=>article.props.onChange({target:{value:'item-2'}}));
    expect(movementForm.findAllByType('select').find(x=>x.parent?.children[0]==='Lot')?.props.value).toBe('');expect(movementForm.findAllByType('option').map(textOf)).not.toContain('Inactiv');expect(movementForm.findAllByType('option').map(textOf)).not.toContain('Depozit inactiv');
    await act(async()=>article.props.onChange({target:{value:'item-1'}}));
    expect(renderer.root.findAllByType('option').map(textOf)).toContain('LOT-EXPIRAT · expiră 2020-01-01 · EXPIRAT');
    await act(async()=>renderer.unmount());
  });
});
