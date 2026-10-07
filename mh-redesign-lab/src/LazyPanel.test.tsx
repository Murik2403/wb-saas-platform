import {lazy} from 'react';
import {act,cleanup,fireEvent,render,screen} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import '@testing-library/jest-dom/vitest';
import {LazyPanel,LazyDisclosure} from './LazyPanel';
afterEach(()=>{cleanup();vi.restoreAllMocks();});

it('shows accessible loading without blocking the shell, then resolves the requested panel',async()=>{
  let resolve!:(module:{default:()=>React.ReactNode})=>void;
  const load=vi.fn(()=>new Promise<{default:()=>React.ReactNode}>(r=>resolve=r));
  const Panel=lazy(load);
  render(<><button>Навигация</button><LazyPanel label="отчёт"><Panel/></LazyPanel></>);
  expect(screen.getByRole('status')).toHaveTextContent('Загружаем отчёт');
  expect(screen.getByRole('button',{name:'Навигация'})).toBeEnabled();
  await act(async()=>resolve({default:()=> <h2>Отчёт готов</h2>}));
  expect(screen.getByRole('heading')).toHaveTextContent('Отчёт готов');
  expect(load).toHaveBeenCalledTimes(1);
});

it('failed chunk leaves unrelated content available and warns before explicit page refresh',async()=>{
  vi.spyOn(console,'error').mockImplementation(()=>{});
  const Panel=lazy(async()=>{throw new Error('Synthetic chunk failure');});
  render(<><button>Настройки</button><LazyPanel label="аналитику"><Panel/></LazyPanel></>);
  expect(await screen.findByRole('alert')).toHaveTextContent('Перед обновлением страницы сохраните');
  expect(screen.getByRole('button',{name:'Настройки'})).toBeEnabled();
  expect(screen.getByRole('button',{name:'Обновить страницу'})).toBeEnabled();
});

it('closed disclosure never runs its loader; visited form survives closing and reopening',async()=>{
  const load=vi.fn(async()=>({default:()=> <input aria-label="Черновик" defaultValue=""/>}));
  const Panel=lazy(load);
  render(<LazyDisclosure title="Документы"><Panel/></LazyDisclosure>);
  expect(load).not.toHaveBeenCalled();
  const details=screen.getByText('Документы').closest('details')!;
  details.open=true;fireEvent(details,new Event('toggle'));
  const input=await screen.findByLabelText('Черновик');
  fireEvent.change(input,{target:{value:'Не терять'}});
  details.open=false;fireEvent(details,new Event('toggle'));
  details.open=true;fireEvent(details,new Event('toggle'));
  expect(screen.getByLabelText('Черновик')).toHaveValue('Не терять');
  expect(load).toHaveBeenCalledTimes(1);
});

it('unmounted pending panel never restores stale content when its import resolves',async()=>{
  let resolve!:(module:{default:()=>React.ReactNode})=>void;
  const Panel=lazy(()=>new Promise<{default:()=>React.ReactNode}>(r=>resolve=r));
  const view=render(<LazyPanel><Panel/></LazyPanel>);
  view.rerender(<p>WB выбран</p>);
  await act(async()=>resolve({default:()=> <p>Старый Ozon</p>}));
  expect(screen.queryByText('Старый Ozon')).toBeNull();
  expect(screen.getByText('WB выбран')).toBeVisible();
});
