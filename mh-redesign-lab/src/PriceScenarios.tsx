import { useState } from "react";
import { priceScenario, type PriceScenarioInput } from "./price-scenarios";
const fields: [keyof PriceScenarioInput,string,boolean][] = [
  ["orders","Заказов в плане",true],["oldUnits","Остаток старой партии, ед.",true],["oldCost","Стоимость старой партии за единицу, ₽",false],
  ["newUnits","Новая закупка, ед.",true],["newCost","Стоимость новой партии за единицу, ₽",false],["price","Цена покупателю, ₽",false],
  ["returnPercent","Возвраты, % заказов",false],["lostReturnPercent","Невосстановимые возвраты, % возвратов",false],
  ["commissionPercent","Комиссия, % сохранённой выручки",false],["advertisingPercent","Реклама, % сохранённой выручки",false],["taxPercent","Налог по вашему предположению, %",false],
  ["delivery","Доставка одного заказа, ₽",false],["returnDelivery","Обратная доставка возврата, ₽",false],
  ["fixedExpenses","Прочие фиксированные расходы периода, ₽",false],["targetMarginPercent","Целевая маржа, % сохранённой выручки",false]
];
const rub=(v:number)=>v.toLocaleString("ru-RU",{style:"currency",currency:"RUB",maximumFractionDigits:2});
export function PriceScenarios(){
  const [result,setResult]=useState<ReturnType<typeof priceScenario>|null>(null);
  const [error,setError]=useState("");
  return <section className="work-section" aria-label="Сценарии цены и закупки"><h2>Сценарии цены и закупки</h2><p>Сравните старую и новую партии, возвраты и минимальную цену. Это модель по вашим предположениям; она не меняет учёт и цены WB.</p>
    <form onChange={()=>setResult(null)} onSubmit={event=>{event.preventDefault();setError("");const data=new FormData(event.currentTarget);try{const input=Object.fromEntries(fields.map(([key])=>[key,Number(data.get(key))])) as PriceScenarioInput;setResult(priceScenario(input));}catch(cause){setResult(null);setError(cause instanceof Error?cause.message:"Проверьте ввод");}}}>
      <div className="pw-capacity-fields">{fields.map(([key,label,integer])=><label key={key}>{label}<input name={key} type="number" required min="0" step={integer?"1":"any"} max={key.includes("Percent")?100:1e9} defaultValue={key.includes("Percent")||key==="fixedExpenses"||key==="returnDelivery"?0:undefined}/></label>)}</div>
      <button type="submit" className="secondary-button">Сравнить сценарий</button>
    </form>{error&&<p role="alert">{error}</p>}
    {result&&<div role="status"><p>Средняя стоимость расходуемых партий: <strong>{rub(result.unitCost)}</strong>; старой: {result.oldUsed} ед., новой: {result.newUsed} ед.</p>
      <p>Безубыточная цена: <strong>{rub(result.breakEvenPrice)}</strong>. Цена для целевой маржи: <strong>{result.targetPrice===null?"Недостижима при указанных ставках":rub(result.targetPrice)}</strong>.</p>
      <p>Результат плана: <strong>{rub(result.profitTotal)}</strong>; маржа: {result.marginPercent===null?"нет выручки":`${result.marginPercent.toFixed(2)}%`}.</p>
      <p>Деньги на всю новую закупку: <strong>{rub(result.newPurchaseRub)}</strong>. Закупка не вычитается повторно из прибыли: в результат входит стоимость расходуемых единиц.</p>
      <p>Модель не предполагает повторную продажу возвратов в этом периоде. Вернувшийся пригодный товар остаётся запасом; потери учитываются указанной долей. Налоговая ставка и база заданы вами вручную.</p>
    </div>}
  </section>;
}
