import { wbAvailabilityMessage } from "./cabinet-copy";
import { useDialogFocus } from "./dialog-focus";
import { WorkspaceTabs } from "./WorkspaceTabs";
import { useEffect, useState } from "react";
import { CalendarDays, CheckCircle2, ChevronRight, PackageCheck, Pencil, Plus, ShieldCheck, Truck, WalletCards, X } from "lucide-react";
import type { DashboardMetric, SupplierPulse, SupplyShipment } from "./contracts";
import { createProcurementPayment, getProcurementOperations, getSuppliers, getFbsSupplyBarcode, getFbsSupplyDetails, postSupplyReceipt, saveSupplier, updateProcurementStatus, type DashboardSession, type FbsSupplyBarcode, type FbsSupplyDetail, type SupplierRecord } from "./data-source";
import { LazyPanel } from "./LazyPanel";
import { lazyNamed } from "./LazyPanel";
import { MetricGrid, SectionHeader, Stat, StatusPill, cx } from "./cabinet-shared";
const SupplyPlanning = lazyNamed(() => import("./SupplyPlanning"), "SupplyPlanning");
const FbsOrdersPanel = lazyNamed(() => import("./FbsOrdersPanel"), "FbsOrdersPanel");

const supplyTabs = [{ id: "orders", label: "Заявки и приёмка" }, { id: "suppliers", label: "Поставщики" }, { id: "plan", label: "План закупок" }, { id: "fbs", label: "FBS" }] as const;
const supplyTabTargets: Record<string, string> = { "supply-orders": "orders", "supplier-directory": "suppliers", "supply-planning": "plan", "supply-fbs": "fbs" };

