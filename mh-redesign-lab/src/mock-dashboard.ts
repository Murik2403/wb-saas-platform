import type {
  ActionRecommendation,
  AdCampaign,
  AdsHero,
  AssistantAutomation,
  CreativeSignal,
  DashboardMetric,
  DashboardSync,
  ExpenseSplit,
  ExportItem,
  FinanceLedgerItem,
  FunnelStage,
  InventoryWatchRow,
  MaterialNeed,
  NavItem,
  PageId,
  PageMeta,
  ProductionStage,
  ProductionTask,
  ReportCard,
  ReorderItem,
  SupplierPulse,
  SupplyShipment,
  StockCoverageRow,
  StockHero,
  StockRule,
} from "./contracts";

export const navItems: NavItem[] = [
  { id: "today", label: "Сегодня", iconKey: "dashboard", count: 7 },
  { id: "stock", label: "Склад", iconKey: "stock", count: 3 },
  { id: "ads", label: "Реклама", iconKey: "ads", count: 2 },
  { id: "finance", label: "Финансы", iconKey: "finance" },
  { id: "production", label: "Производство", iconKey: "production" },
  { id: "supply", label: "Поставки", iconKey: "supply" },
  { id: "assistant", label: "AI-агенты", iconKey: "assistant" },
  { id: "reports", label: "Отчеты", iconKey: "reports" },
  { id: "products", label: "Товары", iconKey: "products" },
  { id: "control", label: "Контроль", iconKey: "control" },
  { id: "settings", label: "Настройки", iconKey: "settings" },
];

export const pageMeta: Record<PageId, PageMeta> = {
  today: {
    section: "Сегодня",
    title: "Контроль продаж и действий",
    search: "SKU, товар, кампания",
  },
  stock: {
    section: "Склад",
    title: "Остатки и план пополнения",
    search: "SKU, склад, поставка",
  },
  ads: {
    section: "Реклама",
    title: "Ставки, ACoS и тесты",
    search: "Кампания, ключ, SKU",
  },
  finance: {
    section: "Финансы",
    title: "Деньги, маржа и выплаты",
    search: "Период, статья, заказ",
  },
  production: {
    section: "Производство",
    title: "Партии, сроки и готовность",
    search: "Партия, цех, товар",
  },
  supply: {
    section: "Поставки",
    title: "Логистика и окна приемки",
    search: "Поставка, склад, статус",
  },
  assistant: {
    section: "AI-агенты",
    title: "AI-агенты ChatGPT и Claude",
    search: "Сценарий, задача, SKU",
  },
  reports: {
    section: "Отчеты",
    title: "Сводки и экспорт",
    search: "Отчет, метрика, период",
  },
  products: {
    section: "Товары",
    title: "Экономика по артикулам",
    search: "Артикул, товар, SKU",
  },
  control: {
    section: "Контроль",
    title: "Качество данных и расчётов",
    search: "Проверка, модуль, риск",
  },
  settings: {
    section: "Настройки",
    title: "Подключения и параметры",
    search: "Параметр, подключение, интервал",
  },
};

export const dashboardSync: DashboardSync = {
  state: "fresh",
  source: "mock",
  updatedAt: "2026-08-30T14:20:00+03:00",
  lagMinutes: 11,
};

export const todayMetrics: DashboardMetric[] = [
  {
    label: "Выручка",
    value: "1,84 млн ₽",
    delta: "+18%",
    detail: "к среде прошлой недели",
    tone: "good",
  },
  {
    label: "Маржа",
    value: "31,6%",
    delta: "+2,4 п.п.",
    detail: "после рекламы и логистики",
    tone: "good",
  },
  {
    label: "Критичные SKU",
    value: "7",
    delta: "3 срочно",
    detail: "нужны решения до 16:00",
    tone: "danger",
  },
  {
    label: "Реклама",
    value: "9,8%",
    delta: "ACoS",
    detail: "2 кампании выше лимита",
    tone: "warn",
  },
];

export const stockMetrics: DashboardMetric[] = [
  {
    label: "Товар в продаже",
    value: "8,4 млн ₽",
    delta: "+11%",
    detail: "по розничной стоимости",
    tone: "good",
  },
  {
    label: "Дней запаса",
    value: "12,6",
    delta: "-2,1 дня",
    detail: "ускорились продажи",
    tone: "warn",
  },
  {
    label: "Дефицит",
    value: "14 SKU",
    delta: "5 критично",
    detail: "потеря продаж завтра",
    tone: "danger",
  },
  {
    label: "В пути",
    value: "1 280 шт.",
    delta: "3 поставки",
    detail: "ближайшая приемка 31 августа",
    tone: "info",
  },
];

