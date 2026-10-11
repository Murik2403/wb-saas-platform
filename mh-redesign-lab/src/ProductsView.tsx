import { wbAvailabilityMessage } from "./cabinet-copy";
import { useDialogFocus } from "./dialog-focus";
import { WorkspaceTabs } from "./WorkspaceTabs";
import { periodLengthDays } from "./periodLength";
import { useGroupSelection, useProductGroups, ALL_PRODUCTS } from "./useProductGroups";
import { useEffect, useRef, useState } from "react";
import { PackageCheck, Search, X } from "lucide-react";
import { answerCustomerFeedback, answerCustomerQuestion, processCustomerReturn, sendCustomerChatMessage, getPriceUploadStatus, getPriceUploadHistory, uploadProductPrices, updateProductCard, saveProductCardMedia, type CustomerQualityData, type DashboardPeriod, type ProductCard, type ProductEconomicsRow, type ProductPriceRow, type PriceUploadHistoryItem } from "./data-source";
import { useProductsPrevious, useProductsTrend } from "./useProductsTrend";
import { compareAmount, compareCount, comparePoints } from "./productsDelta";
import { LazyPanel } from "./LazyPanel";
import { lazyNamed } from "./LazyPanel";
import { DashboardPeriodControl, MetricGrid, SectionHeader, askConfirm, askText, notifyUser } from "./cabinet-shared";
const ProductEconomicsList = lazyNamed(() => import("./ProductEconomicsList"), "ProductEconomicsList");
const ProductsHeatmap = lazyNamed(() => import("./ProductsHeatmap"), "ProductsHeatmap");
const ProductGroupFilter = lazyNamed(() => import("./ProductGroupsPanel"), "ProductGroupFilter");
const ProductGroupsPanel = lazyNamed(() => import("./ProductGroupsPanel"), "ProductGroupsPanel");
const ProductCardsPanel = lazyNamed(() => import("./ProductCardsPanel"), "ProductCardsPanel");
const ShopQualityPanel = lazyNamed(() => import("./ShopQualityPanel"), "ShopQualityPanel");
const ProductPricesPanel = lazyNamed(() => import("./ProductPricesPanel"), "ProductPricesPanel");
const PriceAdvisorPanel = lazyNamed(() => import("./PriceAdvisorPanel"), "PriceAdvisorPanel");
const RnpPlanFact = lazyNamed(() => import("./RnpPlanFact"), "RnpPlanFact");

const productTabs = [{ id: "products", label: "Товары" }, { id: "analytics", label: "Аналитика" }, { id: "content", label: "Контент и качество" }, { id: "prices", label: "Цены" }] as const;
const productTargetMap: Record<string, string> = { "products-heatmap": "analytics", "rnp-plan-fact": "analytics", "product-cards": "content", "shop-quality": "content", "price-advisor": "prices", "product-prices": "prices", "price-uploads": "prices" };