export function SupplyView({
  onAction,
  onRefresh,
  session,
  procurementOrders,
  supplierPulse,
  supplyMetrics,
  supplyShipments,
  supplyNewOrders,
  acceptanceCoefficients,
  transitTariffs,
  supplyErrors,
  onOpenShipment,
  target,
}: {
  onAction: (label: string, itemLabel: string) => void;
  onRefresh: () => Promise<void>;
  session: DashboardSession | null;
  procurementOrders: Array<Record<string, unknown>>;
  supplierPulse: SupplierPulse[];
  supplyMetrics: DashboardMetric[];
  supplyShipments: SupplyShipment[];
  supplyNewOrders: Array<Record<string, unknown>>;
  acceptanceCoefficients: Array<Record<string, unknown>>;
  transitTariffs: Array<Record<string, unknown>>;
  supplyErrors: Record<string, string>;
  onOpenShipment: (shipment: SupplyShipment) => void;
  target?: string | null;
}) {
  const [busyOrder, setBusyOrder] = useState<number | null>(null);
  const [paymentOrder, setPaymentOrder] = useState<Record<string, unknown> | null>(null);
  const [receiptOrder, setReceiptOrder] = useState<Record<string, unknown> | null>(null);
  const [procurementItems, setProcurementItems] = useState<Array<Record<string, unknown>>>([]);
  const [suppliers, setSuppliers] = useState<SupplierRecord[]>([]);
  const [editingSupplier, setEditingSupplier] = useState<SupplierRecord | null>(null);
  const [supplierBusy, setSupplierBusy] = useState(false);
  const [operationError, setOperationError] = useState<string | null>(null);
  const [fbsDialog, setFbsDialog] = useState<{ id: string; detail: FbsSupplyDetail; barcode: FbsSupplyBarcode | null } | null>(null);
  const [fbsBusy, setFbsBusy] = useState(false);
  const [fbsError, setFbsError] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    void Promise.all([getProcurementOperations(), getSuppliers()])
      .then(([operations, directory]) => {
        if (!active) return;
        setProcurementItems(operations.items);
        setSuppliers(directory.items);
      })
      .catch(() => {
        if (active) setOperationError("Не удалось загрузить данные поставок");
      });
    return () => { active = false; };
  }, [procurementOrders]);
  const saveSupplierRecord = async (item: SupplierRecord) => {
    setSupplierBusy(true);
    try {
      const result = await saveSupplier(item);
      if (result.item) setSuppliers((rows) => { const next = rows.filter((row) => row.id !== result.item!.id); return [...next, result.item!].sort((a, b) => a.name.localeCompare(b.name)); });
      else setSuppliers((await getSuppliers()).items);
      setEditingSupplier(null);
      await onRefresh().catch(() => undefined);
    } catch (error) { setOperationError(error instanceof Error ? error.message : "Не удалось сохранить поставщика"); }
    finally { setSupplierBusy(false); }
  };
  const updateStatus = async (orderId: number, status: string) => {
    setOperationError(null);
    setBusyOrder(orderId);
    try {
      await updateProcurementStatus(orderId, status);
      await onRefresh();
    } catch (error) {
      setOperationError(error instanceof Error ? error.message : "Не удалось обновить статус закупки");
    } finally {
      setBusyOrder(null);
    }
  };
  const submitPayment = async (payment: { amount: number; payment_date: string; method: string; note: string }) => {
    if (!paymentOrder) return;
    const orderId = Number(paymentOrder.id);
    setOperationError(null);
    setBusyOrder(orderId);
    try {
      await createProcurementPayment(orderId, payment);
      await onRefresh();
      setPaymentOrder(null);
    } catch (error) {
      setOperationError(error instanceof Error ? error.message : "Не удалось записать оплату");
    } finally {
      setBusyOrder(null);
    }
  };
  const submitReceipt = async (itemId: number, quantity: number) => {
    if (!receiptOrder) return;
    const orderId = Number(receiptOrder.id);
    setOperationError(null);
    setBusyOrder(orderId);
    try {
      await postSupplyReceipt(String(orderId), itemId, quantity);
      await onRefresh();
      const data = await getProcurementOperations();
      setProcurementItems(data.items);
      setReceiptOrder(null);
    } catch (error) {
      setOperationError(error instanceof Error ? error.message : "Не удалось провести приёмку");
    } finally {
      setBusyOrder(null);
    }
  };
  const openFbsSupply = async (supplyId: string) => {
    setFbsBusy(true);
    setFbsError(null);
    try {
      const detail = await getFbsSupplyDetails(supplyId);
      setFbsDialog({ id: supplyId, detail, barcode: null });
    } catch (error) {
      setFbsError(error instanceof Error ? error.message : "Не удалось открыть поставку WB");
    } finally {
      setFbsBusy(false);
    }
  };
  const loadFbsBarcode = async () => {
    if (!fbsDialog) return;
    setFbsBusy(true);
    setFbsError(null);
    try {
      const barcode = await getFbsSupplyBarcode(fbsDialog.id);
      setFbsDialog((current) => current ? { ...current, barcode } : current);
    } catch (error) {
      setFbsError(error instanceof Error ? error.message : "Не удалось получить QR поставки WB");
    } finally {
      setFbsBusy(false);
    }
  };
  return (
    <div className="workspace-view">
      {Object.keys(supplyErrors).length ? <p className="data-banner stale" role="status">{wbAvailabilityMessage(Object.values(supplyErrors))}</p> : null}
      <section className="hero-strip supply-hero" aria-label="Поставки">
        <div>
          <div className="eyebrow">
            <Truck size={16} />
            поставки
          </div>
          <h2>{supplyMetrics[0]?.value ?? "0 SKU"} ждут пополнения</h2>
          <p>Закупки, окна приемки и поставщики сведены в рабочую очередь без перехода в таблицы.</p>
        </div>
        <div className="hero-actions">
          <button className="secondary-button" type="button" onClick={() => onAction("Заявка", "поставки")}>
            <Plus size={17} />
            <span>Создать заявку</span>
          </button>
        </div>
      </section>

      <MetricGrid metrics={supplyMetrics} onMetricClick={(metric) => onAction("Поставка", metric.label)} />

      <WorkspaceTabs ariaLabel="Разделы поставок" storageKey="mh.tab.supply" target={target} targetMap={supplyTabTargets} tabs={supplyTabs} panels={{
        orders: (<>
      <div className="content-grid compact-flow">
        <section className="work-section" id="supply-orders">
          <SectionHeader kicker="Очередь" title="Поставки и заявки">
            <span className="section-tag">
              <Truck size={16} />
              ETA
            </span>
          </SectionHeader>
          {supplyShipments.length ? <div className="shipment-list">
            {supplyShipments.map((shipment) => <SupplyShipmentRow key={shipment.id} onAction={onAction} onOpen={() => onOpenShipment(shipment)} onOpenFbs={shipment.amount === "WB FBS" ? openFbsSupply : undefined} shipment={shipment} />)}
          </div> : <p className="plan-empty">Нет активных поставок</p>}
        </section>
        <aside className="insight-column">
          <section className="panel">
            <SectionHeader compact kicker="Окна" title="Приемка WB">
              <button className="icon-button" type="button" aria-label="Открыть поставки" onClick={() => onAction("Поставка", "Приёмка WB")}><CalendarDays size={20} /></button>
            </SectionHeader>
            <div className="slot-grid">
              {(() => {
                const coefficientRows = acceptanceCoefficients.slice(0, 3);
                if (!coefficientRows.length) return <p className="plan-empty">Коэффициенты приёмки WB пока не загружены или недоступны по токену.</p>;
                return coefficientRows.map((row, index) => { const warehouse = String(row.warehouseName ?? row.warehouse ?? row.warehouse_name ?? "Склад WB"); const date = String(row.date ?? row.dateFrom ?? row.date_to ?? `Окно ${index + 1}`); const coefficient = row.coefficient ?? row.value ?? row.coeff ?? "—"; return <button className="stat detail-trigger" type="button" key={`${warehouse}-${date}-${index}`} onClick={() => onAction("Поставка", `Приёмка WB: ${warehouse}`)}><span>{warehouse}</span><strong>{date}</strong><small>коэффициент: {String(coefficient)}</small></button>; });
              })()}
            </div>
          </section>
          <section className="panel">
            <SectionHeader compact kicker="Маршруты" title="Тарифы транзита">
              <button className="icon-button" type="button" aria-label="Открыть тарифы транзита" onClick={() => onAction("Поставка", "Тарифы транзита")}><Truck size={20} /></button>
            </SectionHeader>
            {transitTariffs.length ? <div className="slot-grid">{transitTariffs.slice(0, 4).map((row, index) => {
              const from = String(row.transitWarehouseName ?? row.fromWarehouseName ?? row.from ?? "Склад отправления");
              const to = String(row.destinationWarehouseName ?? row.toWarehouseName ?? row.to ?? "Направление");
              const box = row.boxTariff == null ? "—" : typeof row.boxTariff === "object" ? "по объёму" : `${row.boxTariff} ₽`;
              const pallet = row.palletTariff == null ? "—" : `${row.palletTariff} ₽`;
              return <button className="stat detail-trigger" type="button" key={`${from}-${to}-${index}`} onClick={() => onAction("Поставка", `Тариф: ${from} → ${to}`)}><span>{from}</span><strong>{to}</strong><small>короб: {box} · паллета: {pallet}</small></button>;
            })}</div> : <p className="plan-empty">Тарифы транзита WB пока не загружены или недоступны по токену.</p>}
          </section>
        </aside>
      </div>
      <section className="work-section">
        <SectionHeader kicker="Журнал" title="Закупочные документы"><Truck size={18} /></SectionHeader>
        {operationError ? <p className="operation-error" role="alert">{operationError}</p> : null}
        {procurementOrders.length ? <div className="procurement-order-list">{procurementOrders.map((order) => {
          const orderId = Number(order.id);
          const outstanding = Number(order.outstanding_amount ?? 0);
          const hasOpenItems = procurementItems.some((item) => Number(item.order_id) === orderId && Number(item.quantity ?? 0) > Number(item.posted_quantity ?? 0));
          const isBusy = busyOrder === orderId;
          return <article className="procurement-order-card detail-trigger" key={String(order.id)} role="button" tabIndex={0} onClick={() => onAction("Поставка", `Заявка #${orderId}`)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onAction("Поставка", `Заявка #${orderId}`); } }}>
            <div className="procurement-order-heading">
              <div><span>Заявка #{String(order.id)}</span><strong>{String(order.supplier_name ?? "Поставщик не указан")}</strong></div>
              <StatusPill tone={String(order.status).includes("Получ") ? "good" : "warn"}>{String(order.status ?? "")}</StatusPill>
            </div>
            <div className="procurement-order-meta"><span>Ожидается: <strong>{String(order.expected_date ?? "—")}</strong></span><span>К оплате: <strong>{outstanding > 0 ? `${Math.round(outstanding).toLocaleString("ru-RU")} ₽` : "нет"}</strong></span></div>
            <label className="procurement-status-field" onClick={(event) => event.stopPropagation()}><span>Статус заявки</span><select className="filter-select" value={String(order.status ?? "Запланировано")} disabled={isBusy} onChange={(event) => void updateStatus(orderId, event.target.value)}><option>Запланировано</option><option>Заказано</option><option>Частично оплачено</option><option>Оплачено</option><option>В пути</option><option>Частично получено</option><option>Получено</option><option>Отменено</option></select></label>
            <div className="procurement-order-actions">
              <button className="ghost-button" type="button" disabled={isBusy || !hasOpenItems} onClick={(event) => { event.stopPropagation(); setOperationError(null); setReceiptOrder(order); }}><PackageCheck size={16} />Провести приёмку</button>
              <button className="secondary-button" type="button" disabled={isBusy || outstanding <= 0} onClick={(event) => { event.stopPropagation(); setOperationError(null); setPaymentOrder(order); }}><WalletCards size={16} />{outstanding > 0 ? "Внести оплату" : "Оплачено"}</button>
            </div>
          </article>;
        })}</div> : <p className="plan-empty">Закупочных документов пока нет</p>}
      </section>
        </>),
        suppliers: (<>
      <section className="work-section supplier-directory-section">
        <SectionHeader kicker="Справочник" title="Поставщик">
          <button className="secondary-button" type="button" onClick={() => setEditingSupplier({ id: 0, name: "", contact_person: "", phone: "", messenger: "", email: "", country: "", default_currency: "RUB", payment_terms_days: 3, lead_time_days: 7, active: 1, note: "" })}><Plus size={16} />Добавить поставщика</button>
        </SectionHeader>
        {suppliers.length ? <div className="supplier-directory-grid">{suppliers.map((supplier) => <article className="supplier-directory-card detail-trigger" key={supplier.id} tabIndex={0} role="button" onClick={() => setEditingSupplier(supplier)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setEditingSupplier(supplier); } }}><div><span>{supplier.country || "Поставщик"}</span><h3>{supplier.name}</h3></div><p>{supplier.contact_person || "Контактное лицо не указано"}</p><div className="supplier-contact-list">{supplier.phone ? <span>Телефон: {supplier.phone}</span> : null}{supplier.email ? <span>Email: {supplier.email}</span> : null}{supplier.messenger ? <span>Мессенджер: {supplier.messenger}</span> : null}</div><div className="supplier-card-footer"><small>Срок поставки: {supplier.lead_time_days} дн.</small><button className="icon-button" type="button" aria-label={`Изменить поставщика ${supplier.name}`} onClick={(event) => { event.stopPropagation(); setEditingSupplier(supplier); }}><Pencil size={16} /></button></div></article>)}</div> : <p className="plan-empty">Поставщиков пока нет. Добавьте первого поставщика с контактами.</p>}
      </section>
        <div className="insight-column">
          <section className="panel">
            <SectionHeader compact kicker="Поставщики" title="Пульс">
              <button className="icon-button" type="button" aria-label="Создать заявку поставщику" onClick={() => onAction("Заявка", "поставщики")}><ShieldCheck size={20} /></button>
            </SectionHeader>
            {supplierPulse.length ? <div className="supplier-list">
              {supplierPulse.map((supplier) => <SupplierPulseRow item={supplier} key={supplier.name} onOpen={() => onAction("Поставщик", supplier.name)} />)}
            </div> : <p className="plan-empty">Нет данных о поставщиках</p>}
          </section>

        </div>
        </>),
        plan: (<>
      <LazyPanel label="планирование поставок"><SupplyPlanning
        readOnly={!session || session.mode === "demo" || !["owner", "manager"].includes(session.user.role.toLowerCase())}
        onRefresh={onRefresh}
      /></LazyPanel>
        </>),
        fbs: (<>
      <section className="work-section">
        <SectionHeader kicker="WB FBS" title="Новые сборочные задания"><PackageCheck size={18} /></SectionHeader>
        {supplyNewOrders.length ? <div className="shipment-list">{supplyNewOrders.slice(0, 12).map((order, index) => <article className="shipment-row" key={String(order.orderId ?? order.id ?? index)}><div><strong>Заказ {String(order.orderId ?? order.id ?? "WB")}</strong><span>{String(order.address ?? order.deliveryType ?? "Новое сборочное задание")}</span></div><StatusPill tone="warn">ожидает сборки</StatusPill></article>)}</div> : <p className="plan-empty">Новых сборочных заданий FBS нет или раздел недоступен для токена.</p>}
      </section>
      <LazyPanel label="заказы FBS"><FbsOrdersPanel canWrite={!!session && session.mode !== "demo" && ["owner", "manager"].includes(session.user.role.toLowerCase())} /></LazyPanel>
        </>),
      }} />
      {paymentOrder ? <ProcurementPaymentDialog busy={busyOrder === Number(paymentOrder.id)} order={paymentOrder} onClose={() => { if (!busyOrder) setPaymentOrder(null); }} onSubmit={submitPayment} /> : null}
      {editingSupplier ? <SupplierDialog busy={supplierBusy} item={editingSupplier} onClose={() => { if (!supplierBusy) setEditingSupplier(null); }} onSubmit={saveSupplierRecord} /> : null}
      {receiptOrder ? <ProcurementReceiptDialog busy={busyOrder === Number(receiptOrder.id)} items={procurementItems.filter((item) => Number(item.order_id) === Number(receiptOrder.id) && Number(item.quantity ?? 0) > Number(item.posted_quantity ?? 0))} order={receiptOrder} onClose={() => { if (!busyOrder) setReceiptOrder(null); }} onSubmit={submitReceipt} /> : null}
      {fbsError && !fbsDialog ? <p className="operation-error" role="alert">{fbsError}</p> : null}
      {fbsDialog ? <FbsSupplyDialog busy={fbsBusy} error={fbsError} item={fbsDialog} onClose={() => { if (!fbsBusy) { setFbsDialog(null); setFbsError(null); } }} onLoadBarcode={loadFbsBarcode} /> : null}
    </div>
  );
}

