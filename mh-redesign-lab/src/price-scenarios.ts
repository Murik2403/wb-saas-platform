export type PriceScenarioInput = {
  orders: number; oldUnits: number; oldCost: number; newUnits: number; newCost: number;
  price: number; returnPercent: number; lostReturnPercent: number;
  commissionPercent: number; advertisingPercent: number; taxPercent: number;
  delivery: number; returnDelivery: number; fixedExpenses: number; targetMarginPercent: number;
};
export function priceScenario(input: PriceScenarioInput) {
  if (Object.values(input).some(value => !Number.isFinite(value) || value < 0 || value > 1e9)) throw new Error("Проверьте числовые значения: отрицательные и бесконечные суммы недопустимы.");
  if (![input.orders, input.oldUnits, input.newUnits].every(Number.isInteger) || input.orders < 1) throw new Error("Количество заказов и запас укажите целыми числами; план должен быть положительным.");
  for (const rate of [input.returnPercent,input.lostReturnPercent,input.commissionPercent,input.advertisingPercent,input.taxPercent,input.targetMarginPercent]) if (rate > 100) throw new Error("Процент не может быть больше 100.");
  if (input.orders > input.oldUnits + input.newUnits) throw new Error("Запаса двух партий недостаточно для плана. Увеличьте поступление или уменьшите план.");
  const oldUsed = Math.min(input.orders, input.oldUnits);
  const newUsed = input.orders-oldUsed;
  const cost = (oldUsed*input.oldCost+newUsed*input.newCost)/input.orders;
  const kept = 1-input.returnPercent/100;
  const fees = (input.commissionPercent+input.advertisingPercent+input.taxPercent)/100;
  if (kept <= 0 || fees >= 1) throw new Error("При этих возвратах и ставках положительная выручка не покрывает затраты.");
  const expense = cost*(kept+(1-kept)*input.lostReturnPercent/100)+input.delivery+(1-kept)*input.returnDelivery+input.fixedExpenses/input.orders;
  const revenue = kept*input.price;
  const profit = revenue*(1-fees)-expense;
  const targetDenominator = kept*(1-fees-input.targetMarginPercent/100);
  return {oldUsed,newUsed,unitCost:cost,newPurchaseRub:input.newUnits*input.newCost,
    breakEvenPrice:expense/(kept*(1-fees)),targetPrice:targetDenominator>0 ? expense/targetDenominator : null,
    profitPerOrder:profit,profitTotal:profit*input.orders,marginPercent:revenue>0?profit/revenue*100:null,
    expectedRetainedUnits:kept*input.orders,expectedLostReturns:input.orders*(1-kept)*input.lostReturnPercent/100};
}
