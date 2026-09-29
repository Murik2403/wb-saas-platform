import {beforeEach,describe,expect,it,vi} from 'vitest';
import {fireEvent,render,screen,waitFor} from '@testing-library/react';
import {MigrationImport} from './MigrationImport';
vi.mock('./data-source',()=>({API_BASE_URL:'',apiHeaders:()=>({'Content-Type':'application/json'})}));
const preview={rows:[{line:2,data:{source_id:'p1',nm_id:123,supplier_article:'ART',product_name:'Товар'}}],errors:[],can_confirm:true,preview_fingerprint:'a'.repeat(64)};
const response=(value:unknown)=>({ok:true,json:async()=>value});
describe('MigrationImport',()=>{
 beforeEach(()=>{vi.restoreAllMocks();vi.stubGlobal('fetch',vi.fn());vi.spyOn(window,'confirm').mockReturnValue(true);});
 async function ready(role='owner'){
  render(<MigrationImport role={role} onRefresh={vi.fn().mockResolvedValue(undefined)}/>);
  fireEvent.click(screen.getByRole('button',{name:'Заполнить пример шаблона'}));
  vi.mocked(fetch).mockResolvedValueOnce(response(preview) as Response);
  fireEvent.click(screen.getByRole('button',{name:'Проверить без записи'}));
  await screen.findByText('Предпросмотр: 1 строк');
 }
 it('preview never confirms automatically and read-only hides confirmation',async()=>{
  await ready('viewer');expect(fetch).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole('button',{name:'Подтвердить импорт'})).not.toBeInTheDocument();
  expect(screen.getByText('Подтверждать перенос может владелец или менеджер.')).toBeInTheDocument();
 });
 it('blocks errors and invalidates preview when source changes',async()=>{
  render(<MigrationImport role='owner' onRefresh={vi.fn().mockResolvedValue(undefined)}/>);
  fireEvent.click(screen.getByRole('button',{name:'Заполнить пример шаблона'}));
  vi.mocked(fetch).mockResolvedValueOnce(response({...preview,errors:[{line:2,message:'Уже существует'}],can_confirm:false}) as Response);
  fireEvent.click(screen.getByRole('button',{name:'Проверить без записи'}));
  expect(await screen.findByRole('button',{name:'Подтвердить импорт'})).toBeDisabled();
  fireEvent.change(screen.getByLabelText('CSV: можно вставить текст'),{target:{value:'changed'}});
  expect(screen.queryByText('Предпросмотр: 1 строк')).not.toBeInTheDocument();
 });
 it('retries identical request after uncertain failure',async()=>{
  await ready();vi.mocked(fetch).mockRejectedValueOnce(new Error('Связь прервана'));
  fireEvent.click(screen.getByRole('button',{name:'Подтвердить импорт'}));
  await screen.findByText('Связь прервана');
  vi.mocked(fetch).mockResolvedValueOnce(response({imported_count:1,replayed:true}) as Response).mockResolvedValueOnce(response({items:[]}) as Response);
  fireEvent.click(screen.getByRole('button',{name:'Подтвердить импорт'}));
  await screen.findByText('Импорт уже выполнен: 1 строк.');
  const calls=vi.mocked(fetch).mock.calls.filter(([url])=>String(url).endsWith('/confirm'));
  expect(calls).toHaveLength(2);expect(calls[0][1]?.body).toEqual(calls[1][1]?.body);
  await waitFor(()=>expect(window.confirm).toHaveBeenCalledTimes(1));
 });
});
