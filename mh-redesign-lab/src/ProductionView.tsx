import { useEffect, useState } from "react";
import { CalendarDays, CheckCircle2, ChevronRight, Factory, PackageCheck, Plus, Zap } from "lucide-react";
import type { DashboardMetric, MaterialNeed, ProductionStage, ProductionTask } from "./contracts";
import { closeProductionExecution, getProductionExecution, saveProductionExecution, type ProductionExecutionTask } from "./data-source";
import { LazyPanel } from "./LazyPanel";
import { lazyNamed } from "./LazyPanel";
import { MetricGrid, SectionHeader, Stat, StatusPill, askConfirm, cx } from "./cabinet-shared";
const WorkcenterCalendar = lazyNamed(() => import("./WorkcenterCalendar"), "WorkcenterCalendar");
const ProductionWorkspace = lazyNamed(() => import("./ProductionWorkspace"), "ProductionWorkspace");
const ManufacturingCosts = lazyNamed(() => import("./ManufacturingCosts"), "ManufacturingCosts");

export function ProductionView({
  materialNeeds,
  onAction,
  onRefresh,
  productionMetrics,
  productionStages,
  productionTasks,
  onOpenProductionTask,
  onOpenProductionStage,
  onOpenMaterialNeed,
}: {
  materialNeeds: MaterialNeed[];
  onAction: (label: string, itemLabel: string) => void;
  onRefresh: () => Promise<void>;
  productionMetrics: DashboardMetric[];
  productionStages: ProductionStage[];
  productionTasks: ProductionTask[];
  onOpenProductionTask: (task: ProductionTask) => void;
  onOpenProductionStage: (stage: ProductionStage) => void;
  onOpenMaterialNeed: (item: MaterialNeed) => void;
}) {
  const [productionTab, setProductionTab] = useState<"shift" | "wip" | "capacity" | "costs">("shift");
  const productionTabs = [{ id: "shift", label: "Смена" }, { id: "wip", label: "НЗП и сырьё" }, { id: "capacity", label: "Мощность и календарь" }, { id: "costs", label: "Состав и затраты" }] as const;
  const plannedValue = productionMetrics.find((metric) => metric.label === "План смены" || metric.label === "К запуску")?.value ?? "0";
  return (
    <div className="workspace-view production-view">
      <nav className="production-tabs" aria-label="Разделы производства">{productionTabs.map(tab => <button type="button" key={tab.id} aria-pressed={productionTab === tab.id} onClick={() => setProductionTab(tab.id)}>{tab.label}</button>)}</nav>
      <div hidden={productionTab !== "shift"}>
      <section className="hero-strip production-hero" aria-label="Производственный контур">
        <div>
          <div className="eyebrow">
            <Factory size={16} />
            смена
          </div>
          <h2>{productionTasks.length ? `К запуску: ${plannedValue} компл.` : "Смена не сформирована"}</h2>
          <p>{productionTasks.length ? "План, фактическое выполнение и материалы собраны рядом с задачами на запуск." : "Сначала соберите рабочую смену — после этого здесь появятся задания и их выполнение."}</p>
        </div>
        <div className="hero-actions">
          <button className="secondary-button" type="button" onClick={() => onAction("Смена", "производство")}>
            <Plus size={17} />
            <span>Собрать смену</span>
          </button>
        </div>
      </section>

      <div id="production-shift"><ProductionExecutionPanel onRefresh={onRefresh} /></div>

      <MetricGrid metrics={productionMetrics} onMetricClick={(metric) => onAction("Партия", metric.label)} />

      <div className="content-grid compact-flow">
        <section className="work-section">
          <SectionHeader kicker="Партии" title="Производственный лист">
            <span className="section-tag">
              <Factory size={16} />
              Смена
            </span>
          </SectionHeader>
          {productionTasks.length ? <div className="production-list">
            {productionTasks.map((task) => <ProductionTaskRow key={task.sku} onAction={onAction} onOpen={onOpenProductionTask} task={task} />)}
          </div> : <p className="plan-empty">Нет заданий в открытой смене. Нажмите «Собрать смену», чтобы создать рабочий план.</p>}
        </section>

        <aside className="insight-column">
          <section className="panel">
            <SectionHeader compact kicker="Этапы" title="Готовность">
              <button className="icon-button" type="button" aria-label="Открыть рабочую смену" onClick={() => onAction("Рабочая смена", "производство")}><Zap size={20} /></button>
            </SectionHeader>
            {productionStages.length ? <div className="timeline">
              {productionStages.map((stage) => <ProductionStageRow key={stage.label} stage={stage} onOpen={() => onOpenProductionStage(stage)} />)}
            </div> : <p className="plan-empty">Этапы не переданы API</p>}
          </section>

          <section className="panel">
            <SectionHeader compact kicker="Материалы" title="Покрытие сырья">
              <button className="icon-button" type="button" aria-label="Открыть закупки сырья" title="Открыть закупки сырья" onClick={() => onAction("Поставка", "сырьё") }><PackageCheck size={20} /></button>
            </SectionHeader>
            {materialNeeds.length ? <div className="material-list">
              {materialNeeds.map((item) => <MaterialNeedRow item={item} key={item.name} onOpen={() => onOpenMaterialNeed(item)} />)}
            </div> : <p className="plan-empty">Нет данных о сырье</p>}
          </section>
        </aside>
      </div>
      </div>
      <LazyPanel label="производство"><ProductionWorkspace view={productionTab === "wip" || productionTab === "capacity" ? productionTab : "hidden"} onChanged={() => void onRefresh()} /></LazyPanel>
      <div hidden={productionTab !== "capacity"}><LazyPanel label="календарь производства"><WorkcenterCalendar /></LazyPanel></div>
      <div hidden={productionTab !== "costs"}><LazyPanel label="себестоимость"><ManufacturingCosts onChanged={() => void onRefresh()} /></LazyPanel></div>
    </div>
  );
}