export const adsMetrics: DashboardMetric[] = [
  {
    label: "Расход",
    value: "181 400 ₽",
    delta: "-6%",
    detail: "после чистки ставок",
    tone: "good",
  },
  {
    label: "ACoS",
    value: "9,8%",
    delta: "лимит 11%",
    detail: "в зеленой зоне",
    tone: "good",
  },
  {
    label: "ROAS",
    value: "10,2x",
    delta: "+1,1x",
    detail: "к предыдущему дню",
    tone: "good",
  },
  {
    label: "Проблемы",
    value: "2",
    delta: "перерасход",
    detail: "нужна корректировка",
    tone: "warn",
  },
];

export const actions: ActionRecommendation[] = [
  {
    id: "a1",
    sku: "WB-493-22",
    title: "Костюм спортивный графит",
    issue: "Остаток на 1,8 дня, карточка держит темп",
    impact: "+73 000 ₽",
    owner: "Закупить 80 шт.",
    due: "до 16:00",
    tone: "danger",
    iconKey: "packageX",
  },
  {
    id: "a2",
    sku: "WB-118-07",
    title: "Футболка oversize молочная",
    issue: "CTR просел после смены главного фото",
    impact: "+41 000 ₽",
    owner: "Вернуть фото A",
    due: "сегодня",
    tone: "warn",
    iconKey: "ads",
  },
  {
    id: "a3",
    sku: "WB-820-14",
    title: "Пижама хлопок, 4 цвета",
    issue: "Маржа растет, есть запас по ставке",
    impact: "+29 000 ₽",
    owner: "Поднять бид на 7%",
    due: "2 часа",
    tone: "good",
    iconKey: "arrowUpRight",
  },
];

export const inventory: InventoryWatchRow[] = [
  {
    sku: "493-22",
    name: "Костюм спортивный графит",
    stock: "1,8 дня",
    margin: "34%",
    signal: "поставка",
    tone: "danger",
  },
  {
    sku: "118-07",
    name: "Футболка oversize молочная",
    stock: "9 дней",
    margin: "27%",
    signal: "контент",
    tone: "warn",
  },
  {
    sku: "820-14",
    name: "Пижама хлопок",
    stock: "18 дней",
    margin: "42%",
    signal: "масштаб",
    tone: "good",
  },
  {
    sku: "702-30",
    name: "Лонгслив rib knit",
    stock: "6 дней",
    margin: "19%",
    signal: "цена",
    tone: "info",
  },
];

export const reorderPlan: ReorderItem[] = [
  {
    sku: "493-22",
    name: "Костюм спортивный графит",
    days: "1,8 дня",
    qty: "80 шт.",
    warehouse: "Коледино",
    risk: "потеря 73 000 ₽",
    tone: "danger",
  },
  {
    sku: "702-30",
    name: "Лонгслив rib knit",
    days: "6 дней",
    qty: "140 шт.",
    warehouse: "Электросталь",
    risk: "маржа ниже цели",
    tone: "warn",
  },
  {
    sku: "820-14",
    name: "Пижама хлопок",
    days: "18 дней",
    qty: "0 шт.",
    warehouse: "Казань",
    risk: "можно ускорять рекламу",
    tone: "good",
  },
];

export const stockHero: StockHero = {
  primary: "5 SKU уходят в ноль быстрее прогноза",
  primaryNote: "Приоритет: закрыть дефицит по Коледино и не расширять рекламу на товары с запасом меньше 4 дней.",
  secondary: "1 280 шт.",
  secondaryNote: "Ближайшее окно приемки: 31 августа, 10:00-14:00.",
  tone: "danger",
};

export const stockCoverage: StockCoverageRow[] = [
  { label: "Коледино", detail: "доля покрытия", value: "37%", width: 37, tone: "danger" },
  { label: "Электросталь", detail: "доля покрытия", value: "58%", width: 58, tone: "warn" },
  { label: "Казань", detail: "доля покрытия", value: "86%", width: 86, tone: "good" },
];

