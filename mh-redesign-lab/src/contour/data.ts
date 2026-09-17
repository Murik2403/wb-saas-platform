export type Sale = { date: string; current: number; previous: number };
export type Expense = { label: string; value: number; color: string };
export type Stock = { name: string; wb: number; internal: number };
export const sales: Sale[] = [
  { date: '01.09', current: 80, previous: 60 }, { date: '02.09', current: 110, previous: 90 },
  { date: '03.09', current: 100, previous: 95 }, { date: '04.09', current: 140, previous: 110 },
  { date: '05.09', current: 170, previous: 130 }, { date: '06.09', current: 150, previous: 140 },
  { date: '07.09', current: 200, previous: 160 },
];
export const expenses: Expense[] = [
  { label: 'Себестоимость', value: 520, color: 'ink' },
  { label: 'Комиссия', value: 298, color: 'accent' },
  { label: 'Логистика и упаковка', value: 90, color: 'sand' },
  { label: 'Реклама', value: 100, color: 'olive' },
];
export const stocks: Stock[] = [{ name: 'Товар A', wb: 340, internal: 120 }, { name: 'Товар B', wb: 220, internal: 80 }, { name: 'Товар C', wb: 120, internal: 60 }];
export const number = (value: number) => new Intl.NumberFormat('ru-RU').format(value);
