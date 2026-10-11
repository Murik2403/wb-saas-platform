import { WorkspaceTabs } from "./WorkspaceTabs";
import { AlertTriangle, Package, PackageCheck, Truck, Zap } from "lucide-react";
import type { DashboardMetric, InventoryWatchRow, ReorderItem, StockCoverageRow, StockHero, StockRule } from "./contracts";
import { LazyPanel } from "./LazyPanel";
import { lazyNamed } from "./LazyPanel";
import { InventoryTable, MetricGrid, SectionHeader, StatusPill, cx } from "./cabinet-shared";
const IncidentWorkflow = lazyNamed(() => import("./IncidentWorkflow"), "IncidentWorkflow");

const stockTabs = [{ id: "stock", label: "Остатки" }, { id: "reorder", label: "Пополнение" }] as const;
const stockTabTargets: Record<string, string> = { "stock-coverage": "stock", "seller-warehouses": "stock", "reorder-plan": "reorder", "stock-reorder-plan": "reorder" };

export function StockView({
  inventory,
  onAction,
  onOpenStockRow,
  onOpenReorder,
  onOpenCoverage,
  onOpenRule,
  reorderPlan,
  stockCoverage,
  stockHero,
  stockMetrics,
  stockRules,
  sellerWarehouses,
  target,
}: {
  inventory: InventoryWatchRow[];
  onAction: (label: string, itemLabel: string) => void;
  onOpenStockRow: (row: InventoryWatchRow) => void;
  onOpenReorder: (item: ReorderItem) => void;
  onOpenCoverage: (row: StockCoverageRow) => void;
  onOpenRule: (row: StockRule) => void;
  reorderPlan: ReorderItem[];
  stockCoverage: StockCoverageRow[];
  stockHero: StockHero;
  stockMetrics: DashboardMetric[];
  stockRules: StockRule[];
  sellerWarehouses: Array<{ id?: number; name?: string; office_id?: number; quantity?: number; processing?: boolean }>;
  target?: string | null;
}) {
  return (
    <div className="workspace-view">
      <LazyPanel label="инциденты"><IncidentWorkflow /></LazyPanel>
      <section className="split-hero">
        <button className={cx("hero-card", "detail-trigger", stockHero.tone === "danger" ? "urgent" : "calm")} type="button" onClick={() => onAction("Поставка", "Пополнение")}>
          <div className="eyebrow">
            <AlertTriangle size={16} />
            пополнение
          </div>
          <h2>{stockHero.primary}</h2>
          <p>{stockHero.primaryNote}</p>
        </button>
        <button className="hero-card calm detail-trigger" type="button" onClick={() => onAction("Контроль", "Запасы WB")}>
          <div className="eyebrow">
            <PackageCheck size={16} />
            запас
          </div>
          <h2>{stockHero.secondary}</h2>
          <p>{stockHero.secondaryNote}</p>
        </button>
      </section>

      <MetricGrid metrics={stockMetrics} onMetricClick={(metric) => onAction("Контроль", metric.label)} />

      <WorkspaceTabs ariaLabel="Разделы склада" storageKey="mh.tab.stock" target={target} targetMap={stockTabTargets} tabs={stockTabs} panels={{
        stock: (<>
      <InventoryTable inventory={inventory} onOpen={(row) => onOpenStockRow(row)} />
      <div className="stock-panels">
          <section className="panel" id="stock-coverage">
            <SectionHeader compact kicker="Контур" title="Запасы WB">
              <button className="icon-button" type="button" aria-label="Открыть контроль запасов" onClick={() => onAction("Контроль", "Запасы WB")}><Package size={20} /></button>
            </SectionHeader>
            <div className="warehouse-list">
              {stockCoverage.map((row) => (
                <CoverageRow key={row.label} row={row} onOpen={() => onOpenCoverage(row)} />
              ))}
            </div>
          </section>

          <section className="panel" id="seller-warehouses">
            <SectionHeader compact kicker="FBS / DBS" title="Склады продавца"><Truck size={20} /></SectionHeader>
            {sellerWarehouses.length ? <div className="warehouse-list">{sellerWarehouses.map((warehouse) => <button className="warehouse-row detail-trigger" type="button" key={String(warehouse.id ?? warehouse.name)} onClick={() => onAction("Поставка", String(warehouse.name ?? "Склад продавца"))}><span><strong>{warehouse.name ?? "Склад продавца"}</strong><small>{warehouse.processing ? "обновляется" : "остатки продавца"}</small></span><b>{Number(warehouse.quantity ?? 0).toLocaleString("ru-RU")} шт.</b></button>)}</div> : <p className="plan-empty">Склады продавца не заведены или у токена нет категории «Маркетплейс».</p>}
          </section>
      </div>
        </>),
        reorder: (<>
      <div className="stock-grid">
        <section className="work-section" id="reorder-plan">
          <SectionHeader kicker="План пополнения" title="Что отгружать первым">
            <span className="section-tag">
              <Truck size={16} />
              Склад
            </span>
          </SectionHeader>

          <div className="reorder-list">
            {reorderPlan.map((item) => (
              <article className={cx("reorder-card", "detail-trigger", item.tone)} key={item.sku} tabIndex={0} role="button" onClick={() => onOpenReorder(item)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onOpenReorder(item); } }}>
                <div>
                  <span>{item.sku}</span>
                  <h3>{item.name}</h3>
                  <p>{item.risk}</p>
                </div>
                <div className="reorder-numbers">
                  <strong>{item.days}</strong>
                  <span>{item.warehouse}</span>
                </div>
                <button className="secondary-button" type="button" onClick={(event) => { event.stopPropagation(); onAction("Отгрузка", item.sku); }}>
                  {item.qty}
                </button>
              </article>
            ))}
          </div>
        </section>
        <aside className="insight-column">
          <section className="panel">
            <SectionHeader compact kicker="Правила" title="Пополнение">
              <button className="icon-button" type="button" aria-label="Открыть поставки для пополнения" onClick={() => onAction("Поставка", "Пополнение")}><Zap size={20} /></button>
            </SectionHeader>
            <div className="rule-list">
              {stockRules.map((rule) => (
                <button className="rule-card detail-trigger" type="button" key={rule.id} onClick={() => onOpenRule(rule)}>
                  <strong>{rule.title}</strong>
                  <p>{rule.description}</p>
                </button>
              ))}
            </div>
          </section>
        </aside>
      </div>
        </>),
      }} />

    </div>
  );
}

function CoverageRow({ onOpen, row }: { onOpen?: () => void; row: StockCoverageRow }) {
  const content = <>
      <div>
        <strong>{row.label}</strong>
        <span>{row.detail}</span>
      </div>
      <div className="warehouse-meter">
        <span className={row.tone} style={{ width: `${row.width}%` }} />
      </div>
      <StatusPill tone={row.tone}>{row.value}</StatusPill>
    </>;
  return onOpen ? <button className="warehouse-row detail-trigger" type="button" onClick={onOpen}>{content}</button> : <div className="warehouse-row">{content}</div>;
}
