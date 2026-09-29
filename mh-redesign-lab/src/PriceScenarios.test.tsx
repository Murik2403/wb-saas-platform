import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PriceScenarios } from "./PriceScenarios";
describe("PriceScenarios",()=>{
  it("shows a user scenario and clears stale result when inputs change",()=>{
    const {container}=render(<PriceScenarios/>);
    const values={orders:10,oldUnits:5,oldCost:100,newUnits:10,newCost:200,price:300,returnPercent:0,lostReturnPercent:0,commissionPercent:10,advertisingPercent:0,taxPercent:0,delivery:20,returnDelivery:10,fixedExpenses:100,targetMarginPercent:20};
    for(const [name,value] of Object.entries(values)) fireEvent.change(container.querySelector(`input[name="${name}"]`)!,{target:{value}});
    fireEvent.click(screen.getByRole("button",{name:"Сравнить сценарий"}));
    expect(screen.getByRole("status").textContent).toContain("Безубыточная цена");
    expect(screen.getByRole("status").textContent).toContain("900");
    fireEvent.change(container.querySelector('input[name="price"]')!,{target:{value:400}});
    expect(screen.queryByRole("status")).toBeNull();
  });
});
