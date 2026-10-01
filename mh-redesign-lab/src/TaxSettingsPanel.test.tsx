import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { TaxSettingsPanel, buildRates } from "./TaxSettingsPanel";
import { getTaxSettings, saveTaxSettings } from "./data-source";

vi.mock("./data-source", () => ({ getTaxSettings: vi.fn(), saveTaxSettings: vi.fn() }));
const load = vi.mocked(getTaxSettings);
const save = vi.mocked(saveTaxSettings);
const empty = { enabled: true, year: new Date().getFullYear(), regime: null, cost_official: false, rates: [], supported_regimes: ["usn_income" as const, "usn_income_expense" as const] };

describe("buildRates", () => {
  const cells = Array.from({ length: 12 }, () => ({ usn: "", vat: "" }));
  it("skips empty months and parses comma decimals", () => {
    const next = cells.map((c, i) => (i === 0 ? { usn: "1,5", vat: "20" } : c));
    expect(buildRates(next)).toEqual({ rates: [{ month: 1, usn_pct: 1.5, vat_pct: 20 }] });
  });
  it("rejects half-filled and out-of-range months", () => {
    expect(buildRates(cells.map((c, i) => (i === 1 ? { usn: "6", vat: "" } : c)))).toEqual({ error: expect.stringContaining("февраль") });
    expect(buildRates(cells.map((c, i) => (i === 2 ? { usn: "101", vat: "0" } : c)))).toEqual({ error: expect.stringContaining("март") });
  });
});

describe("TaxSettingsPanel", () => {
  it("hides the entire panel when accountant review is disabled", async () => {
    load.mockResolvedValue({ ...empty, enabled: false });
    const { container } = render(<TaxSettingsPanel />);
    await waitFor(() => expect(load).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
    expect(save).not.toHaveBeenCalled();
  });
  beforeEach(() => { load.mockReset(); save.mockReset(); load.mockResolvedValue(empty); });

  it("disables unsupported regimes and explains why", async () => {
    render(<TaxSettingsPanel />);
    await waitFor(() => expect(screen.getByLabelText(/УСН «Доходы»$/)).toBeEnabled());
    expect(screen.getByLabelText(/ИП на ОСНО/)).toBeDisabled();
    expect(screen.getByLabelText(/ООО на ОСНО/)).toBeDisabled();
    expect(screen.getByLabelText(/с НДС 22%/)).toBeDisabled();
    expect(screen.getAllByText(/пока не считается/).length).toBe(3);
  });

  it("fills a quarter's months, lets months differ, and saves the rates", async () => {
    save.mockResolvedValue({ ...empty, regime: "usn_income" });
    render(<TaxSettingsPanel />);
    await waitFor(() => expect(screen.getByLabelText(/УСН «Доходы»$/)).toBeEnabled());
    fireEvent.click(screen.getByLabelText(/УСН «Доходы»$/));
    fireEvent.change(screen.getByLabelText("УСН, % — I квартал"), { target: { value: "6" } });
    fireEvent.change(screen.getByLabelText("НДС, % — I квартал"), { target: { value: "20" } });
    expect(screen.getByLabelText("УСН, % — март")).toHaveValue(6);
    fireEvent.change(screen.getByLabelText("УСН, % — март"), { target: { value: "5" } });
    expect(screen.getByLabelText("УСН, % — I квартал")).toHaveValue(null);
    fireEvent.click(screen.getByRole("button", { name: "Сохранить ставки" }));
    await waitFor(() => expect(save).toHaveBeenCalled());
    const sent = save.mock.calls[0][0];
    expect(sent.regime).toBe("usn_income");
    expect(sent.rates.map((r) => [r.month, r.usn_pct, r.vat_pct])).toEqual([[1, 6, 20], [2, 6, 20], [3, 5, 20]]);
    expect(await screen.findByRole("status")).toHaveTextContent("Сохранено");
  });

  it("shows the cost checkbox only for Д−Р and reports save errors", async () => {
    save.mockRejectedValue(new Error("Режим налогообложения пока не поддерживается"));
    render(<TaxSettingsPanel />);
    await waitFor(() => expect(screen.getByLabelText(/УСН «Доходы»$/)).toBeEnabled());
    expect(screen.queryByLabelText(/себестоимость товара/)).toBeNull();
    fireEvent.click(screen.getByLabelText(/Доходы−Расходы», НДС по ставке/));
    fireEvent.click(screen.getByLabelText(/себестоимость товара/));
    fireEvent.click(screen.getByRole("button", { name: "Сохранить ставки" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("пока не поддерживается");
    expect(save.mock.calls[0][0].cost_official).toBe(true);
  });

  it("does not call the API without a regime", async () => {
    render(<TaxSettingsPanel />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Сохранить ставки" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Сохранить ставки" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Выберите режим");
    expect(save).not.toHaveBeenCalled();
  });
});
