import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ManualStoreExpenseManager } from "./ManualStoreExpenseManager";
import {
  createManualStoreExpense,
  createRecurringStoreExpenseTemplate,
  generateRecurringStoreExpenses,
  getManualStoreExpenses,
  getRecurringStoreExpenseTemplates,
  previewStoreExpenseImport,
  confirmStoreExpenseImport,
  setRecurringStoreExpenseTemplateActive,
  updateManualStoreExpenseInclusion,
} from "./data-source";

vi.mock("./data-source", () => ({
  createManualStoreExpense: vi.fn(),
  createRecurringStoreExpenseTemplate: vi.fn(),
  deleteManualStoreExpense: vi.fn(),
  generateRecurringStoreExpenses: vi.fn(),
  getManualStoreExpenses: vi.fn(),
  getRecurringStoreExpenseTemplates: vi.fn(),
  setRecurringStoreExpenseTemplateActive: vi.fn(),
  previewStoreExpenseImport: vi.fn(),
  confirmStoreExpenseImport: vi.fn(),
  updateManualStoreExpenseInclusion: vi.fn(),
}));

afterEach(() => { cleanup(); vi.clearAllMocks(); });

it("creates a whole-store expense as excluded by default", async () => {
  vi.mocked(getManualStoreExpenses).mockResolvedValue({ items: [] });
  vi.mocked(createManualStoreExpense).mockResolvedValue({ id: 42, message: "saved" });
  render(<ManualStoreExpenseManager />);

  const checkbox = await screen.findByLabelText(/Проверил, что это внешний расход/);
  expect(checkbox).not.toBeChecked();
  expect(screen.getByText(/Проверил, что это внешний расход и он не задвоен с данными WB, рекламой и себестоимостью/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Сумма за период, ₽"), { target: { value: "1500" } });
  fireEvent.change(screen.getByLabelText("Период от"), { target: { value: "2026-09-01" } });
  fireEvent.change(screen.getByLabelText("Период до"), { target: { value: "2026-09-30" } });
  fireEvent.change(screen.getByLabelText("Примечание / источник"), { target: { value: "Аренда цеха" } });
  fireEvent.submit(screen.getByRole("form", { name: "Добавить общий расход магазина" }));

  await waitFor(() => expect(createManualStoreExpense).toHaveBeenCalledWith({
    article: "taxes", amount_rub: 1500, source: "manual", period_start: "2026-09-01",
    period_end: "2026-09-30", note: "Аренда цеха", include_in_management_result: false,
  }));
  expect(await screen.findByRole("status")).toHaveTextContent("Общий расход сохранён");
});

it("requires a deliberate confirmation before including a whole-store expense", async () => {
  const row = { id: 9, article: "storage", amount_rub: 500, source: "manual" as const, period_start: "2026-09-01", period_end: "2026-09-30", note: "Склад", include_in_management_result: false, created_at: "", updated_at: "" };
  vi.mocked(getManualStoreExpenses)
    .mockResolvedValueOnce({ items: [row] })
    .mockResolvedValueOnce({ items: [{ ...row, include_in_management_result: true }] })
    .mockResolvedValue({ items: [row] });
  vi.mocked(updateManualStoreExpenseInclusion).mockResolvedValue({ id: 9, include_in_management_result: true });
  render(<ManualStoreExpenseManager />);

  expect(await screen.findByRole("button", { name: "Подтвердить и включить" })).toBeInTheDocument();
  fireEvent.click(await screen.findByRole("button", { name: "Подтвердить и включить" }));
  await waitFor(() => expect(updateManualStoreExpenseInclusion).toHaveBeenCalledWith(9, true));
  expect(await screen.findByText(/включён после подтверждения/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Исключить" }));
  await waitFor(() => expect(updateManualStoreExpenseInclusion).toHaveBeenLastCalledWith(9, false));
  expect(await screen.findByText(/исключён до подтверждения/)).toBeInTheDocument();
});

it("previews invalid and duplicate CSV rows and prevents confirmation without valid rows", async () => {
  vi.mocked(getManualStoreExpenses).mockResolvedValue({ items: [] });
  vi.mocked(getRecurringStoreExpenseTemplates).mockResolvedValue({ items: [] });
  vi.mocked(previewStoreExpenseImport).mockResolvedValue({
    batch_id: "17",
    already_imported: false,
    rows: [
      { line_number: 2, article: "storage", amount_rub: 500, period_start: "2026-09-01", period_end: "2026-09-30", note: "Склад", errors: [], duplicate: true },
      { line_number: 3, article: "", amount_rub: null, period_start: "", period_end: "", note: "", errors: ["Не указана статья"], duplicate: false },
    ],
    valid_count: 0,
    invalid_count: 1,
    duplicate_count: 1,
  });
  render(<ManualStoreExpenseManager />);

  const file = new File(["article,amount_rub,period_start,period_end,note"], "expenses.csv", { type: "text/csv" });
  fireEvent.change(await screen.findByLabelText("CSV-файл расходов"), { target: { files: [file] } });

  expect(await screen.findByText(/Строка 2/)).toBeInTheDocument();
  expect(screen.getByText(/Уже есть в реестре/)).toBeInTheDocument();
  expect(screen.getByText(/Не указана статья/)).toBeInTheDocument();
  expect(screen.getByText("Не распознана")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Подтвердить импорт" })).toBeDisabled();
  expect(confirmStoreExpenseImport).not.toHaveBeenCalled();
});

it("confirms valid CSV rows explicitly and reports an already imported file", async () => {
  vi.mocked(getManualStoreExpenses).mockResolvedValue({ items: [] });
  vi.mocked(getRecurringStoreExpenseTemplates).mockResolvedValue({ items: [] });
  vi.mocked(previewStoreExpenseImport)
    .mockResolvedValueOnce({ batch_id: "18", already_imported: false, rows: [{ line_number: 2, article: "storage", amount_rub: 500, period_start: "2026-09-01", period_end: "2026-09-30", note: "Склад", errors: [], duplicate: false }], valid_count: 1, invalid_count: 0, duplicate_count: 0 })
    .mockResolvedValueOnce({ batch_id: "18", already_imported: true, rows: [{ line_number: 2, article: "storage", amount_rub: 500, period_start: "2026-09-01", period_end: "2026-09-30", note: "Склад", errors: [], duplicate: true }], valid_count: 0, invalid_count: 0, duplicate_count: 1 });
  vi.mocked(confirmStoreExpenseImport).mockResolvedValue({ batch_id: "18", created_count: 1, expense_ids: [71], already_confirmed: false });
  render(<ManualStoreExpenseManager />);

  const input = await screen.findByLabelText("CSV-файл расходов");
  const file = new File(["article,amount_rub,period_start,period_end,note\nstorage,500,2026-09-01,2026-09-30,Склад"], "expenses.csv", { type: "text/csv" });
  fireEvent.change(input, { target: { files: [file] } });
  fireEvent.click(await screen.findByRole("button", { name: "Подтвердить импорт" }));

  await waitFor(() => expect(confirmStoreExpenseImport).toHaveBeenCalledWith("18"));
  expect(await screen.findByRole("status")).toHaveTextContent(/Добавлен.*расход/);
  fireEvent.change(input, { target: { files: [file] } });
  expect(await screen.findByText(/Этот файл уже импортирован/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Подтвердить импорт" })).toBeDisabled();
});

it("creates recurring templates and generates a month only after a manual action", async () => {
  vi.mocked(getManualStoreExpenses).mockResolvedValue({ items: [] });
  vi.mocked(getRecurringStoreExpenseTemplates)
    .mockResolvedValueOnce({ items: [] })
    .mockResolvedValue({ items: [{ id: 4, article: "taxes", amount_rub: 1200, note: "Аренда", active: true }] });
  vi.mocked(createRecurringStoreExpenseTemplate).mockResolvedValue({ id: 4, message: "created" });
  vi.mocked(generateRecurringStoreExpenses).mockResolvedValue({ created: 1, already_exists: 2, expense_ids: [81] });
  render(<ManualStoreExpenseManager />);

  expect(generateRecurringStoreExpenses).not.toHaveBeenCalled();
  fireEvent.change(await screen.findByLabelText("Сумма регулярного расхода, ₽"), { target: { value: "1200" } });
  fireEvent.change(screen.getByLabelText("Примечание шаблона"), { target: { value: "Аренда" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить шаблон" }));
  await waitFor(() => expect(createRecurringStoreExpenseTemplate).toHaveBeenCalledWith({ article: "taxes", amount_rub: 1200, note: "Аренда", active: true }));
  expect(screen.queryByLabelText("День месяца")).not.toBeInTheDocument();
  expect(screen.getByText(/создаётся за полный календарный месяц/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Создать расходы за месяц" }));
  await waitFor(() => expect(generateRecurringStoreExpenses).toHaveBeenCalledWith(expect.any(Number), expect.any(Number)));
  expect(await screen.findByRole("status")).toHaveTextContent(/Создано расходов: 1; уже создано ранее: 2/);
});

it("pauses and resumes a recurring template and refreshes its displayed state", async () => {
  const template = { id: 12, article: "storage", amount_rub: 300, note: "Склад", active: false };
  vi.mocked(getManualStoreExpenses).mockResolvedValue({ items: [] });
  vi.mocked(getRecurringStoreExpenseTemplates)
    .mockResolvedValueOnce({ items: [template] })
    .mockResolvedValueOnce({ items: [{ ...template, active: true }] })
    .mockResolvedValueOnce({ items: [template] });
  vi.mocked(setRecurringStoreExpenseTemplateActive)
    .mockResolvedValueOnce({ id: 12, active: true })
    .mockResolvedValueOnce({ id: 12, active: false });
  render(<ManualStoreExpenseManager />);

  fireEvent.click(await screen.findByRole("button", { name: "Включить" }));
  await waitFor(() => expect(setRecurringStoreExpenseTemplateActive).toHaveBeenNthCalledWith(1, 12, true));
  expect(await screen.findByText(/Шаблон включён/)).toBeInTheDocument();
  expect(await screen.findByRole("button", { name: "Отключить" })).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Отключить" }));
  await waitFor(() => expect(setRecurringStoreExpenseTemplateActive).toHaveBeenNthCalledWith(2, 12, false));
  expect(await screen.findByText(/Шаблон отключён/)).toBeInTheDocument();
  expect(await screen.findByRole("button", { name: "Включить" })).toBeInTheDocument();
  expect(getRecurringStoreExpenseTemplates).toHaveBeenCalledTimes(3);
});