function ProductionExecutionPanel({ onRefresh }: { onRefresh: () => Promise<void> }) {
  const [tasks, setTasks] = useState<ProductionExecutionTask[]>([]);
  const [date, setDate] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const load = async () => {
    try {
      const result = await getProductionExecution();
      setTasks(result.tasks);
      const availableDates = Array.from(new Set(result.tasks.map((task) => task.task_date).filter((value): value is string => Boolean(value)))).sort((a, b) => b.localeCompare(a));
      setDate((current) => current && availableDates.includes(current) ? current : availableDates[0] ?? "");
      setMessage(null);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Не удалось загрузить смены");
    }
  };

  useEffect(() => { void load(); }, []);

  const dates = Array.from(new Set(tasks.map((task) => task.task_date).filter((value): value is string => Boolean(value)))).sort((a, b) => b.localeCompare(a));
  const visible = tasks.filter((task) => task.task_date === date);
  const updateTask = (taskKey: string, patch: Partial<ProductionExecutionTask>) => {
    setTasks((rows) => rows.map((row) => row.task_key === taskKey ? { ...row, ...patch } : row));
  };

  const save = async () => {
    setBusy(true);
    setMessage(null);
    try {
      await saveProductionExecution(visible);
      await load();
      await onRefresh();
      setMessage("Исполнение смены сохранено");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Не удалось сохранить смену");
    } finally {
      setBusy(false);
    }
  };

  const close = async () => {
    if (!await askConfirm("Закрыть смену и оприходовать подтверждённый фактический выпуск?")) return;
    setBusy(true);
    setMessage(null);
    try {
      // The editor keeps the current shift changes locally until the user
      // saves them. Persist those edits before closing so the backend closes
      // the same facts/statuses the operator just confirmed, rather than the
      // stale values from the last save.
      await saveProductionExecution(visible);
      const result = await closeProductionExecution(date);
      setMessage(`Смена закрыта: оприходовано ${result.units} компл.`);
      // The close operation is already committed by the API. Do not keep the
      // primary action in a busy state while the dashboard reloads unrelated
      // sections; a slow refresh must never look like a hung close request.
      setBusy(false);
      void load().catch(() => undefined);
      void onRefresh().catch(() => undefined);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Не удалось закрыть смену");
    } finally {
      setBusy(false);
    }
  };

  return <section className="work-section">
    <SectionHeader kicker="Исполнение" title="Рабочая смена"><CalendarDays size={18} /></SectionHeader>
    {message ? <p className="operation-error" role="status">{message}</p> : null}
    <div className="production-shift-toolbar">
    <label className="operation-field production-date-field">
      <span>Дата смены</span>
      <select aria-label="Дата смены" className="filter-select" disabled={!dates.length} value={date} onChange={(event) => setDate(event.target.value)}>
        {!dates.length ? <option value="">Нет открытых смен</option> : null}
        {dates.map((value) => <option key={value} value={value}>{new Date(`${value}T00:00:00`).toLocaleDateString("ru-RU")}</option>)}
      </select>
    </label>
    <span className="production-shift-count">{dates.length ? `${visible.length} ${visible.length === 1 ? "задача" : "задач"}` : "Соберите смену, чтобы начать работу"}</span>
    </div>
    {visible.length ? <div className="production-execution-list">
      {visible.map((task) => <article className="production-execution-task" key={task.task_key}>
        <div className="production-task-header">
          <strong>{task.product_name || task.supplier_article}</strong>
          <span>{task.supplier_article || task.task_key}</span>
        </div>
        <div className="production-task-fields">
          <div className="production-task-plan"><span>План</span><strong>{task.planned_units} ед.</strong></div>
          <label><span>Факт, ед.</span><input min="0" max={task.planned_units} type="number" value={task.actual_units} onChange={(event) => updateTask(task.task_key, { actual_units: Number(event.target.value) })} /></label>
          <label><span>Статус</span><select className="filter-select" value={task.status} onChange={(event) => updateTask(task.task_key, { status: event.target.value })}><option>Не начато</option><option>В работе</option><option>Изготовлено</option><option>Упаковано</option><option>Передано на отгрузку</option></select></label>
          <label className="production-task-note"><span>Примечание</span><input value={task.note} placeholder="Комментарий" onChange={(event) => updateTask(task.task_key, { note: event.target.value })} /></label>
        </div>
      </article>)}
    </div> : <p className="plan-empty">{dates.length ? "На выбранную дату заданий нет" : "Открытых смен пока нет. Нажмите «Собрать смену» и укажите дату, товар и план."}</p>}
    <div className="operation-actions">
      <button className="ghost-button" disabled={busy || !visible.length} type="button" onClick={() => void save()}>Сохранить исполнение</button>
      <button className="primary-button" disabled={busy || !visible.some((task) => task.actual_units > 0 && ["Изготовлено", "Упаковано", "Передано на отгрузку"].includes(task.status))} type="button" onClick={() => void close()}><CheckCircle2 size={16} />{busy ? "Проводим" : "Закрыть смену"}</button>
    </div>
  </section>;
}

