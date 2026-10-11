import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { ProductsView, ReportsView } from "./App";
import type { CustomerQualityData } from "./data-source";
import { getReportDelivery,requestTelegramLinkCode } from "./data-source";

vi.mock("./data-source", async (load) => ({
  ...await load<typeof import("./data-source")>(),
  getPriceUploadHistory: vi.fn(async () => []),
  getReportOperations: vi.fn(async () => ({ definitions: [], runs: [], metric_labels: {} })),
  getReportDelivery: vi.fn(async () => ({ configured: false, email_configured: false, telegram_configured: false, telegram_linked: false })),
  requestTelegramLinkCode: vi.fn(),
  getAnalyticsDashboard: vi.fn(async () => { throw new Error("Недоступна"); }),
}));
afterEach(() => { cleanup(); window.localStorage.clear(); });
const quality: CustomerQualityData = { rating: { valuation: 4.8, feedbackCount: 12 }, unread: {}, feedbacks: [], questions: [], quarantine: [], returns: { total: 2, claims: [] }, return_history: [], chats: [], errors: [] };
async function products(data: CustomerQualityData | null) {
  const view = render(<ProductsView period={{ id: "30d" }} products={[]} prices={[]} productCards={[]} productCardErrors={[]} productPriceErrors={[]}
    customerQuality={data} onRefresh={vi.fn(async () => {})} setPeriod={vi.fn()} onOpenProduct={vi.fn()} />);
  await userEvent.click(screen.getByRole("button", { name: "Контент и качество" }));
  return view;
}
function metric(label: string) { return screen.getByText(label).closest(".stat") as HTMLElement; }

describe("customer quality source failures", () => {
  it("shows unavailable counts for an entire failed response instead of manufactured zeros", async () => {
    await products({ ...quality, rating: {}, returns: {}, errors: ["/api/v1/dashboard/customer-quality: HTTP 409"] });
    for (const label of ["Рейтинг продавца", "Карантин цен", "Возвраты", "Чаты"]) {
      expect(within(metric(label)).getByText("—")).toBeVisible();
      expect(within(metric(label)).queryByText("0")).toBeNull();
    }
    expect(screen.getByText("нет данных об отзывах")).toBeVisible();
    expect(screen.queryByText("0 отзывов")).toBeNull();
  });
  it("retains real zeros and values in successful sections when only one labelled source fails", async () => {
    await products({ ...quality, errors: ["quarantine: у токена нет доступа к категории «Цены и скидки»."] });
    expect(within(metric("Карантин цен")).getByText("—")).toBeVisible();
    expect(within(metric("Возвраты")).getByText("2")).toBeVisible();
    expect(within(metric("Чаты")).getByText("0")).toBeVisible();
    expect(within(metric("Рейтинг продавца")).getByText("4.80")).toBeVisible();
    expect(screen.getByText("12 отзывов")).toBeVisible();
  });
  it("does not turn a missing quality response into zero-valued counters", async () => {
    await products(null);
    expect(within(metric("Карантин цен")).getByText("—")).toBeVisible();
    expect(within(metric("Чаты")).getByText("—")).toBeVisible();
  });
});

describe("reports presentation", () => {
  it("hides paused Telegram controls while keeping email delivery visible",async()=>{
    vi.mocked(getReportDelivery).mockResolvedValueOnce({configured:true,email_configured:true,email:'synthetic@example.test',telegram_configured:false,telegram_linked:false,telegram_paused:true});
    render(<ReportsView exportItems={[]} reportCards={[]} reportMetrics={[]} onAction={vi.fn()} onQuickReport={vi.fn(async()=>{})}/>);
    fireEvent.click(screen.getByRole('button',{name:'Доставка'}));
    expect(await screen.findByText('Доставка по email')).toBeVisible();
    expect(screen.queryByRole('button',{name:'Привязать Telegram'})).toBeNull();
    expect(screen.queryByText('Telegram',{exact:true})).toBeNull();
    expect(screen.getByText(/synthetic@example.test/)).toBeVisible();
  });
  it("recipient linking stays unavailable without owner permission",async()=>{
    vi.mocked(getReportDelivery).mockResolvedValueOnce({configured:true,email_configured:false,telegram_configured:true,telegram_linked:false});
    render(<ReportsView canLinkTelegram={false} exportItems={[]} reportCards={[]} reportMetrics={[]} onAction={vi.fn()} onQuickReport={vi.fn(async()=>{})}/>);
    fireEvent.click(screen.getByRole('button',{name:'Доставка'}));
    const button=await screen.findByRole('button',{name:'Привязать Telegram'});
    expect(button).toBeDisabled();expect(button).toHaveAttribute('title','Получателя Telegram изменяет только владелец');fireEvent.click(button);expect(requestTelegramLinkCode).not.toHaveBeenCalled();
  });
  it("uses readable metric emphasis and correct count declensions without changing values", async () => {
    render(<ReportsView exportItems={[]} reportCards={[]} onAction={vi.fn()} onQuickReport={vi.fn(async () => {})}
      reportMetrics={[{ label: "Резервная копия", value: "2026-09-23T23:55:39", delta: "настройки tenant", detail: "настройки tenant", tone: "info" }]} />);
    expect(screen.getByText("23.09.2026 23:55")).toBeVisible();
    expect(screen.getAllByText("настройки кабинета")).toHaveLength(2);
    expect(screen.queryByText("настройки tenant")).toBeNull();
    expect(screen.getByText("5 разделов · 30 дней")).toBeVisible();
    expect(screen.getAllByText("4 раздела · 30 дней")).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "Расписания и запуски" }));
    expect(await screen.findByRole("button", { name: "Создать расписание" })).toBeVisible();
  });
});
