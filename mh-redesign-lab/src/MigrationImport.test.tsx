import {beforeEach,describe,expect,it,vi} from 'vitest';
import {fireEvent,render,screen,waitFor} from '@testing-library/react';
import {MigrationImport} from './MigrationImport';
vi.mock('./data-source',()=>({API_BASE_URL:'',apiHeaders:()=>({'Content-Type':'application/json'})}));
const preview={rows:[{line:2,data:{source_id:'p1',nm_id:123,supplier_article:'ART',product_name:'Товар'}}],errors:[],can_confirm:true,preview_fingerprint:'a'.repeat(64)};
const response=(value:unknown)=>({ok:true,json:async()=>value});
describe('MigrationImport',()=>{
 beforeEach(()=>{vi.restoreAllMocks();vi.stubGlobal('fetch',vi.fn());vi.spyOn(window,'confirm').mockReturnValue(true);});
 it('downloads attachment source version into mapping without inventing document evidence or confirming',async()=>{
  render(<MigrationImport role='owner' onRefresh={vi.fn().mockResolvedValue(undefined)}/>);
  fireEvent.change(screen.getByLabelText('Тип импорта'),{target:{value:'historical_attachments'}});
  expect(screen.getByText(/Прикрепление сохраняет исходные строки WB/)).toBeInTheDocument();
  const text='source_id;source_srid;financial_fingerprint;lot_source_id;document_proof\n;WB11;'+ 'a'.repeat(64)+';;';
  vi.mocked(fetch).mockResolvedValueOnce({ok:true,text:async()=>text} as Response);
  URL.createObjectURL=vi.fn(()=> 'blob:synthetic-template');URL.revokeObjectURL=vi.fn();
  vi.spyOn(HTMLAnchorElement.prototype,'click').mockImplementation(()=>undefined);
  fireEvent.click(screen.getByRole('button',{name:'Скачать исходные строки WB для прикрепления'}));
  await waitFor(()=>expect(screen.getByLabelText('CSV: можно вставить текст')).toHaveValue(text));
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(String(vi.mocked(fetch).mock.calls[0][0])).toContain('/historical-attachments/template?start=');
  expect(screen.queryByRole('button',{name:'Подтвердить импорт'})).not.toBeInTheDocument();
  vi.mocked(fetch).mockResolvedValueOnce(response({...preview,can_confirm:false,errors:[{line:2,message:'Заполните документ и исходную партию'}]}) as Response);
  fireEvent.click(screen.getByRole('button',{name:'Проверить без записи'}));
  await screen.findByText('Строка 2: Заполните документ и исходную партию');
  expect(screen.getByRole('button',{name:'Подтвердить импорт'})).toBeDisabled();
 });
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
  fireEvent.click(screen.getByRole('checkbox',{name:/Проверил .* строк/}));
  fireEvent.click(screen.getByRole('button',{name:'Подтвердить импорт'}));
  await screen.findByText('Связь прервана');
  vi.mocked(fetch).mockResolvedValueOnce(response({imported_count:1,replayed:true}) as Response).mockResolvedValueOnce(response({items:[]}) as Response);
  fireEvent.click(screen.getByRole('button',{name:'Подтвердить импорт'}));
  await screen.findByText('Импорт уже выполнен: 1 строк.');
  const calls=vi.mocked(fetch).mock.calls.filter(([url])=>String(url).endsWith('/confirm'));
  expect(calls).toHaveLength(2);expect(calls[0][1]?.body).toEqual(calls[1][1]?.body);
  await waitFor(()=>expect(window.confirm).not.toHaveBeenCalled());
 });
 it('explains an unknown network result and retains the request for safe retry',async()=>{
  await ready();vi.mocked(fetch).mockRejectedValueOnce(new TypeError('Failed to fetch'));
  fireEvent.click(screen.getByRole('checkbox',{name:/Проверил .* строк/}));
  fireEvent.click(screen.getByRole('button',{name:'Подтвердить импорт'}));
  await screen.findByText(/Результат импорта неизвестен/);
  expect(screen.queryByText('Failed to fetch')).not.toBeInTheDocument();
  vi.mocked(fetch).mockResolvedValueOnce(response({imported_count:1,replayed:true}) as Response).mockResolvedValueOnce(response({items:[]}) as Response);
  fireEvent.click(screen.getByRole('button',{name:'Подтвердить импорт'}));
  await screen.findByText('Импорт уже выполнен: 1 строк.');
  const calls=vi.mocked(fetch).mock.calls.filter(call=>String(call[0]).endsWith('/confirm'));
  expect(calls).toHaveLength(2);expect(calls[1][1]?.body).toEqual(calls[0][1]?.body);
 });
 it('requires visible approval and sends only one request during rapid repeated clicks',async()=>{
  await ready();const button=screen.getByRole('button',{name:'Подтвердить импорт'});
  expect(button).toBeDisabled();fireEvent.click(button);expect(fetch).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('checkbox',{name:/Проверил .* строк/}));expect(button).toBeEnabled();
  vi.mocked(fetch).mockReturnValueOnce(new Promise(()=>{}));fireEvent.click(button);fireEvent.click(button);
  expect(vi.mocked(fetch).mock.calls.filter(([url])=>String(url).endsWith('/confirm'))).toHaveLength(1);
  expect(window.confirm).not.toHaveBeenCalled();
 });
 it.each(['source','mapping','kind','template','preview'])('clears approval after %s changes the reviewed input',async(change)=>{
  await ready();fireEvent.click(screen.getByRole('checkbox',{name:/Проверил .* строк/}));
  if(change==='source')fireEvent.change(screen.getByLabelText('CSV: можно вставить текст'),{target:{value:'changed'}});
  if(change==='mapping')fireEvent.change(screen.getByRole('combobox',{name:'Название товара'}),{target:{value:'source_id'}});
  if(change==='kind')fireEvent.change(screen.getByLabelText('Тип импорта'),{target:{value:'materials'}});
  if(change==='template')fireEvent.click(screen.getByRole('button',{name:'Заполнить пример шаблона'}));
  if(change==='preview'){vi.mocked(fetch).mockResolvedValueOnce(response(preview) as Response);fireEvent.click(screen.getByRole('button',{name:'Проверить без записи'}));await waitFor(()=>expect(screen.getByRole('checkbox',{name:/Проверил .* строк/})).not.toBeChecked());}
  else expect(screen.queryByRole('checkbox',{name:/Проверил .* строк/})).not.toBeInTheDocument();
 });
 it('requires a fresh preview and consent after role changes',async()=>{
  const refresh=vi.fn().mockResolvedValue(undefined);const view=render(<MigrationImport role='owner' onRefresh={refresh}/>);
  fireEvent.click(screen.getByRole('button',{name:'Заполнить пример шаблона'}));vi.mocked(fetch).mockResolvedValueOnce(response(preview) as Response);
  fireEvent.click(screen.getByRole('button',{name:'Проверить без записи'}));await screen.findByText('Предпросмотр: 1 строк');
  fireEvent.click(screen.getByRole('checkbox',{name:/Проверил .* строк/}));view.rerender(<MigrationImport role='viewer' onRefresh={refresh}/>);
  view.rerender(<MigrationImport role='owner' onRefresh={refresh}/>);expect(screen.queryByRole('button',{name:'Подтвердить импорт'})).not.toBeInTheDocument();
 });
 it('does not restore stale preview after a role change while loading',async()=>{
  const refresh=vi.fn().mockResolvedValue(undefined);const view=render(<MigrationImport role='owner' onRefresh={refresh}/>);
  fireEvent.click(screen.getByRole('button',{name:'Заполнить пример шаблона'}));let finish!:(value:Response)=>void;
  vi.mocked(fetch).mockReturnValueOnce(new Promise(resolve=>{finish=resolve;}));fireEvent.click(screen.getByRole('button',{name:'Проверить без записи'}));
  view.rerender(<MigrationImport role='viewer' onRefresh={refresh}/>);view.rerender(<MigrationImport role='owner' onRefresh={refresh}/>);finish(response(preview) as Response);
  await waitFor(()=>expect(screen.queryByText('Проверка / сохранение…')).not.toBeInTheDocument());expect(screen.queryByText('Предпросмотр: 1 строк')).not.toBeInTheDocument();
 });
 it.each(['oversize','readfailure'])('invalidates reviewed approval immediately on %s file selection',async(kind)=>{
  await ready();fireEvent.click(screen.getByRole('checkbox',{name:/Проверил .* строк/}));
  const file={size:kind==='oversize'?1_000_001:10,text:vi.fn().mockRejectedValue(new Error('readfailed'))};
  fireEvent.change(screen.getByLabelText('Файл CSV (UTF-8, до 1 МБ)'),{target:{files:[file]}});
  expect(screen.queryByRole('button',{name:'Подтвердить импорт'})).not.toBeInTheDocument();
  await screen.findByText(kind==='oversize'?'Файл больше 1 МБ':'Не удалось прочитать файл CSV');expect(fetch).toHaveBeenCalledTimes(1);
 });
 it('ignores an older file read after the user edits CSV text',async()=>{
  await ready();let finish!:(value:string)=>void;const file={size:10,text:()=>new Promise<string>(resolve=>{finish=resolve;})};
  fireEvent.change(screen.getByLabelText('Файл CSV (UTF-8, до 1 МБ)'),{target:{files:[file]}});
  const text=screen.getByLabelText('CSV: можно вставить текст');fireEvent.change(text,{target:{value:'new reviewed input'}});finish('old file');
  await Promise.resolve();expect(text).toHaveValue('new reviewed input');expect(screen.queryByRole('button',{name:'Подтвердить импорт'})).not.toBeInTheDocument();
 });
 it('finished template separates original and remaining and is manual preview only',async()=>{
  render(<MigrationImport role="owner" onRefresh={vi.fn().mockResolvedValue(undefined)}/>);
  fireEvent.change(screen.getByLabelText('Тип импорта'),{target:{value:'finished_lots'}});
  fireEvent.click(screen.getByRole('button',{name:'Заполнить пример шаблона'}));
  expect((screen.getByLabelText('CSV: можно вставить текст') as HTMLTextAreaElement).value).toContain('original_units;remaining_units');
  expect(screen.getByRole('note')).toHaveTextContent('Источник ручной: отчёт остаётся предварительным');
  expect(fetch).not.toHaveBeenCalled();expect(screen.queryByRole('button',{name:'Подтвердить импорт'})).not.toBeInTheDocument();
 });
 it('history template retains negative returned revenue and positive return logistics with explicit sequence',()=>{
  render(<MigrationImport role="manager" onRefresh={vi.fn().mockResolvedValue(undefined)}/>);
  fireEvent.change(screen.getByLabelText('Тип импорта'),{target:{value:'historical_documents'}});
  fireEvent.click(screen.getByRole('button',{name:'Заполнить пример шаблона'}));
  const text=(screen.getByLabelText('CSV: можно вставить текст') as HTMLTextAreaElement).value;
  expect(text).toContain('operation_date;sequence;event_type');expect(text).toContain('-100;-90;-10;5;0;0;0;0');
  expect(screen.getByRole('note')).toHaveTextContent('одним полным файлом');
 });
 it('shows server conversion provenance and unchanged amount without confirming the import',async()=>{
  render(<MigrationImport role='owner' onRefresh={vi.fn().mockResolvedValue(undefined)}/>);
  fireEvent.change(screen.getByLabelText('Тип импорта'),{target:{value:'opening_materials'}});fireEvent.click(screen.getByRole('button',{name:'Заполнить пример шаблона'}));
  vi.mocked(fetch).mockResolvedValueOnce(response({...preview,rows:[{line:2,data:{source_id:'oil1',material_name:'Масло',unit:'л',quantity:1.5,unit_cost_rub:200,source_date:'2026-09-01',unit_conversion:{entered_quantity:'1500',entered_unit:'мл',entered_unit_price_rub:'0.2',conversion_factor:'0.001',base_quantity:'1.5',base_unit:'л',base_unit_price_rub:'200',source_total_rub:'300'}}}]}) as Response);
  fireEvent.click(screen.getByRole('button',{name:'Проверить без записи'}));
  const proof=await screen.findByRole('region',{name:'Перевод единиц перед переносом'});
  expect(proof).toHaveTextContent('Строка 2: Масло');expect(proof).toHaveTextContent('1500 мл × 0.2 ₽/мл → 1.5 л × 200 ₽/л');expect(proof).toHaveTextContent('Сумма не меняется: 300 ₽');
  expect(screen.getByRole('button',{name:'Подтвердить импорт'})).toBeDisabled();expect(fetch).toHaveBeenCalledTimes(1);
 });
});