export const stockRules: StockRule[] = [
  {
    id: "auto-reorder-under-5-days",
    title: "Заказывать при запасе меньше 5 дней",
    description: "Исключение: товары с маржей ниже 20% отправлять в ручное решение.",
  },
];

export const campaigns: AdCampaign[] = [
  {
    name: "Поиск / костюмы / авто",
    spend: "63 200 ₽",
    acos: "14,8%",
    revenue: "427 000 ₽",
    signal: "выше лимита",
    action: "Снизить ставки",
    tone: "warn",
  },
  {
    name: "Каталог / пижама / ручная",
    spend: "28 900 ₽",
    acos: "6,1%",
    revenue: "474 000 ₽",
    signal: "масштаб",
    action: "Добавить бюджет",
    tone: "good",
  },
  {
    name: "Ретаргет / футболки",
    spend: "19 700 ₽",
    acos: "18,5%",
    revenue: "106 000 ₽",
    signal: "стоп-тест",
    action: "Остановить",
    tone: "danger",
  },
];

export const adsHero: AdsHero = {
  title: "Бюджет переносим из перерасхода в масштаб",
  description: "Снижаем ставки в поиске по костюмам и добавляем бюджет на пижамы, где ACoS держится в 6,1%.",
  tone: "info",
};

export const adsFunnel: FunnelStage[] = [
  { label: "Показы", value: "184k", width: 100 },
  { label: "Клики", value: "7,8k", width: 68 },
  { label: "Корзины", value: "1,2k", width: 34 },
  { label: "Заказы", value: "486", width: 22 },
];

export const creativeSignals: CreativeSignal[] = [
  { label: "Фото A", value: "CTR 4,9%", active: true },
  { label: "Фото B", value: "CTR 3,1%", active: false },
];

export const financeMetrics: DashboardMetric[] = [
  { label: "Прибыль", value: "738 977 ₽", delta: "-12%", detail: "расчетная по товарам", tone: "warn" },
  { label: "Выручка", value: "1 682 866 ₽", delta: "+8%", detail: "оперативные продажи", tone: "good" },
  { label: "Реклама", value: "36 354 ₽", delta: "ДРР 2,2%", detail: "расход за период", tone: "good" },
  { label: "К выплате", value: "1,12 млн ₽", delta: "прогноз", detail: "после комиссий и логистики", tone: "info" },
];

export const financeLedger: FinanceLedgerItem[] = [
  { label: "Продажи WB", amount: "+1 682 866 ₽", detail: "оперативная выручка", tone: "good" },
  { label: "Реклама", amount: "-36 354 ₽", detail: "кампании за период", tone: "warn" },
  { label: "Закупки", amount: "-214 000 ₽", detail: "активные заявки", tone: "info" },
  { label: "Маржинальный остаток", amount: "+738 977 ₽", detail: "расчетная прибыль", tone: "good" },
];

export const expenseSplit: ExpenseSplit[] = [
  { label: "Себестоимость", value: "42%", width: 42, tone: "info" },
  { label: "Реклама", value: "2%", width: 12, tone: "good" },
  { label: "Комиссии и логистика", value: "24%", width: 24, tone: "warn" },
];

export const productionMetrics: DashboardMetric[] = [
  { label: "План смены", value: "320", delta: "компл.", detail: "к отгрузке завтра", tone: "info" },
  { label: "Готово", value: "218", delta: "68%", detail: "по текущей партии", tone: "warn" },
  { label: "Материалы", value: "91%", delta: "покрытие", detail: "по нормам сырья", tone: "good" },
  { label: "Блокеры", value: "2", delta: "требуют решения", detail: "сырье и упаковка", tone: "danger" },
];

export const productionStages: ProductionStage[] = [
  { label: "Крой", value: "готово", status: "done" },
  { label: "Пошив", value: "68%", status: "active" },
  { label: "Упаковка", value: "завтра", status: "pending" },
  { label: "Маркировка", value: "ожидает", status: "pending" },
];

export const productionTasks: ProductionTask[] = [
  { sku: "493-22", title: "Костюм спортивный графит", plan: "180", actual: "124", status: "в работе", tone: "warn" },
  { sku: "820-14", title: "Пижама хлопок", plan: "90", actual: "90", status: "готово", tone: "good" },
  { sku: "702-30", title: "Лонгслив rib knit", plan: "50", actual: "4", status: "материал", tone: "danger" },
];