function FbsSupplyDialog({
  busy,
  error,
  item,
  onClose,
  onLoadBarcode,
}: {
  busy: boolean;
  error: string | null;
  item: { id: string; detail: FbsSupplyDetail; barcode: FbsSupplyBarcode | null };
  onClose: () => void;
  onLoadBarcode: () => Promise<void>;
}) {
  const dialogRef=useDialogFocus(onClose);
  const barcodeValue = item.barcode?.barcode ?? item.barcode?.file ?? item.barcode?.url;
  const imageSource = typeof barcodeValue === "string" && barcodeValue.startsWith("data:image/") ? barcodeValue : null;
  return <dialog ref={dialogRef} aria-modal="true" className="plan-drawer operation-dialog" open aria-labelledby="fbs-supply-dialog-title">
    <div className="dialog-header"><div><span className="eyebrow">WB FBS</span><h2 id="fbs-supply-dialog-title">Поставка {item.id}</h2></div><button className="icon-button" type="button" aria-label="Закрыть" onClick={onClose}><X size={18} /></button></div>
    {error ? <p className="operation-error" role="alert">{error}</p> : null}
    <div className="fbs-detail-actions"><button className="secondary-button" type="button" disabled={busy} onClick={() => void onLoadBarcode()}>{busy ? "Загружаю…" : "Получить QR / стикер"}</button></div>
    {imageSource ? <img className="fbs-barcode-preview" src={imageSource} alt={`QR поставки ${item.id}`} /> : item.barcode ? <p className="form-hint">WB вернул код в формате {String(item.barcode.type ?? "файла")}. Откройте его в кабинете WB для печати.</p> : null}
    <pre className="fbs-detail-json">{JSON.stringify(item.detail, null, 2)}</pre>
    <div className="dialog-actions"><button className="ghost-button" type="button" onClick={onClose}>Закрыть</button></div>
  </dialog>;
}

