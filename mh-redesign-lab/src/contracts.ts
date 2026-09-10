export type PageId =
  | "today"
  | "stock"
  | "ads"
  | "finance"
  | "production"
  | "supply"
  | "assistant"
  | "reports"
  | "products"
  | "control"
  | "settings";

export type Tone = "good" | "warn" | "danger" | "info";

export type IconKey =
  | "dashboard"
  | "stock"
  | "ads"
  | "finance"
  | "production"
  | "supply"
  | "assistant"
  | "reports"
  | "products"
  | "control"
  | "settings"
  | "packageX"
  | "arrowUpRight";

export type NavItem = {
  id: PageId;
  label: string;
  iconKey: IconKey;
  count?: number;
};

export type PageMeta = {
  section: string;
  title: string;
  search: string;
};

export type DashboardMetric = {
  label: string;
  value: string;
  delta: string;
  detail: string;
  tone: Tone;
};

export type ActionRecommendation = {
  id: string;
  sku: string;
  title: string;
  issue: string;
  impact: string;
  owner: string;
  due: string;
  tone: Tone;
  iconKey: IconKey;
};

export type InventoryWatchRow = {
  sku: string;
  name: string;
  stock: string;
  margin: string;
  signal: string;
  tone: Tone;
};

export type ReorderItem = {
  sku: string;
  name: string;
  days: string;
  qty: string;
  warehouse: string;
  risk: string;
  tone: Tone;
};

export type StockHero = {
  primary: string;
  primaryNote: string;
  secondary: string;
  secondaryNote: string;
  tone: Tone;
};

export type StockCoverageRow = {
  label: string;
  detail: string;
  value: string;
  width: number;
  tone: Tone;
};

export type StockRule = {
  id: string;
  title: string;
  description: string;
};

export type AdCampaign = {
  name: string;
  spend: string;
  acos: string;
  revenue: string;
  signal: string;
  action: string;
  tone: Tone;
};

export type AdsHero = {
  title: string;
  description: string;
  tone: Tone;
};

export type FunnelStage = {
  label: string;
  value: string;
  width: number;
};

export type CreativeSignal = {
  label: string;
  value: string;
  active: boolean;
};

export type FinanceLedgerItem = {
  label: string;
  amount: string;
  detail: string;
  tone: Tone;
};

export type ExpenseSplit = {
  label: string;
  value: string;
  width: number;
  tone: Tone;
};

export type CashWindow = {
  label: string;
  value: string;
  detail: string;
};

export type ProductionStage = {
  label: string;
  value: string;
  status: "done" | "active" | "blocked" | "pending";
};

export type ProductionTask = {
  sku: string;
  title: string;
  plan: string;
  actual: string;
  status: string;
  tone: Tone;
};

export type MaterialNeed = {
  name: string;
  need: string;
  stock: string;
  status: string;
  tone: Tone;
};

export type SupplyShipment = {
  id: string;
  supplier: string;
  status: string;
  eta: string;
  amount: string;
  tone: Tone;
};

export type SupplierPulse = {
  name: string;
  metric: string;
  detail: string;
  tone: Tone;
};

export type AssistantAutomation = {
  title: string;
  detail: string;
  status: string;
  tone: Tone;
};

export type AgentCandidate = {
  id: number;
  status?: string;
  agent: string;
  marketplace: string;
  targetId: string;
  targetName: string;
  action: string;
  currentValue: string;
  proposedValue: string;
  reason: string;
};

export type ReportCard = {
  title: string;
  detail: string;
  period: string;
  status: string;
  tone: Tone;
};

export type ExportItem = {
  name: string;
  format: string;
  updated: string;
  tone: Tone;
};

export type SyncState = "fresh" | "stale" | "error";

export type DashboardSync = {
  state: SyncState;
  source: "mock" | "wb-api";
  updatedAt: string;
  lagMinutes: number;
};
