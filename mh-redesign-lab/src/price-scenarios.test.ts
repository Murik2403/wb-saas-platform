import { describe, expect, it } from "vitest";
import { priceScenario, type PriceScenarioInput } from "./price-scenarios";
const base: PriceScenarioInput = {orders:10,oldUnits:5,oldCost:100,newUnits:10,newCost:200,price:300,returnPercent:0,lostReturnPercent:0,commissionPercent:10,advertisingPercent:0,taxPercent:0,delivery:20,returnDelivery:10,fixedExpenses:100,targetMarginPercent:20};
describe("Price scenarios",()=>{
  it("crosses FIFO boundary and separates purchase cash from period cost",()=>{const x=priceScenario(base);expect(x.unitCost).toBe(150);expect(x.oldUsed).toBe(5);expect(x.newUsed).toBe(5);expect(x.newPurchaseRub).toBe(2000);expect(x.profitTotal).toBe(900);expect(x.breakEvenPrice).toBeCloseTo(200);expect(x.targetPrice).toBeCloseTo(180/.7);});
  it("recovers cost of returned goods but retains two-way logistics",()=>{const x=priceScenario({...base,returnPercent:50});expect(x.expectedRetainedUnits).toBe(5);expect(x.expectedLostReturns).toBe(0);expect(x.profitPerOrder).toBeCloseTo(25);expect(x.breakEvenPrice).toBeCloseTo(110/.45);});
  it("charges explicitly lost returns",()=>{const x=priceScenario({...base,returnPercent:50,lostReturnPercent:100});expect(x.expectedLostReturns).toBe(5);expect(x.profitPerOrder).toBeCloseTo(-50);});
  it("does not invent feasible target when deductions plus margin exceed revenue",()=>expect(priceScenario({...base,targetMarginPercent:95}).targetPrice).toBeNull());
  it.each([{orders:16},{orders:1.5},{price:NaN},{newCost:-1},{returnPercent:100},{commissionPercent:100},{taxPercent:101}])("rejects invalid plan %j",change=>expect(()=>priceScenario({...base,...change})).toThrow());
});