function SupplyShipmentRow({
  onAction,
  onOpen,
  onOpenFbs,
  shipment,
}: {
  onAction: (label: string, itemLabel: string) => void;
  onOpen?: (shipment: SupplyShipment) => void;
  onOpenFbs?: (supplyId: string) => void;
  shipment: SupplyShipment;
}) {
  const open = () => {
    if (onOpenFbs) {
      onOpenFbs(shipment.id);
      return;
    }
    if (onOpen) {
      onOpen(shipment);
      return;
    }
    onAction("Поставка", shipment.id);
  };
  return (
    <article className={cx("shipment-row", "detail-trigger", shipment.tone)} role="button" tabIndex={0} onClick={open} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); open(); } }}>
      <div>
        <span>{shipment.id}</span>
        <h3>{shipment.supplier}</h3>
      </div>
      <div className="shipment-meta">
        <Stat label="ETA" value={shipment.eta} />
        <Stat label="Сумма" value={shipment.amount} />
      </div>
      <StatusPill tone={shipment.tone}>{shipment.status}</StatusPill>
      <button className="icon-button" type="button" aria-label={onOpenFbs ? "Открыть поставку WB" : "Открыть поставку"} onClick={(event) => { event.stopPropagation(); open(); }}>
        <ChevronRight size={18} />
      </button>
    </article>
  );
}

