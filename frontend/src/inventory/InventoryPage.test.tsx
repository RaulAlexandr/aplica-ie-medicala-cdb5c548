import TestRenderer, { act } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { InventoryPage } from './InventoryPage';

vi.mock('./api',()=>({
  inventoryOverview:vi.fn().mockResolvedValue({items:[{id:'item-1',name:'Material fictiv',unit:'buc',minimum_quantity:'2',quantity_total:'0',below_minimum:true,is_active:true}],locations:[{id:'loc-1',name:'Depozit fictiv',is_active:true}],balances:[]}),
  inventoryHistory:vi.fn().mockResolvedValue([]),addInventoryItem:vi.fn(),addInventoryLocation:vi.fn(),addInventoryLot:vi.fn(),addInventoryMovement:vi.fn(),reverseInventoryMovement:vi.fn(),updateInventoryItem:vi.fn(),updateInventoryLocation:vi.fn(),
}));
vi.mock('../api/client',()=>({apiErrorMessage:()=> 'Eroare',getClinic:async()=>({timezone:'Europe/Bucharest'})}));

function textOf(node:TestRenderer.ReactTestInstance):string{return node.children.map(x=>typeof x==='string'?x:textOf(x)).join('');}
async function page(role:string){const client=new QueryClient({defaultOptions:{queries:{retry:false}}});let renderer!:TestRenderer.ReactTestRenderer;await act(async()=>{renderer=TestRenderer.create(<QueryClientProvider client={client}><InventoryPage role={role}/></QueryClientProvider>);});for(let n=0;n<4;n++)await act(async()=>{await new Promise(r=>setTimeout(r,0));});return renderer;}
afterEach(()=>vi.clearAllMocks());

describe('pagina Stocuri',()=>{
  it('afișează articole fără sold sub prag și formularele managerului',async()=>{
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
});