export function ProductsView({
  period,
  productCards,
  productCardErrors,
  productPriceErrors,
  products,
  prices,
  customerQuality,
  onRefresh,
  setPeriod,
  onOpenProduct,
  target,
}: {
  period: DashboardPeriod;
  productCards: import("./data-source").ProductCard[];
  productCardErrors: string[];
  productPriceErrors: string[];
  products: ProductEconomicsRow[];
  prices: ProductPriceRow[];
  customerQuality: CustomerQualityData | null;
  onRefresh: () => Promise<void>;
  setPeriod: (period: DashboardPeriod) => void;
  onOpenProduct: (item: ProductEconomicsRow) => void;
  target?: string | null;
}) {
  const [query, setQuery] = useState("");
  const [priceDrafts, setPriceDrafts] = useState<Record<number, { price: number; discount: number }>>({});
  const [priceMessage, setPriceMessage] = useState("");
  const [priceUploadId, setPriceUploadId] = useState<number | null>(null);
  const [priceUploadStatus, setPriceUploadStatus] = useState("");
  const [priceSubmitting, setPriceSubmitting] = useState(false);
  const priceSubmitLock = useRef(false);
  const [priceResults, setPriceResults] = useState<Array<Record<string, unknown>>>([]);
  const [priceUploadHistory, setPriceUploadHistory] = useState<PriceUploadHistoryItem[]>([]);
  const [editingCard, setEditingCard] = useState<ProductCard | null>(null);
  const normalized = query.trim().toLocaleLowerCase("ru-RU");
  const productGroups = useProductGroups();
  const [groupId, setGroupId] = useGroupSelection(productGroups.groups, productGroups.loaded);
  const groupMembers = groupId === ALL_PRODUCTS ? null : new Set(productGroups.groups.find((g) => String(g.id) === groupId)?.nmIds);
  const visible = products.filter((item) => (!groupMembers || groupMembers.has(Number(item.nmId))) && (!normalized || `${item.article} ${item.product} ${item.nmId}`.toLocaleLowerCase("ru-RU").includes(normalized)));
  const revenue = products.reduce((sum, item) => sum + item.revenue, 0);
  const netProfit = products.reduce((sum, item) => sum + item.margin, 0);
  const netMarginPct = revenue > 0 ? (netProfit / revenue) * 100 : 0;
  const atRisk = products.filter((item) => item.daysOfStock !== null && item.daysOfStock >= 0 && item.daysOfStock < 7).length;
  const trend = useProductsTrend(period).data; // lazy, like the deltas: the table renders first
  const previous = useProductsPrevious(period).data; // lazy: tiles render first, the delta appears when it arrives
  const prev = previous ? (previous.hasData ? previous.totals : null) : undefined;
  const changes = prev === undefined ? [undefined, undefined, undefined, undefined] : [
    compareAmount(revenue, prev?.revenue, "₽"),
    comparePoints(netMarginPct, prev && prev.revenue > 0 ? prev.marginPct : null),
    compareCount(atRisk, prev?.atRisk, "SKU", false),
    compareCount(products.length, prev?.skuCount, "", null),
  ];
  useEffect(() => { void getPriceUploadHistory().then(setPriceUploadHistory).catch(() => undefined); }, []);
  useEffect(() => {
    if (!priceUploadId) return undefined;
    let active = true;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const result = await getPriceUploadStatus(priceUploadId);
        if (!active) return;
        const payload = (result.data ?? {}) as Record<string, unknown>;
        setPriceUploadStatus(`${String(result.label ?? "Статус неизвестен")} · ${String(payload.successGoodsNumber ?? "—")} из ${String(payload.overAllGoodsNumber ?? "—")}`);
        setPriceResults(Array.isArray(result.items) ? result.items : []);
        if (result.terminal === true) { setPriceUploadId(null); return; }
      } catch (error) {
        if (!active) return;
        setPriceUploadStatus(`${error instanceof Error ? error.message : "Статус недоступен"}. Проверка будет повторена; цены повторно не отправляются.`);
      }
      if (active) timer = window.setTimeout(() => void poll(), 10000);
    };
    void poll();
    return () => { active = false; window.clearTimeout(timer); };
  }, [priceUploadId]);
  const submitPrices = async () => {
    if (priceSubmitLock.current || priceUploadId) return;
    const goods = Object.entries(priceDrafts).map(([nmID, value]) => ({ nmID: Number(nmID), price: Number(value.price), discount: Number(value.discount) }));
    if (!goods.length) { setPriceMessage("Изменений цен нет"); return; }
    if (goods.some((row) => !Number.isInteger(row.price) || row.price <= 0 || !Number.isInteger(row.discount) || row.discount < 0 || row.discount > 99)) {
      setPriceMessage("Цена должна быть положительным целым числом, скидка — целым числом от 0 до 99."); return;
    }
    if (!await askConfirm(`Отправить изменения в WB для ${goods.length} SKU? Перед отправкой проверьте предпросмотр цен в списке.`)) return;
    priceSubmitLock.current = true;
    setPriceSubmitting(true);
    try {
      const result = await uploadProductPrices(goods, goods.flatMap((row) => { const old = (prices ?? []).find((item) => item.nmId === row.nmID); return old ? [{ nmID: row.nmID, vendorCode: old.vendorCode, price: old.price, discount: old.discount }] : []; }));
      const data = result.data as Record<string, unknown> | undefined;
      const uploadId = Number(data?.id ?? data?.uploadID ?? 0);
      if (!uploadId || result.error) throw new Error(String(result.errorText || "WB не вернул номер загрузки. Результат не подтверждён; проверьте историю в ЛК WB перед повторной отправкой."));
      setPriceUploadId(uploadId);
      setPriceUploadStatus("В обработке");
      setPriceResults([]);
      setPriceMessage(`Загрузка WB №${uploadId}: ожидаем результат обработки`);
      setPriceUploadHistory(await getPriceUploadHistory().catch(() => priceUploadHistory));
      setPriceDrafts({});
    } catch (error) { setPriceMessage(error instanceof Error ? error.message : "Не удалось загрузить цены"); }
    finally { priceSubmitLock.current = false; setPriceSubmitting(false); }
  };
  const qualityActions = {
    onRefresh: () => void onRefresh(),
    onAnswerFeedback: async (id: string) => { const text = await askText("Ответ на отзыв", "Спасибо за обратную связь!"); if (!text) return; try { await answerCustomerFeedback(id, text); await notifyUser("Ответ отправлен в WB"); } catch (error) { await notifyUser(error instanceof Error ? error.message : "Ответ не отправлен"); } },
    onAnswerQuestion: async (id: string) => { const text = await askText("Ответ на вопрос", "Спасибо за вопрос! Сейчас уточним информацию."); if (!text) return; try { await answerCustomerQuestion(id, text); await notifyUser("Ответ отправлен в WB"); } catch (error) { await notifyUser(error instanceof Error ? error.message : "Ответ не отправлен"); } },
    onReplyChat: async (replySign: string) => { if (!replySign) return; const text = await askText("Сообщение покупателю"); if (!text?.trim()) return; try { await sendCustomerChatMessage(replySign, text); await notifyUser("Сообщение отправлено в WB"); } catch (error) { await notifyUser(error instanceof Error ? error.message : "Сообщение не отправлено"); } },
    onReturn: async (id: string, decision: "approve" | "reject") => { if (!await askConfirm(decision === "approve" ? "Одобрить возврат и отправить решение в WB?" : "Отклонить возврат? Решение будет отправлено в WB.")) return; try { await processCustomerReturn(id, decision); await notifyUser(decision === "approve" ? "Возврат одобрен" : "Возврат отклонён"); } catch (error) { await notifyUser(error instanceof Error ? error.message : "Возврат не обработан"); } },
  };
  return (
    <div className="workspace-view">
      {(customerQuality?.errors?.length || productCardErrors.length || productPriceErrors.length) ? <p className="data-banner stale" role="status">{wbAvailabilityMessage([...(customerQuality?.errors ?? []), ...productCardErrors, ...productPriceErrors])}</p> : null}
      <WorkspaceTabs tabs={productTabs} ariaLabel="Разделы товаров" storageKey="mh.tab.products" target={target} targetMap={productTargetMap} panels={{
        products: <>
          <section className="hero-strip finance-hero"><div><div className="eyebrow"><PackageCheck size={16} /> товары</div><h2>{products.length} SKU в экономике</h2><p>Выручка, выкуп, реклама, запас и чистая маржинальность по каждому артикулу WB.</p></div></section>
          <MetricGrid metrics={[{ label: "Выручка", value: `${Math.round(revenue).toLocaleString("ru-RU")} ₽`, delta: "за период", detail: "по товарам", tone: "good", change: changes[0] }, { label: "Расчётная маржинальность", value: `${netMarginPct.toFixed(1)}%`, delta: "по учтённым затратам", detail: "себестоимость и комиссия WB учтены", tone: netMarginPct >= 0 ? "good" : "warn", change: changes[1] }, { label: "Риск запаса", value: String(atRisk), delta: "SKU", detail: "меньше 7 дней", tone: atRisk ? "warn" : "good", change: changes[2] }, { label: "Артикулы", value: String(products.length), delta: "в выборке", detail: "данные WB", tone: "info", change: changes[3] }]} onMetricClick={(metric) => setQuery(metric.label === "Артикулы" ? "" : metric.label)} />
          <p className="form-hint">ДРР — доля рекламных расходов в выручке: значение выше 100% означает, что расходы на рекламу превышают выручку. Отрицательная маржа означает превышение учтённых затрат над выручкой; при небольшой выручке процент может быть большим.</p>
          <section className="work-section"><SectionHeader kicker="Экономика" title="Товары по артикулам"><DashboardPeriodControl period={period} setPeriod={setPeriod} /></SectionHeader><label className="search-box product-search"><Search size={17} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Артикул или товар" type="search" /></label><LazyPanel label="фильтр групп"><ProductGroupFilter groups={productGroups.groups} value={groupId} onChange={setGroupId} /></LazyPanel><LazyPanel label="группы товаров"><ProductGroupsPanel products={products} groups={productGroups.groups} error={productGroups.error} onChanged={productGroups.reload} /></LazyPanel>{visible.length ? <LazyPanel label="экономику товаров"><ProductEconomicsList items={visible} onOpen={onOpenProduct} trend={trend} periodDays={periodLengthDays(period)} /></LazyPanel> : <p className="plan-empty">По этому запросу товары не найдены</p>}</section>
        </>,
        analytics: <>
          <LazyPanel label="карту товаров"><ProductsHeatmap period={period} /></LazyPanel>
          <LazyPanel label="план-факт"><RnpPlanFact products={products} /></LazyPanel>
        </>,
        content: <>
          <LazyPanel label="карточки товаров"><ProductCardsPanel cards={productCards} errors={productCardErrors} onEdit={setEditingCard} /></LazyPanel>
          <LazyPanel label="качество магазина"><ShopQualityPanel data={customerQuality} actions={qualityActions} /></LazyPanel>
        </>,
        prices: <>
          <LazyPanel label="советник по ценам"><PriceAdvisorPanel period={period} prices={prices ?? []} onApply={(items) => setPriceDrafts((rows) => ({ ...rows, ...Object.fromEntries(items.map((i) => [i.nmId, { price: i.price, discount: i.discount }])) }))} /></LazyPanel>
          <LazyPanel label="цены"><ProductPricesPanel prices={prices ?? []} query={query} drafts={priceDrafts} onDraft={(nmId, draft) => setPriceDrafts((rows) => ({ ...rows, [nmId]: draft }))}
            onSubmit={() => void submitPrices()} busy={priceSubmitting || !!priceUploadId} message={priceMessage} uploadStatus={priceUploadStatus}
            results={priceResults} history={priceUploadHistory} hasErrors={productPriceErrors.length > 0} /></LazyPanel>
        </>,
      }} />
      {editingCard ? <ProductCardEditor card={editingCard} onClose={() => setEditingCard(null)} onSaved={async () => { setEditingCard(null); await onRefresh(); }} /> : null}
    </div>
  );
}