function SupplierPulseRow({ item, onOpen }: { item: SupplierPulse; onOpen?: () => void }) {
  const content = <>
      <div>
        <strong>{item.name}</strong>
        <span>{item.detail}</span>
      </div>
      <StatusPill tone={item.tone}>{item.metric}</StatusPill>
    </>;
  return onOpen ? <button className="supplier-row detail-trigger" type="button" onClick={onOpen}>{content}</button> : <article className="supplier-row">{content}</article>;
}

function SupplierDialog({ busy, item, onClose, onSubmit }: { busy: boolean; item: SupplierRecord; onClose: () => void; onSubmit: (item: SupplierRecord) => void }) {
  const dialogRef=useDialogFocus(()=>{if(!busy)onClose();});
  const [draft, setDraft] = useState(item);
  const update = (key: keyof SupplierRecord, value: string | number) => setDraft((current) => ({ ...current, [key]: value }));
  return <div className="drawer-backdrop operation-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onClose(); }}>
    <section className="plan-drawer operation-dialog supplier-dialog" role="dialog" aria-modal="true" ref={dialogRef} aria-labelledby="supplier-dialog-title">
      <div className="drawer-header"><div><span className="eyebrow">справочник поставщиков</span><h2 id="supplier-dialog-title">{item.id ? "Редактировать поставщика" : "Добавить поставщика"}</h2><p>Контакты используются при оформлении закупок.</p></div><button className="icon-button" type="button" aria-label="Закрыть" disabled={busy} onClick={onClose}><X size={18} /></button></div>
      <form className="operation-form supplier-form" onSubmit={(event) => { event.preventDefault(); onSubmit(draft); }}>
        <label>Название поставщика<input required value={draft.name} onChange={(event) => update("name", event.target.value)} placeholder="Например, ООО Кромка+" /></label>
        <label>Контактное лицо<input value={draft.contact_person} onChange={(event) => update("contact_person", event.target.value)} placeholder="Имя менеджера" /></label>
        <div className="supplier-form-grid"><label>Телефон<input value={draft.phone} onChange={(event) => update("phone", event.target.value)} placeholder="+7 ..." /></label><label>Email<input type="email" value={draft.email} onChange={(event) => update("email", event.target.value)} placeholder="name@example.com" /></label><label>WeChat / Telegram<input value={draft.messenger} onChange={(event) => update("messenger", event.target.value)} placeholder="WeChat ID или @username" /></label><label>Страна<input value={draft.country} onChange={(event) => update("country", event.target.value)} placeholder="Китай / Россия" /></label><label>Срок оплаты, дней<input min="0" type="number" value={draft.payment_terms_days} onChange={(event) => update("payment_terms_days", Number(event.target.value))} /></label><label>Срок поставки, дней<input min="0" type="number" value={draft.lead_time_days} onChange={(event) => update("lead_time_days", Number(event.target.value))} /></label></div>
        <label>Примечание<textarea value={draft.note} onChange={(event) => update("note", event.target.value)} placeholder="Условия, реквизиты, комментарии" /></label>
        <label className="checkbox-field"><input type="checkbox" checked={Boolean(draft.active)} onChange={(event) => update("active", event.target.checked ? 1 : 0)} /> Активный поставщик</label>
        <div className="operation-actions"><button className="ghost-button" disabled={busy} type="button" onClick={onClose}>Отмена</button><button className="primary-button" disabled={busy} type="submit"><CheckCircle2 size={16} />{busy ? "Сохраняем" : "Сохранить поставщика"}</button></div>
      </form>
    </section>
  </div>;
}

