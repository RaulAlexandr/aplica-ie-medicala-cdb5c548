import TestRenderer,{act} from 'react-test-renderer';import {describe,expect,it,vi} from 'vitest';import * as api from './api';import {WaitlistPanel} from './Waitlist';
import * as client from '../api/client';
function text(n:TestRenderer.ReactTestInstance):string{return n.children.map(c=>typeof c==='string'?c:typeof c==='number'?String(c):text(c)).join('')}
describe('T16 waitlist screen',()=>{it('shows waitlist, slot candidates workflow and recalls',async()=>{vi.spyOn(client,'getClinic').mockResolvedValue({timezone:'Europe/Bucharest'} as never);vi.spyOn(api,'entries').mockResolvedValue([]);vi.spyOn(api,'recalls').mockResolvedValue([{id:'r1',due_date:'2026-11-20',reason:'Fictiv',status:'pending'}] as never);let r!:TestRenderer.ReactTestRenderer;await act(async()=>{r=TestRenderer.create(<WaitlistPanel/>)});expect(text(r.root)).toContain('Lista de așteptare');expect(r.root.findAllByType('h3').map(x=>x.children.join(''))).toContain('Rechemări scadente');await act(async()=>r.unmount())})});

it('runs slot → candidates → offer → confirmation and renders due recalls',async()=>{
 vi.spyOn(client,'getClinic').mockResolvedValue({timezone:'Europe/Bucharest'} as never);
 const candidate={id:'entry-1',patient_id:'patient-1',desired_service:'Serviciu fictiv',desired_duration_minutes:30,priority:3,status:'active'};
 vi.spyOn(api,'entries').mockResolvedValue([candidate] as never);
 vi.spyOn(api,'recalls').mockResolvedValue([{id:'recall-1',due_date:'2026-11-20',reason:'Motiv fictiv',status:'pending'}] as never);
 const find=vi.spyOn(api,'candidates').mockResolvedValue([candidate] as never);
 const makeOffer=vi.spyOn(api,'offer').mockResolvedValue({id:'offer-1'} as never);
 const confirm=vi.spyOn(api,'confirm').mockResolvedValue({id:'appointment-1'} as never);
 let r!:TestRenderer.ReactTestRenderer;await act(async()=>{r=TestRenderer.create(<WaitlistPanel/>)});
 const fill=(label:string,value:string)=>{const item=r.root.findAllByType('label').find(x=>text(x).startsWith(label));if(!item)throw new Error(`Missing ${label}`);const input=item.findByType('input');act(()=>input.props.onChange({target:{value}}))};
 fill('Medic ID','doctor-1');fill('Cabinet ID','room-1');fill('Început','2026-11-30T10:00');
 const searchForm=r.root.findAllByType('form').find(x=>x.props['aria-label']==='Interval liber')!;
 await act(async()=>searchForm.props.onSubmit({preventDefault(){}}));
 expect(find).toHaveBeenCalledWith(expect.objectContaining({doctor_id:'doctor-1',room_id:'room-1',starts_at:expect.any(String)}));
 const offerButton=r.root.findAllByType('button').find(b=>text(b)==='Înregistrează oferta telefonică')!;
 await act(async()=>offerButton.props.onClick());expect(makeOffer).toHaveBeenCalledWith('entry-1',expect.objectContaining({starts_at:'2026-11-30T08:00:00.000Z'}));
 const confirmButton=r.root.findAllByType('button').find(b=>text(b)==='Confirmă și creează programarea')!;
 await act(async()=>confirmButton.props.onClick());expect(confirm).toHaveBeenCalledWith('offer-1');
 expect(text(r.root)).toContain('Motiv fictiv');await act(async()=>r.unmount());
});

it('keeps the clinic-local datetime input value and safely converts it on submit',async()=>{
 vi.spyOn(client,'getClinic').mockResolvedValue({timezone:'Europe/Bucharest'} as never);
 vi.spyOn(api,'entries').mockResolvedValue([]);vi.spyOn(api,'recalls').mockResolvedValue([] as never);
 const find=vi.spyOn(api,'candidates').mockResolvedValue([] as never);
 let r!:TestRenderer.ReactTestRenderer;await act(async()=>{r=TestRenderer.create(<WaitlistPanel/>)});
 const input=r.root.findAllByType('label').find(x=>text(x).startsWith('Început'))!.findByType('input');
 act(()=>input.props.onChange({target:{value:'2026-11-23T10:00'}}));
 expect(r.root.findAllByType('label').find(x=>text(x).startsWith('Început'))!.findByType('input').props.value).toBe('2026-11-23T10:00');
 act(()=>input.props.onChange({target:{value:'2026-11-23T'}}));
 expect(r.root.findAllByType('label').find(x=>text(x).startsWith('Început'))!.findByType('input').props.value).toBe('2026-11-23T');
 act(()=>input.props.onChange({target:{value:''}}));
 expect(r.root.findAllByType('label').find(x=>text(x).startsWith('Început'))!.findByType('input').props.value).toBe('');
 const invalidForm=r.root.findAllByType('form').find(x=>x.props['aria-label']==='Interval liber')!;
 await act(async()=>invalidForm.props.onSubmit({preventDefault(){}}));
 expect(find).not.toHaveBeenCalled();expect(text(r.root)).toContain('Enter a valid clinic-local date and time.');
 const fill=(label:string,value:string)=>{const item=r.root.findAllByType('label').find(x=>text(x).startsWith(label))!;act(()=>item.findByType('input').props.onChange({target:{value}}))};
 fill('Medic ID','doctor-1');fill('Cabinet ID','room-1');fill('Început','2026-11-23T10:00');
 const form=r.root.findAllByType('form').find(x=>x.props['aria-label']==='Interval liber')!;
 await act(async()=>form.props.onSubmit({preventDefault(){}}));
 expect(find).toHaveBeenCalledWith(expect.objectContaining({starts_at:'2026-11-23T08:00:00.000Z'}));
 await act(async()=>r.unmount());
});