function ProductionStageRow({ onOpen, stage }: { onOpen?: () => void; stage: ProductionStage }) {
  const content = <>
      <span />
      <div>
        <strong>{stage.label}</strong>
        <p>{stage.value}</p>
      </div>
    </>;
  const className = cx("signal", stage.status === "done" && "done", stage.status === "active" && "active", stage.status === "blocked" && "blocked", onOpen && "detail-trigger");
  return onOpen ? <button className={className} type="button" onClick={onOpen}>{content}</button> : <div className={className}>{content}</div>;
}

function ProductionTaskRow({
  onAction,
  onOpen,
  task,
}: {
  onAction: (label: string, itemLabel: string) => void;
  onOpen?: (task: ProductionTask) => void;
  task: ProductionTask;
}) {
  const open = () => {
    if (onOpen) {
      onOpen(task);
      return;
    }
    onAction("Партия", task.sku);
  };
  return (
    <article className={cx("production-row", "detail-trigger", task.tone)} role="button" tabIndex={0} onClick={open} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); open(); } }}>
      <div>
        <span>{task.sku}</span>
        <h3>{task.title}</h3>
      </div>
      <div className="production-counts">
        <Stat label="План" value={task.plan} />
        <Stat label="Факт" value={task.actual} />
      </div>
      <StatusPill tone={task.tone}>{task.status}</StatusPill>
      <button className="icon-button" type="button" aria-label="Открыть партию" onClick={(event) => { event.stopPropagation(); onAction("Партия", task.sku); }}>
        <ChevronRight size={18} />
      </button>
    </article>
  );
}

function MaterialNeedRow({ item, onOpen }: { item: MaterialNeed; onOpen?: () => void }) {
  const content = <>
      <div>
        <strong>{item.name}</strong>
        <span>{item.need}</span>
      </div>
      <div>
        <em>{item.stock}</em>
        <StatusPill tone={item.tone}>{item.status}</StatusPill>
      </div>
    </>;
  return onOpen ? <button className={cx("material-row", "detail-trigger", item.tone)} type="button" onClick={onOpen}>{content}</button> : <article className={cx("material-row", item.tone)}>{content}</article>;
}