function ProcurementPaymentDialog({
  busy,
  order,
  onClose,
  onSubmit,
}: {
  busy: boolean;
  order: Record<string, unknown>;
  onClose: () => void;
  onSubmit: (payment: { amount: number; payment_date: string; method: string; note: string }) => void;
}) {
  const dialogRef=useDialogFocus(()=>{if(!busy)onClose();});
  const outstanding = Math.max(0, Number(order.outstanding_amount ?? 0));
  const today = new Date().toISOString().slice(0, 10);
  return <div className="drawer-backdrop operation-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onClose(); }}>
    <section className="plan-drawer operation-dialog" role="dialog" aria-modal="true" ref={dialogRef} aria-labelledby="payment-title">
      <div className="drawer-header"><div><span className="eyebrow">реальная операция</span><h2 id="payment-title">Внести оплату</h2><p>Закупка #{String(order.id)} · остаток {Math.round(outstanding).toLocaleString("ru-RU")} ₽</p></div><button className="icon-button" type="button" aria-label="Закрыть" disabled={busy} onClick={onClose}><X size={18} /></button></div>
      <form className="operation-form" onSubmit={(event) => { event.preventDefault(); const form = new FormData(event.currentTarget); onSubmit({ amount: Number(form.get("amount")), payment_date: String(form.get("payment_date")), method: String(form.get("method")), note: String(form.get("note") ?? "") }); }}>
        <label>Сумма оплаты<input name="amount" required min="0.01" max={outstanding || undefined} step="0.01" type="number" defaultValue={outstanding ? outstanding.toFixed(2) : ""} /></label>
        <label>Дата оплаты<input name="payment_date" required type="date" defaultValue={today} /></label>
        <label>Способ оплаты<select className="filter-select" name="method" defaultValue="Банк"><option>Банк</option><option>Наличные</option><option>Карта</option><option>Другое</option></select></label>
        <label>Комментарий<input name="note" placeholder="Необязательно" /></label>
        <div className="operation-actions"><button className="ghost-button" type="button" disabled={busy} onClick={onClose}>Отмена</button><button className="primary-button" disabled={busy || outstanding <= 0} type="submit"><CheckCircle2 size={16} />{busy ? "Сохраняем" : "Записать оплату"}</button></div>
      </form>
    </section>
  </div>;
}