export const materialNeeds: MaterialNeed[] = [
  { name: "Футер графит", need: "126 м", stock: "88 м", status: "докупить", tone: "danger" },
  { name: "Рибана молочная", need: "42 м", stock: "64 м", status: "достаточно", tone: "good" },
  { name: "Пакеты zip", need: "320 шт.", stock: "280 шт.", status: "на контроле", tone: "warn" },
];

export const supplyMetrics: DashboardMetric[] = [
  { label: "В пути", value: "1 280 шт.", delta: "3 поставки", detail: "ближайшее окно завтра", tone: "info" },
  { label: "Просрочка", value: "1", delta: "поставщик", detail: "нужен контакт", tone: "warn" },
  { label: "К оплате", value: "214 000 ₽", delta: "2 счета", detail: "до конца недели", tone: "warn" },
  { label: "Риск склада", value: "5 SKU", delta: "Коледино", detail: "падает покрытие", tone: "danger" },
];

export const supplyShipments: SupplyShipment[] = [
  { id: "PO-1042", supplier: "Текстиль Север", status: "в пути", eta: "31 авг", amount: "128 000 ₽", tone: "info" },
  { id: "PO-1038", supplier: "Упаковка Про", status: "к оплате", eta: "сегодня", amount: "42 000 ₽", tone: "warn" },
  { id: "PO-1035", supplier: "Швейный цех", status: "задержка", eta: "+2 дня", amount: "44 000 ₽", tone: "danger" },
];

export const supplierPulse: SupplierPulse[] = [
  { name: "Текстиль Север", metric: "96%", detail: "сроки поставок", tone: "good" },
  { name: "Упаковка Про", metric: "81%", detail: "закрытие счетов", tone: "warn" },
  { name: "Швейный цех", metric: "74%", detail: "готовность партий", tone: "danger" },
];

export const assistantMetrics: DashboardMetric[] = [
  { label: "Решения", value: "7", delta: "3 срочно", detail: "в очереди агента", tone: "danger" },
  { label: "Автоправила", value: "12", delta: "9 активны", detail: "контроль без ручной рутины", tone: "good" },
  { label: "Экономия", value: "4,6 ч", delta: "за неделю", detail: "оценка автоматизации", tone: "info" },
  { label: "Проверки", value: "2", delta: "ждут", detail: "нужен человек", tone: "warn" },
];

export const automations: AssistantAutomation[] = [
  { title: "Дефицит SKU", detail: "Создать задачу при запасе ниже 7 дней", status: "активно", tone: "good" },
  { title: "ДРР выше цели", detail: "Предложить снижение ставок после 11%", status: "активно", tone: "good" },
  { title: "Сверка FIFO", detail: "Остановить списания при расхождениях WB", status: "ручное подтверждение", tone: "warn" },
];

export const reportMetrics: DashboardMetric[] = [
  { label: "Готовые отчеты", value: "6", delta: "2 новых", detail: "для собственника", tone: "good" },
  { label: "Период", value: "30 дней", delta: "по умолчанию", detail: "сравнение с прошлым", tone: "info" },
  { label: "Экспорт", value: "XLSX/PDF", delta: "готово", detail: "финансы и товары", tone: "good" },
  { label: "Аномалии", value: "3", delta: "в отчете", detail: "требуют пояснения", tone: "warn" },
];

export const reportCards: ReportCard[] = [
  { title: "Сводка собственника", detail: "Прибыль, выручка, ДРР, проблемные товары", period: "30 дней", status: "готово", tone: "good" },
  { title: "Остатки и закупки", detail: "Дефицит, план пополнения, товары в риске", period: "сегодня", status: "обновляется", tone: "warn" },
  { title: "Реклама и ставки", detail: "ДРР, ROAS, кандидаты на бюджет", period: "30 дней", status: "готово", tone: "good" },
];

export const exportItems: ExportItem[] = [
  { name: "finance_30d.xlsx", format: "XLSX", updated: "сегодня", tone: "good" },
  { name: "stock_reorder.pdf", format: "PDF", updated: "сегодня", tone: "warn" },
  { name: "ads_actions.csv", format: "CSV", updated: "вчера", tone: "info" },
];

export const chartBars = [42, 57, 49, 66, 62, 78, 74, 91, 86, 96, 88, 104];
export const mobileNav: PageId[] = ["today", "stock", "ads", "finance"];
