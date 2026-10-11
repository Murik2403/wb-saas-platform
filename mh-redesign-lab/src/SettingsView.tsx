import { useDialogFocus } from "./dialog-focus";
import { OnboardingPanel } from "./OnboardingPanel";
import { ProductFeedbackLink } from "./ProductFeedbackLink";
import { RuntimeStatus } from "./RuntimeStatus";
import { useEffect, useRef, useState } from "react";
import { CheckCircle2, ClipboardCheck, Factory, Download, Pencil, RefreshCw, Search, ShieldCheck, SlidersHorizontal, WalletCards, X } from "lucide-react";
import { connectWBToken, disconnectWBToken, getWBTokenStatus, connectOzonCredentials, disconnectOzonCredentials, createTenantBackup, getMaterialInventory, getProductionNorms, getProductCostSettings, getTenantBackups, getTenantSettings, getTenantSyncHistory, getTenantSyncStatus, initializeMaterialFifo, startTenantSync, updateMaterialInventory, updateProductCostSettings, updateProductionNorms, updateTenantSettings, getManualExpenses, createManualExpense, deleteManualExpense, updateManualExpenseInclusion, getManagementEconomics, type ManagementEconomics, type ManualExpense, type MaterialFifoRow, type MaterialInventory, type ProductCostSetting, type ProductionNorm, type TenantSettings, type TenantSyncRun } from "./data-source";
import { LazyPanel } from "./LazyPanel";
import { lazyNamed } from "./LazyPanel";
import { AutomationRow, SectionHeader, askConfirm } from "./cabinet-shared";
const SettingsWorkspace = lazyNamed(() => import("./SettingsWorkspace"), "SettingsWorkspace");
const TaxSettingsPanel = lazyNamed(() => import("./TaxSettingsPanel"), "TaxSettingsPanel");
const ExpenseCoveragePanel = lazyNamed(() => import("./ExpenseCoveragePanel"), "ExpenseCoveragePanel");
const ManualStoreExpenseManager = lazyNamed(() => import("./ManualStoreExpenseManager"), "ManualStoreExpenseManager");
const SettingsChangeHistory = lazyNamed(() => import("./SettingsChangeHistory"), "SettingsChangeHistory");
const SyncHistory = lazyNamed(() => import("./SyncHistory"), "SyncHistory");
const BackupHistory = lazyNamed(() => import("./SyncHistory"), "BackupHistory");