function ProcurementReceiptDialog({
  busy,
  items,
  order,
  onClose,
  onSubmit,
}: {
  busy: boolean;
  items: Array<Record<string, unknown>>;
  order: Record<string, unknown>;
  onClose: () => void;
  onSubmit: (itemId: number, quantity: number) => void;
}) {
  const dialogRef=useDialogFocus(()=>{if(!busy)onClose();});
  const first = items[0];
  const [itemId, setItemId] = useState(String(first?.id ?? ""));
  const selected = items.find((item) => String(item.id) === itemId) ?? first;
  const remaining = Math.max(0, Number(selected?.quantity ?? 0) - Number(selected?.posted_quantity ?? 0));
  return <div className="drawer-backdrop operation-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onClose(); }}>
    <section className="plan-drawer operation-dialog" role="dialog" aria-modal="true" ref={dialogRef} aria-labelledby="receipt-title">
      <div className="drawer-header"><div><span className="eyebrow">реальная операция</span><h2 id="receipt-title">Провести приёмку</h2><p>Закупка #{String(order.id)}. Остаток позиции будет оприходован в FIFO.</p></div><button className="icon-button" type="button" aria-label="Закрыть" disabled={busy} onClick={onClose}><X size={18} /></button></div>
      <form className="operation-form" onSubmit={(event) => { event.preventDefault(); onSubmit(Number(itemId), Number(new FormData(event.currentTarget).get("quantity"))); }}>
        <label>Позиция<select className="filter-select" value={itemId} onChange={(event) => setItemId(event.target.value)}>{items.map((item) => <option key={String(item.id)} value={String(item.id)}>{String(item.product_name || item.material_name || item.supplier_article || `Позиция #${String(item.id)}`)} · остаток {Math.max(0, Number(item.quantity ?? 0) - Number(item.posted_quantity ?? 0))}</option>)}</select></label>
        <label>Принято единиц<input name="quantity" required min="0.01" max={remaining || undefined} step="0.01" type="number" defaultValue={remaining || ""} /></label>
        <div className="operation-actions"><button className="ghost-button" type="button" disabled={busy} onClick={onClose}>Отмена</button><button className="primary-button" disabled={busy || !selected || remaining <= 0} type="submit"><CheckCircle2 size={16} />{busy ? "Проводим" : "Оприходовать"}</button></div>
      </form>
    </section>
  </div>;
}