function ProductCardEditor({
  card,
  onClose,
  onSaved,
}: {
  card: ProductCard;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const dialogRef=useDialogFocus(onClose);
  const [title, setTitle] = useState(card.title);
  const [description, setDescription] = useState(card.description ?? "");
  const [brand, setBrand] = useState(card.brand);
  const [mediaText, setMediaText] = useState(card.photos.join("\n"));
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const save = async () => {
    if (!confirmed) { setMessage("Подтвердите отправку изменений в WB."); return; }
    const urls = mediaText.split(/\r?\n/).map((url) => url.trim()).filter(Boolean);
    if (urls.some((url) => !/^https?:\/\//i.test(url))) { setMessage("Каждая ссылка на медиа должна начинаться с http:// или https://."); return; }
    setBusy(true); setMessage("");
    try {
      await updateProductCard(card.nmId, { title: title.trim(), description: description.trim(), brand: brand.trim() });
      if (urls.length) await saveProductCardMedia(card.nmId, urls);
      await onSaved();
    } catch (error) { setMessage(error instanceof Error ? error.message : "Изменения не отправлены"); }
    finally { setBusy(false); }
  };
  return <div className="drawer-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onClose(); }}>
    <section className="plan-drawer operation-dialog product-card-editor" role="dialog" aria-modal="true" ref={dialogRef} aria-labelledby="product-card-editor-title">
      <div className="drawer-header"><div><span className="eyebrow">Контент WB · редактирование</span><h2 id="product-card-editor-title">{card.title}</h2><p>WB {card.nmId} · {card.vendorCode || "без артикула продавца"}</p></div><button className="icon-button" type="button" aria-label="Закрыть" onClick={onClose} disabled={busy}><X size={18} /></button></div>
      <div className="form-grid"><label>Название<input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={255} /></label><label>Бренд<input value={brand} onChange={(event) => setBrand(event.target.value)} maxLength={255} /></label><label className="full-width">Описание<textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={5} maxLength={5000} /></label><label className="full-width">Медиафайлы, по одной ссылке на строку<textarea value={mediaText} onChange={(event) => setMediaText(event.target.value)} rows={5} /></label></div>
      <label className="form-checkbox"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /> Я подтверждаю отправку изменений и замену списка медиафайлов в WB</label>
      {message ? <p className="operation-error" role="status">{message}</p> : null}
      <div className="dialog-actions"><button className="ghost-button" type="button" onClick={onClose} disabled={busy}>Отмена</button><button className="primary-button" type="button" onClick={() => void save()} disabled={busy || !confirmed}>{busy ? "Отправка…" : "Сохранить в WB"}</button></div>
    </section>
  </div>;
}
