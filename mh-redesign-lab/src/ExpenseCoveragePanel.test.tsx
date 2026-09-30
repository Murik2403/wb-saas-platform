import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ExpenseCoveragePanel } from "./ExpenseCoveragePanel";
import { getExpenseCoverage, saveExpenseCoverage, type ExpenseCoverage } from "./data-source";
vi.mock("./data-source", () => ({ getExpenseCoverage: vi.fn(), saveExpenseCoverage: vi.fn() }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
const products = [{ nm_id:123, supplier_article:"one", product_name:"Товар",cost_per_wb_unit:12,note:"",updated_at:"" }];
const coverage: ExpenseCoverage = { nm_id:123, start:"2026-09-01",end:"2026-09-30",can_write:true,count:0,total:9,preliminary:true,items:[{article:"taxes",state:"unknown",stale:true,source_ref:"",fingerprint:"a".repeat(64)}],history:[] };
async function load(canWrite=true) {
  vi.mocked(getExpenseCoverage).mockResolvedValue({...coverage,can_write:canWrite});
  render(<ExpenseCoveragePanel products={products} />);
  fireEvent.change(screen.getByLabelText("Товар для проверки"),{target:{value:"123"}});
  fireEvent.change(screen.getByLabelText("Начало проверки"),{target:{value:"2026-09-01"}});
  fireEvent.change(screen.getByLabelText("Конец проверки"),{target:{value:"2026-09-30"}});
  fireEvent.click(screen.getByRole("button",{name:"Показать покрытие расходов"}));
  await screen.findByText(/0 из 9 статей/);
}
it("never grants write UI to read-only roles",async()=>{
  await load(false);
  expect(screen.queryByRole("button",{name:"Сохранить состояние расходов"})).not.toBeInTheDocument();
  expect(screen.getByText(/исходные данные изменились/)).toBeInTheDocument();
});
it("binds confirmation to loaded scope and refuses changed scope",async()=>{
  await load();
  fireEvent.change(screen.getByLabelText("Конец проверки"),{target:{value:"2026-09-29"}});
  expect(screen.queryByRole("button",{name:"Сохранить состояние расходов"})).not.toBeInTheDocument();
  expect(saveExpenseCoverage).not.toHaveBeenCalled();
});
it("reuses exact retry key but rotates after edited confirmation",async()=>{
  vi.mocked(saveExpenseCoverage).mockRejectedValue(new Error("Lost response"));
  await load();
  fireEvent.change(screen.getByLabelText("Источник или документ"),{target:{value:"document-42"}});
  fireEvent.change(screen.getByLabelText("Причина подтверждения"),{target:{value:"checked"}});
  const save=screen.getByRole("button",{name:"Сохранить состояние расходов"});
  fireEvent.click(save); await screen.findByText("Lost response");
  fireEvent.click(save); await waitFor(()=>expect(saveExpenseCoverage).toHaveBeenCalledTimes(2));
  await waitFor(()=>expect(save).not.toBeDisabled());
  const [first, second]=vi.mocked(saveExpenseCoverage).mock.calls;
  expect(first[0].request_key).toBe(second[0].request_key);
  expect(first[0].expected_fingerprint).toBe("a".repeat(64));
  fireEvent.change(screen.getByLabelText("Причина подтверждения"),{target:{value:"edited"}});
  fireEvent.click(save); await waitFor(()=>expect(saveExpenseCoverage).toHaveBeenCalledTimes(3));
  expect(vi.mocked(saveExpenseCoverage).mock.calls[2][0].request_key).not.toBe(first[0].request_key);
});