export function SettingsViewV2({ onSynced, migration, sectionTarget, canManage = false, canWriteOperations = false }: { onSynced: () => Promise<void>; migration: React.ReactNode; sectionTarget?: string | null; canManage?: boolean; canWriteOperations?: boolean }) {
  const [requestedSection, setRequestedSection] = useState(sectionTarget);
  useEffect(() => { if (sectionTarget) setRequestedSection(sectionTarget); }, [sectionTarget]);
  const [settings, setSettings] = useState<TenantSettings | null>(null);
  const [backups, setBackups] = useState<Array<Record<string, unknown>>>([]);
  const [syncRuns, setSyncRuns] = useState<TenantSyncRun[]>([]);
  const [productCosts, setProductCosts] = useState<ProductCostSetting[]>([]);
  const [manualExpenses, setManualExpenses] = useState<ManualExpense[]>([]);
  const [managementEconomics, setManagementEconomics] = useState<ManagementEconomics | null>(null);
  const [productionNorms, setProductionNorms] = useState<ProductionNorm[]>([]);
  const [materialInventory, setMaterialInventory] = useState<MaterialInventory[]>([]);
  const [materialFifo, setMaterialFifo] = useState<MaterialFifoRow[]>([]);
  const [openingRate, setOpeningRate] = useState(0.01);
  const [costQuery, setCostQuery] = useState("");
  const [editingCost, setEditingCost] = useState<ProductCostSetting | null>(null);
  const [editingNorm, setEditingNorm] = useState<ProductionNorm | null>(null);
  const [wbToken, setWbToken] = useState("");
  const [wbScopes, setWbScopes] = useState<string[]>([]);
  const [wbMissingScopes, setWbMissingScopes] = useState<string[]>([]);
  const [wbScopeMetadataAvailable, setWbScopeMetadataAvailable] = useState(false);
  const [wbReadOnly, setWbReadOnly] = useState(false);
  const [wbExpiresAt, setWbExpiresAt] = useState<number | null>(null);
  const [ozonClientId, setOzonClientId] = useState("");
  const [ozonApiKey, setOzonApiKey] = useState("");
  const [message, setMessage] = useState("");
  const [historyRefreshKey, setHistoryRefreshKey] = useState(0);
  const [busy, setBusy] = useState<"settings" | "backup" | "sync" | "cost" | "material" | "expense" | "wb-token" | null>(null);
  const loadVersion = useRef(0);

  const load = async () => {
    const version = ++loadVersion.current;
    try {
      const [nextSettings, backupData, syncData, costData, normsData, materialData, wbStatus, expenseData] = await Promise.all([getTenantSettings(), canManage ? getTenantBackups() : Promise.resolve({backups:[]}), getTenantSyncHistory(), getProductCostSettings(), getProductionNorms(), getMaterialInventory(), getWBTokenStatus(), getManualExpenses().catch(() => ({ items: [] as ManualExpense[] }))]);
      if (version !== loadVersion.current) return;
      setSettings(nextSettings);
      setBackups(backupData.backups);
      setSyncRuns(syncData.runs);
      setProductCosts(costData.items);
      setManualExpenses(expenseData.items);
      setProductionNorms(normsData.items);
      setMaterialInventory(materialData.items);
      setMaterialFifo(materialData.fifo);
      setOpeningRate(materialData.opening_rate_rub_m);
      setWbScopes(wbStatus.scopes);
      setWbMissingScopes(wbStatus.missing_scopes);
      setWbScopeMetadataAvailable(Boolean(wbStatus.scope_metadata_available));
      setWbReadOnly(Boolean(wbStatus.read_only));
      setWbExpiresAt(wbStatus.expires_at ?? null);
    } catch (error) {
      if (version === loadVersion.current) setMessage(error instanceof Error ? error.message : "Не удалось загрузить параметры кабинета");
    }
  };

  useEffect(() => { void load(); return () => { loadVersion.current++; }; }, [canManage]);

  const saveSettings = async () => {
    if (!settings) return;
    setBusy("settings");
    try {
      setSettings(await updateTenantSettings(settings));
      setMessage("Параметры сохранены");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Не удалось сохранить параметры");
    } finally {
      setBusy(null);
    }
  };

  const saveWBToken = async () => {
    if (!canManage) return;
    if (!wbToken.trim()) { setMessage("Введите WB-токен"); return; }
    setBusy("wb-token");
    try {
      const result = await connectWBToken(wbToken);
      setWbToken("");
      setWbScopes(result.scopes);
      setWbMissingScopes(result.missing_scopes);
      setWbScopeMetadataAvailable(Boolean(result.scope_metadata_available));
      setWbReadOnly(Boolean(result.read_only));
      setWbExpiresAt(result.expires_at ?? null);
      setSettings((current) => current ? { ...current, wb_connected: result.connected } : current);
      setMessage("WB подключён. Запустите первую синхронизацию ниже.");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Не удалось подключить WB"); }
    finally { setBusy(null); }
  };

  const removeWBToken = async () => {
    if (!canManage) return;
    setBusy("wb-token");
    try {
      await disconnectWBToken();
      setSettings((current) => current ? { ...current, wb_connected: false } : current);
      setWbScopes([]); setWbMissingScopes([]);
      setWbScopeMetadataAvailable(false); setWbReadOnly(false); setWbExpiresAt(null);
      setMessage("WB отключён");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Не удалось отключить WB"); }
    finally { setBusy(null); }
  };

  const saveOzon = async () => {
    if (!canManage) return;
    if (!ozonClientId.trim() || !ozonApiKey.trim()) { setMessage("Введите Client-Id и Api-Key Ozon"); return; }
    setBusy("wb-token");
    try {
      await connectOzonCredentials(ozonClientId, ozonApiKey);
      setOzonClientId(""); setOzonApiKey("");
      setSettings((current) => current ? { ...current, ozon_connected: true } : current);
      setMessage("Ozon подключён");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Не удалось подключить Ozon"); }
    finally { setBusy(null); }
  };

  const removeOzon = async () => {
    if (!canManage) return;
    setBusy("wb-token");
    try { await disconnectOzonCredentials(); setSettings((current) => current ? { ...current, ozon_connected: false } : current); setMessage("Ozon отключён"); }
    catch (error) { setMessage(error instanceof Error ? error.message : "Не удалось отключить Ozon"); }
    finally { setBusy(null); }
  };

  const createBackup = async () => {
    if (!canManage) return;
    setBusy("backup");
    try {
      const result = await createTenantBackup();
      setMessage(`Создана резервная копия: ${result.name}`);
      const backupData = await getTenantBackups();
      setBackups(backupData.backups);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Не удалось создать резервную копию");
    } finally {
      setBusy(null);
    }
  };

  const monitorSync = async () => {
    try {
      for (let attempt = 0; attempt < 60; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 5000));
        const status = await getTenantSyncStatus();
        if (status.status === "running") continue;
        const [syncData, costData] = await Promise.all([getTenantSyncHistory(), getProductCostSettings()]);
        setSyncRuns(syncData.runs);
        setProductCosts(costData.items);
        await onSynced();
        setMessage(status.status === "success" ? "Синхронизация завершена, данные обновлены." : status.status === "partial" ? "Данные обновлены частично. Подробности — в журнале синхронизации." : (status.details || "Синхронизация завершилась с ошибкой"));
        return;
      }
      setMessage("Синхронизация продолжается в фоне. История обновится после завершения.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Не удалось обновить статус синхронизации");
    }
  };

  const startSync = async (scope: "all" | "ads" | "finance" = "all", days = 90) => {
    setBusy("sync");
    try {
      await startTenantSync(scope, days);
      setMessage("Синхронизация WB запущена. Можно продолжать работу, статус появится в журнале.");
      await monitorSync();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Не удалось запустить синхронизацию");
    } finally {
      setBusy(null);
    }
  };

  const saveCost = async (item: ProductCostSetting, reason?: string) => {
    setBusy("cost");
    try {
      await updateProductCostSettings([item], reason);
      setMessage(`Себестоимость для «${item.product_name || item.supplier_article || item.nm_id}» сохранена`);
      setHistoryRefreshKey((value) => value + 1);
      const costData = await getProductCostSettings();
      setProductCosts(costData.items);
      return true;
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Не удалось сохранить себестоимость");
      return false;
    } finally {
      setBusy(null);
    }
  };

  const saveExpense = async (form: HTMLFormElement) => {
    const data = new FormData(form);
    const nmId = Number(data.get("nm_id"));
    if (!nmId) { setMessage("Выберите товар"); return; }
    setBusy("expense");
    try {
      await createManualExpense({ nm_id: nmId, article: String(data.get("article") || ""), amount_rub: Number(data.get("amount_rub") || 0), allocation_basis: String(data.get("allocation_basis") || "per_unit") as ManualExpense["allocation_basis"], source: "manual", period_start: String(data.get("period_start") || ""), period_end: String(data.get("period_end") || ""), note: String(data.get("note") || ""), include_in_management_result: Boolean(data.get("include_in_management_result")) });
      setManualExpenses((await getManualExpenses()).items);
      form.reset();
      setMessage("Расход сохранён");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Не удалось сохранить расход"); }
    finally { setBusy(null); }
  };

  const removeExpense = async (id: number) => {
    setBusy("expense");
    try { await deleteManualExpense(id); setManualExpenses((rows) => rows.filter((row) => row.id !== id)); setMessage("Расход удалён"); }
    catch (error) { setMessage(error instanceof Error ? error.message : "Не удалось удалить расход"); }
    finally { setBusy(null); }
  };

  const toggleExpenseInclusion = async (item: ManualExpense) => {
    const included = !item.include_in_management_result;
    const confirmation = included
      ? "Подтвердите: эта сумма отсутствует в данных WB, расходах WB-рекламы и себестоимости товара. Включить её в управленческий результат?"
      : "Исключить этот ручной расход из управленческого результата?";
    if (!window.confirm(confirmation)) return;
    setBusy("expense");
    try {
      await updateManualExpenseInclusion(item.id, included);
      setManualExpenses((rows) => rows.map((row) => row.id === item.id ? { ...row, include_in_management_result: included } : row));
      setMessage(included ? "Расход включён в управленческий результат" : "Расход оставлен только в реестре");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Не удалось изменить учёт расхода"); }
    finally { setBusy(null); }
  };

  const calculateManagement = async (item: ManualExpense) => {
    setBusy("expense");
    try { setManagementEconomics(await getManagementEconomics(item.nm_id, item.period_start, item.period_end)); }
    catch (error) { setMessage(error instanceof Error ? error.message : "Не удалось рассчитать управленческий результат"); }
    finally { setBusy(null); }
  };

  const saveNorm = async (item: ProductionNorm, reason?: string) => {
    setBusy("cost");
    try {
      await updateProductionNorms([{ nm_id: item.nm_id, material_per_unit: item.material_per_unit, min_batch: item.min_batch, pack_size: item.pack_size, enabled: item.enabled }], reason);
      setProductionNorms((rows) => rows.map((row) => row.nm_id === item.nm_id ? item : row));
      setHistoryRefreshKey((value) => value + 1);
      setMessage(`Норма для «${item.supplier_article || item.nm_id}» сохранена`);
      setEditingNorm(null);
    } catch (error) { setMessage(error instanceof Error ? error.message : "Не удалось сохранить норму"); }
    finally { setBusy(null); }
  };

  const saveMaterials = async () => {
    setBusy("material");
    try {
      const result = await updateMaterialInventory(materialInventory);
      setMaterialInventory(result.items);
      setMaterialFifo(result.fifo);
      setOpeningRate(result.opening_rate_rub_m);
      setMessage("Физические остатки сырья сохранены. Теперь можно инициализировать FIFO.");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Не удалось сохранить остатки сырья"); }
    finally { setBusy(null); }
  };

  const initializeMaterials = async () => {
    if (!await askConfirm("Инициализировать FIFO по подтверждённым физическим остаткам сырья? Слои будут созданы с указанной ставкой.")) return;
    setBusy("material");
    try {
      const result = await initializeMaterialFifo(openingRate);
      setMaterialFifo(result.fifo);
      setOpeningRate(result.opening_rate_rub_m);
      const warnings = result.result.warnings?.length ? ` Предупреждений: ${result.result.warnings.length}.` : "";
      setMessage(`FIFO сырья обновлён: создано слоёв ${result.result.created}, учтено количество ${Number(result.result.meters).toLocaleString("ru-RU")}.${warnings}`);
      await onSynced();
    } catch (error) { setMessage(error instanceof Error ? error.message : "Не удалось инициализировать FIFO сырья"); }
    finally { setBusy(null); }
  };

  if (!settings) return <div className="workspace-view"><section className="work-section"><p className="plan-empty">{message || "Загружаем параметры кабинета."}</p></section></div>;

  return (
    <div className="workspace-view">
      <section className="hero-strip">
        <div>
          <div className="eyebrow"><SlidersHorizontal size={16} /> настройки</div>
          <h2>Подключения и обслуживание</h2>
          <p>Токены остаются в защищённом хранилище кабинета. Резервные копии не содержат ключей доступа.</p>
        </div>
      </section>
      <OnboardingPanel canManageConnections={canManage} canWrite={canWriteOperations} connected={settings.wb_connected} runs={syncRuns} costs={productCosts}
        days={settings.initial_history_days} busy={busy !== null} token={wbToken}
        onTokenChange={setWbToken} onConnect={() => void saveWBToken()}
        onDaysChange={(days) => setSettings({ ...settings, initial_history_days: days })}
        onSync={() => void startSync("all", settings.initial_history_days)}
        onEditCost={(item) => setEditingCost({ ...item })} />
      <ProductFeedbackLink />
      {message ? <p className="operation-error" role="status">{message}</p> : null}
      <LazyPanel label="настройки"><SettingsWorkspace sectionTarget={requestedSection}
        connections={<>      <div className="settings-connections-grid">
        <section className="work-section">
          <SectionHeader kicker="Обновление" title="Интервалы работы"><SlidersHorizontal size={18} /></SectionHeader>
          <div className="settings-form settings-intervals">
            <label className="operation-field"><span>Синхронизация WB, минут</span><input min="15" max="1440" type="number" value={settings.sync_interval_minutes} onChange={(event) => setSettings({ ...settings, sync_interval_minutes: Number(event.target.value) })} /></label>
            <label className="operation-field"><span>История при первом запуске, дней</span><input min="7" max="90" type="number" value={settings.initial_history_days} onChange={(event) => setSettings({ ...settings, initial_history_days: Number(event.target.value) })} /></label>
            <label className="operation-field"><span>Обновление рекомендаций, минут</span><input min="15" max="1440" type="number" value={settings.agent_interval_minutes} onChange={(event) => setSettings({ ...settings, agent_interval_minutes: Number(event.target.value) })} /></label>
            <button className="primary-button" disabled={busy !== null} type="button" onClick={() => void saveSettings()}><CheckCircle2 size={17} /><span>{busy === "settings" ? "Сохраняем" : "Сохранить параметры"}</span></button>
          </div>
        </section>
        <div className="settings-connection-panel">
          <section className="panel">
            <SectionHeader compact kicker="Подключения" title="Статус API"><ShieldCheck size={20} /></SectionHeader>
            {!settings.wb_connected ? <div className="settings-form connection-form" role="group" aria-label="Подключение Wildberries"><p className="form-hint">Подключите WB-токен, чтобы загрузить продажи, остатки, финансы и отчёты.</p><label className="operation-field"><span>WB API-токен</span><input autoComplete="off" type="password" value={wbToken} onChange={(event) => setWbToken(event.target.value)} placeholder="Вставьте токен WB" /></label><button className="primary-button" disabled={!canManage || busy !== null} type="button" onClick={() => void saveWBToken()}>{busy === "wb-token" ? "Проверяем" : "Проверить и сохранить"}</button></div> : null}
            <div className="automation-list">
              <AutomationRow item={{ title: "Wildberries API", detail: "Данные и операции кабинета", status: settings.wb_connected ? "подключён" : "не подключён", tone: settings.wb_connected ? "good" : "warn" }} />
              <AutomationRow item={{ title: "Ozon Seller API", detail: "Используется агентом цен", status: settings.ozon_connected ? "подключён" : "не подключён", tone: settings.ozon_connected ? "good" : "info" }} />
            </div>
            {!settings.ozon_connected ? <div className="settings-form connection-form" role="group" aria-label="Подключение Ozon"><p className="form-hint">Подключите Ozon для ценовых рекомендаций.</p><label className="operation-field"><span>Client-Id</span><input autoComplete="off" value={ozonClientId} onChange={(event) => setOzonClientId(event.target.value)} /></label><label className="operation-field"><span>Api-Key</span><input autoComplete="off" type="password" value={ozonApiKey} onChange={(event) => setOzonApiKey(event.target.value)} /></label><button className="secondary-button" disabled={!canManage || busy !== null} type="button" onClick={() => void saveOzon()}>{busy === "wb-token" ? "Сохраняем" : "Сохранить Ozon"}</button></div> : <button className="ghost-button" disabled={!canManage || busy !== null} type="button" onClick={() => void removeOzon()}>Отключить Ozon</button>}
            {settings.wb_connected ? <div className="connection-actions"><span className="form-hint">Токен хранится зашифрованно и не показывается повторно.</span><button className="ghost-button" disabled={!canManage || busy !== null} type="button" onClick={() => void removeWBToken()}>Отключить WB</button></div> : null}
            {wbScopes.length || wbMissingScopes.length ? <p className="form-hint">Проверено: {wbScopes.join(", ") || "—"}. Для полного функционала добавьте категории: {wbMissingScopes.join(", ")}.</p> : null}
            {wbReadOnly ? <p className="form-hint warning-text">Токен WB доступен только для чтения: операции изменения и печати в кабинете могут быть недоступны.</p> : null}
            {wbScopeMetadataAvailable && wbExpiresAt ? <p className="form-hint">Срок действия токена: {new Date(wbExpiresAt * 1000).toLocaleString("ru-RU")}</p> : null}
            {settings.wb_connected && !wbScopeMetadataAvailable ? <p className="form-hint">WB не передал карту категорий для сохранённого токена. Проверена категория «Статистика»; права остальных разделов уточняются при обращении к ним.</p> : null}
          </section>
        </div>
      </div>
</>}
        data={<>{migration}      <section className="work-section">
        <SectionHeader kicker="WB API" title="Синхронизация и журнал"><RefreshCw size={18} /></SectionHeader>
        <div className="operation-actions">
          <p className="plan-empty">Обновляет продажи, остатки, финансы и рекламу. Результат каждого запуска — в журнале ниже.</p>
          <button className="secondary-button" disabled={busy !== null || !settings.wb_connected} type="button" onClick={() => void startSync()}><RefreshCw size={17} />{busy === "sync" ? "Синхронизируем" : "Синхронизировать сейчас"}</button>
          <button className="ghost-button" disabled={busy !== null || !settings.wb_connected} type="button" onClick={() => void startSync("finance", 90)}>Обновить финансовый отчёт WB (90 дней)</button>
        </div>
        <LazyPanel label="историю синхронизаций"><SyncHistory runs={syncRuns} /></LazyPanel>
        <button className="ghost-button" disabled={busy !== null || !settings.wb_connected} type="button" onClick={() => void startSync("ads")}>Повторить только рекламу</button>
        <RuntimeStatus />
      </section>
      <LazyPanel label="историю изменений"><SettingsChangeHistory refreshKey={historyRefreshKey} /></LazyPanel></>}
        production={<>      <section className="work-section" id="material-inventory">
        <SectionHeader kicker="Производство" title="Остатки сырья и сверка FIFO"><ClipboardCheck size={18} /></SectionHeader>
        <div className="operation-actions">
          <p className="plan-empty">Сначала укажите фактический остаток по каждому материалу и включите «Подтверждён». После проверки создайте FIFO-слои стартового сырья.</p>
          <div className="material-fifo-actions"><label className="operation-field"><span>Резервная ставка сырья, ₽/ед.</span><input min="0.01" step="0.01" type="number" value={openingRate} onChange={(event) => setOpeningRate(Number(event.target.value))} /></label><button className="secondary-button" disabled={busy !== null || !materialInventory.length} type="button" onClick={() => void saveMaterials()}>{busy === "material" ? "Сохраняем" : "Сохранить остатки"}</button><button className="primary-button" disabled={busy !== null || !materialInventory.length} type="button" onClick={() => void initializeMaterials()}>{busy === "material" ? "Обновляем FIFO" : "Инициализировать FIFO"}</button></div>
        </div>
        {materialInventory.length ? <div className="material-inventory-list">{materialInventory.map((item) => { const key = item.material_key || item.material_name.toLowerCase(); const fifo = materialFifo.find((row) => (row.material_key || row.material_name.toLowerCase()) === key); const update = (patch: Partial<MaterialInventory>) => setMaterialInventory((rows) => rows.map((row) => row.material_name === item.material_name ? { ...row, ...patch } : row)); return <div className="material-inventory-row" key={key}><div className="cost-setting-product"><strong>{item.material_name}</strong><span>{fifo ? `Физически ${Number(fifo.physical_meters).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} ${item.unit || "ед."} · FIFO ${Number(fifo.fifo_meters).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} ${item.unit || "ед."} · разница ${Number(fifo.difference_meters).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} ${item.unit || "ед."}` : "FIFO-слой ещё не создан"}</span></div><label className="compact-field"><span>Упаковки</span><input min="0" type="number" value={item.full_rolls} onChange={(event) => update({ full_rolls: Number(event.target.value) })} /></label><label className="compact-field"><span>Остаток, {item.unit || "ед."}</span><input min="0" step="0.1" type="number" value={item.partial_meters} onChange={(event) => update({ partial_meters: Number(event.target.value) })} /></label><label className="compact-field"><span>В упаковке, {item.unit || "ед."}</span><input min="0.1" step="0.1" type="number" value={item.roll_length} disabled={item.tracking_mode === "quantity"} onChange={(event) => update({ roll_length: Number(event.target.value) })} /></label><label className="compact-field"><span>Единица</span><input aria-label={`Единица ${item.material_name}`} maxLength={16} value={item.unit} onChange={(event) => update({ unit: event.target.value })} /></label><label className="compact-field"><span>Ставка, ₽/{item.unit || "ед."}</span><input type="number" min="0" step="0.01" value={item.opening_rate_rub} onChange={(event) => update({ opening_rate_rub: Number(event.target.value) })} /></label><label className="compact-field"><span>Учёт</span><select value={item.tracking_mode} onChange={(event) => update({ tracking_mode: event.target.value as MaterialInventory["tracking_mode"] })}><option value="packaged">Упаковки</option><option value="quantity">Количество</option></select></label><label className="checkbox-field material-known"><input type="checkbox" checked={Boolean(item.balance_known)} onChange={(event) => update({ balance_known: event.target.checked })} /> Подтверждён</label></div>; })}</div> : <p className="plan-empty">Материалы пока не загружены.</p>}
      </section>
      <section className="work-section" id="production-norms">
        <SectionHeader kicker="Производство" title="Нормы материала"><Factory size={18} /></SectionHeader>
        <div className="operation-actions">
          <p className="plan-empty">Расход указан на комплект. Эти нормы используются при расчёте производственного плана.</p>
        </div>
        {productionNorms.length ? <div className="production-norms-list">{productionNorms.map((item) => <div className="production-norm-row" key={item.nm_id}><div className="cost-setting-product"><strong>{item.product_name || "Товар без названия"}</strong><span>WB {item.nm_id} · {item.supplier_article}</span></div><div className="production-norm-values"><span>Расход <strong>{Number(item.material_per_unit).toLocaleString("ru-RU", { maximumFractionDigits: 3 })} {item.material_unit || "ед."}/компл.</strong></span><span>Партия <strong>{item.min_batch} компл.</strong></span><span>В комплекте <strong>{item.pack_size} шт.</strong></span></div><span className={item.enabled ? "section-tag" : "section-tag muted-tag"}>{item.enabled ? "Активно" : "Выключено"}</span><button className="icon-button" type="button" aria-label="Изменить норму" title="Изменить норму" onClick={() => setEditingNorm(item)}><Pencil size={17} /></button></div>)}</div> : <p className="plan-empty">Нормы производства пока не заданы.</p>}
      </section>
      <section className="work-section" id="product-costs">
        <SectionHeader kicker="Экономика" title="Себестоимость товаров"><WalletCards size={18} /></SectionHeader>
        <div className="operation-actions">
          <p className="plan-empty">Себестоимость одной продаваемой единицы WB. Она используется в расчёте маржи и прибыли; изменение не создаёт движения товаров и не меняет исторические FIFO-слои.</p>
          <label className="search-box product-search"><Search size={17} /><input value={costQuery} onChange={(event) => setCostQuery(event.target.value)} placeholder="Артикул или товар" type="search" /></label>
        </div>
        {productCosts.length ? <div className="cost-settings-list">{productCosts.filter((item) => `${item.product_name} ${item.supplier_article} ${item.nm_id}`.toLowerCase().includes(costQuery.trim().toLowerCase())).map((item) => <div className="cost-setting-row" key={item.nm_id}><div className="cost-setting-product"><strong>{item.product_name || "Товар без названия"}</strong><span>WB {item.nm_id}{item.supplier_article ? ` · ${item.supplier_article}` : ""}</span></div><div className="cost-setting-amount"><span>Себестоимость</span><strong>{Number(item.cost_per_wb_unit).toLocaleString("ru-RU")} ₽</strong></div><button className="icon-button cost-edit-button" disabled={busy !== null} type="button" aria-label={`Изменить себестоимость: ${item.product_name || item.nm_id}`} title="Изменить себестоимость" onClick={() => setEditingCost(item)}><Pencil size={17} /></button></div>)}</div> : <p className="plan-empty">Каталог товаров ещё не загружен. После синхронизации здесь появятся карточки WB.</p>}
      </section>
      <section className="work-section" id="manual-expenses">
        <SectionHeader kicker="Экономика" title="Дополнительные расходы"><WalletCards size={18} /></SectionHeader>
        <p className="plan-empty">Ручные суммы сохраняются отдельно. По умолчанию они не уменьшают результат: база уже включает себестоимость, расходы WB и данные WB-рекламы. Включайте строку только после проверки, что её нет в этих источниках.</p>
        <form className="settings-form" onSubmit={(event) => { event.preventDefault(); void saveExpense(event.currentTarget); }}>
          <label className="operation-field"><span>Товар</span>{productCosts.length ? <select name="nm_id" required defaultValue=""><option value="" disabled>Выберите товар</option>{productCosts.map((item) => <option key={item.nm_id} value={item.nm_id}>{item.product_name || item.supplier_article || item.nm_id} · WB {item.nm_id}</option>)}</select> : <input name="nm_id" required min="1" type="number" placeholder="Артикул WB" />}</label>
          <label className="operation-field"><span>Статья</span><select name="article" defaultValue="packaging_labor"><option value="purchase_production">Закупка / производство</option><option value="logistics_returns">Логистика и возвраты</option><option value="storage">Хранение</option><option value="acceptance">Приёмка</option><option value="advertising">Реклама</option><option value="packaging_labor">Упаковка и труд</option><option value="defects_losses">Брак и потери</option><option value="taxes">Налоги</option><option value="wb_commission">Комиссия WB</option></select></label>
          <label className="operation-field"><span>Сумма, ₽</span><input name="amount_rub" min="0" required step="0.01" type="number" /></label>
          <label className="operation-field"><span>База распределения</span><select name="allocation_basis" defaultValue="per_unit"><option value="per_unit">На единицу</option><option value="total">Итого за период</option><option value="revenue_pct">Процент выручки</option></select></label>
          <label className="operation-field"><span>Период от</span><input name="period_start" required type="date" /></label>
          <label className="operation-field"><span>Период до</span><input name="period_end" required type="date" /></label>
          <label className="operation-field"><span>Примечание</span><input name="note" placeholder="Источник или комментарий" /></label>
          <label className="checkbox-field"><input name="include_in_management_result" type="checkbox" /><span>Подтверждаю: сумма отсутствует в данных WB, WB-рекламе и себестоимости; включить её в управленческий результат</span></label>
          <button className="primary-button" disabled={busy !== null} type="submit">{busy === "expense" ? "Сохраняем" : "Добавить расход"}</button>
        </form>
        {manualExpenses.length ? <div className="cost-settings-list">{manualExpenses.map((item) => <div className="cost-setting-row" key={item.id}><div className="cost-setting-product"><strong>{item.article}</strong><span>WB {item.nm_id} · {item.period_start} — {item.period_end} · источник: {item.source === "manual" ? "вручную" : "импорт"} · {item.article === "wb_commission" ? "исключён: комиссия уже в выплате WB" : item.include_in_management_result ? "включён в результат после подтверждения" : "исключён: требуется проверить отсутствие в WB, рекламе и себестоимости"}</span></div><div className="cost-setting-amount"><span>{item.allocation_basis}</span><strong>{Number(item.amount_rub).toLocaleString("ru-RU")} ₽</strong></div>{item.article !== "wb_commission" ? <button className="ghost-button" disabled={busy !== null} type="button" onClick={() => void toggleExpenseInclusion(item)}>{item.include_in_management_result ? "Исключить из результата" : "Включить в результат"}</button> : <span className="muted-tag">Комиссия WB уже учтена</span>}<button className="ghost-button" disabled={busy !== null} type="button" onClick={() => void calculateManagement(item)}>Рассчитать</button><button className="ghost-button" disabled={busy !== null} type="button" onClick={() => void removeExpense(item.id)}>Удалить</button></div>)}</div> : <p className="plan-empty">Ручных расходов пока нет.</p>}
        <LazyPanel label="ручные расходы"><ManualStoreExpenseManager /></LazyPanel>
        <LazyPanel label="покрытие расходов"><ExpenseCoveragePanel products={productCosts} /></LazyPanel>
        <LazyPanel label="налоги"><TaxSettingsPanel /></LazyPanel>
        <a className="secondary-button" href="?view=finance#tax-reconciliation">Финансы → Налоги: расчёт, сверка и история</a>
        {managementEconomics ? <div className="operation-actions"><strong>Управленческий результат · WB {managementEconomics.nm_id}</strong>{managementEconomics.data_status !== "ok" ? <span className="warning-text">По этому SKU нет данных продаж за выбранный период.</span> : null}<span>Прибыль по учётным данным: {managementEconomics.accounting_profit_rub.toLocaleString("ru-RU")} ₽ → после подтверждённых дополнительных расходов: {managementEconomics.management_profit_rub.toLocaleString("ru-RU")} ₽</span><span>Включено вручную: {managementEconomics.manual_expenses_rub.toLocaleString("ru-RU")} ₽ · исключено без подтверждения: {managementEconomics.excluded_manual_expenses_rub.toLocaleString("ru-RU")} ₽ · комиссия WB всегда исключена из повторного вычитания.</span>{managementEconomics.items.filter((item) => item.excluded_from_result).map((item) => <span className="muted-tag" key={item.id}>{item.article}: {item.exclusion_reason}</span>)}</div> : null}
      </section>
</>}
        backups={<>      <section className="work-section">
        <SectionHeader kicker="Обслуживание" title="Резервные копии"><ShieldCheck size={18} /></SectionHeader>
        <div className="operation-actions">
          <p className="plan-empty">Копируется база кабинета и настройки без токенов. Хранится до 14 последних копий.</p>
          <button className="secondary-button" disabled={!canManage || busy !== null} type="button" onClick={() => void createBackup()}><Download size={17} />{busy === "backup" ? "Создаём копию" : "Создать резервную копию"}</button>
        </div>
        {canManage ? <LazyPanel label="резервные копии"><BackupHistory backups={backups} /></LazyPanel> : <p className="plan-empty">Резервные копии доступны владельцу кабинета.</p>}
      </section>
</>}
      /></LazyPanel>
      {editingCost ? <ProductCostDialog busy={busy === "cost"} item={editingCost} onClose={() => setEditingCost(null)} onSubmit={(item, reason) => { void saveCost(item, reason).then((saved) => { if (saved) setEditingCost(null); }); }} /> : null}
      {editingNorm ? <ProductionNormDialog busy={busy === "cost"} item={editingNorm} onClose={() => setEditingNorm(null)} onSubmit={(item, reason) => { void saveNorm(item, reason); }} /> : null}
    </div>
  );
}

function ProductCostDialog({
  busy,
  item,
  onClose,
  onSubmit,
}: {
  busy: boolean;
  item: ProductCostSetting;
  onClose: () => void;
  onSubmit: (item: ProductCostSetting, reason?: string) => void;
}) {
  const dialogRef=useDialogFocus(()=>{if(!busy)onClose();});
  const [reason, setReason] = useState("");
  const [cost, setCost] = useState(String(item.cost_per_wb_unit));
  const [note, setNote] = useState(item.note);
  const title = item.product_name || item.supplier_article || `WB ${item.nm_id}`;
  return <div className="drawer-backdrop operation-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onClose(); }}>
    <section className="plan-drawer operation-dialog" role="dialog" aria-modal="true" ref={dialogRef} aria-labelledby="cost-dialog-title">
      <div className="drawer-header"><div><span className="eyebrow">экономика</span><h2 id="cost-dialog-title">Себестоимость товара</h2><p>{title} · WB {item.nm_id}</p></div><button className="icon-button" type="button" aria-label="Закрыть" disabled={busy} onClick={onClose}><X size={18} /></button></div>
      <form className="operation-form" onSubmit={(event) => { event.preventDefault(); onSubmit({ ...item, cost_per_wb_unit: Number(cost), note }, reason); }}>
        <label>Себестоимость одной единицы, ₽<input autoFocus min="0" required step="0.01" type="number" value={cost} onChange={(event) => setCost(event.target.value)} /></label>
        <label>Примечание<input value={note} onChange={(event) => setNote(event.target.value)} placeholder="Необязательно" /></label>
        <p className="plan-empty">Изменение применяется к расчёту маржи и прибыли. Исторические FIFO-слои не меняются.</p>
        <label>Причина изменения<textarea value={reason} maxLength={1000} onChange={(event) => setReason(event.target.value)} placeholder="Например, новая закупочная цена или уточнение расхода. Необязательно." /></label>
        <div className="operation-actions"><button className="ghost-button" disabled={busy} type="button" onClick={onClose}>Отмена</button><button className="primary-button" disabled={busy} type="submit"><CheckCircle2 size={16} />{busy ? "Сохраняем" : "Сохранить"}</button></div>
      </form>
    </section>
  </div>;
}

function ProductionNormDialog({ busy, item, onClose, onSubmit }: { busy: boolean; item: ProductionNorm; onClose: () => void; onSubmit: (item: ProductionNorm, reason?: string) => void }) {
  const dialogRef=useDialogFocus(()=>{if(!busy)onClose();});
  const [reason, setReason] = useState("");
  const [material, setMaterial] = useState(String(item.material_per_unit));
  const [batch, setBatch] = useState(String(item.min_batch));
  const [pack, setPack] = useState(String(item.pack_size));
  const [enabled, setEnabled] = useState(Boolean(item.enabled));
  return <div className="drawer-backdrop operation-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onClose(); }}>
    <section className="plan-drawer operation-dialog" role="dialog" aria-modal="true" ref={dialogRef} aria-labelledby="norm-dialog-title">
      <div className="drawer-header"><div><span className="eyebrow">производство</span><h2 id="norm-dialog-title">Норма материала</h2><p>{item.product_name || item.supplier_article} · WB {item.nm_id}</p></div><button className="icon-button" type="button" aria-label="Закрыть" disabled={busy} onClick={onClose}><X size={18} /></button></div>
      <form className="operation-form" onSubmit={(event) => { event.preventDefault(); onSubmit({ ...item, material_per_unit: Number(material), min_batch: Number(batch), pack_size: Number(pack), enabled: enabled ? 1 : 0 }, reason); }}>
        <label>Расход на комплект, {item.material_unit || "ед."}<input required min="0" step="0.001" type="number" value={material} onChange={(event) => setMaterial(event.target.value)} /></label>
        <label>Минимальная партия, комплектов<input required min="1" step="1" type="number" value={batch} onChange={(event) => setBatch(event.target.value)} /></label>
        <label>Штук в комплекте<input required min="1" step="1" type="number" value={pack} onChange={(event) => setPack(event.target.value)} /></label>
        <label className="checkbox-field"><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} /> Использовать в производственном плане</label>
        <label>Причина изменения<textarea value={reason} maxLength={1000} onChange={(event) => setReason(event.target.value)} placeholder="Например, новая закупочная цена или уточнение расхода. Необязательно." /></label>
        <div className="operation-actions"><button className="ghost-button" disabled={busy} type="button" onClick={onClose}>Отмена</button><button className="primary-button" disabled={busy} type="submit"><CheckCircle2 size={16} />{busy ? "Сохраняем" : "Сохранить норму"}</button></div>
      </form>
    </section>
  </div>;
}
