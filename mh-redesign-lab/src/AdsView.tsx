import { WorkspaceTabs } from "./WorkspaceTabs";
import { BarChart3, Megaphone, Package, Plus, Sparkles } from "lucide-react";
import type { AdCampaign, AdsHero, CreativeSignal, DashboardMetric, FunnelStage } from "./contracts";
import { type DashboardPeriod } from "./data-source";
import { LazyPanel } from "./LazyPanel";
import { lazyNamed } from "./LazyPanel";
import { DashboardPeriodControl, MetricGrid, SectionHeader, Stat, cx } from "./cabinet-shared";
const AdsAnalyticsPanel = lazyNamed(() => import("./AdsAnalyticsPanel"), "AdsAnalyticsPanel");

const adsTabs = [{ id: "campaigns", label: "Кампании" }, { id: "analytics", label: "Аналитика РК" }] as const;
export function AdsView({
  adsFunnel,
  adsHero,
  adsMetrics,
  campaigns,
  creativeSignals,
  onAction,
  period,
  setPeriod,
  target,
}: {
  adsFunnel: FunnelStage[];
  adsHero: AdsHero;
  adsMetrics: DashboardMetric[];
  campaigns: AdCampaign[];
  creativeSignals: CreativeSignal[];
  onAction: (label: string, itemLabel: string) => void;
  period: DashboardPeriod;
  setPeriod: (period: DashboardPeriod) => void;
  target?: string | null;
}) {
  const campaignsPanel = (<>
      <section className={cx("hero-strip", "ads-hero", adsHero.tone)} aria-label="Рекламный сигнал">
        <div>
          <div className="eyebrow">
            <Megaphone size={16} />
            кампании под контролем
          </div>
          <h2>{adsHero.title}</h2>
          <p>{adsHero.description}</p>
        </div>
        <div className="hero-actions">
          <button className="secondary-button" type="button" onClick={() => onAction("Медиаплан", "реклама")}>
            <Plus size={17} />
            <span>В медиаплан</span>
          </button>
        </div>
      </section>

      <MetricGrid metrics={adsMetrics} onMetricClick={(metric) => onAction("Медиаплан", metric.label)} />

      <div className="content-grid">
        <section className="work-section" id="ads-campaigns">
          <SectionHeader kicker="Кампании" title="Где менять ставки">
            <DashboardPeriodControl period={period} setPeriod={setPeriod} />
          </SectionHeader>

          <div className="campaign-list">
            {campaigns.map((campaign) => (
              <CampaignRow campaign={campaign} key={campaign.name} onAction={onAction} />
            ))}
          </div>
        </section>

        <aside className="insight-column">
          <section className="panel" id="ads-funnel">
            <SectionHeader compact kicker="Воронка" title="Путь до заказа">
              <BarChart3 size={20} />
            </SectionHeader>
            <div className="funnel-list">
              {adsFunnel.map((stage) => (
                <FunnelBar key={stage.label} label={stage.label} value={stage.value} width={stage.width} onOpen={() => onAction("Медиаплан", stage.label)} />
              ))}
            </div>
          </section>

          <section className="panel" id="ads-signals">
            <SectionHeader compact kicker="Сигналы" title="Товары в ставках">
              <Sparkles size={20} />
            </SectionHeader>
            <div className="creative-grid">
              {creativeSignals.map((signal) => (
                <CreativeCard active={signal.active} key={signal.label} label={signal.label} value={signal.value} onOpen={() => onAction("Медиаплан", signal.label)} />
              ))}
            </div>
          </section>
        </aside>
      </div>
  </>);
  return (
    <div className="workspace-view">
      <WorkspaceTabs
        tabs={adsTabs}
        ariaLabel="Разделы рекламы"
        storageKey="mh.tab.ads"
        target={target}
        targetMap={{ "ads-campaigns": "campaigns", "ads-funnel": "campaigns", "ads-signals": "campaigns", "ads-analytics": "analytics" }}
        panels={{ campaigns: campaignsPanel, analytics: <LazyPanel label="аналитику рекламы"><AdsAnalyticsPanel period={period} /></LazyPanel> }}
      />
    </div>
  );
}

function CampaignRow({
  campaign,
  onAction,
}: {
  campaign: AdCampaign;
  onAction: (label: string, itemLabel: string) => void;
}) {
  return (
    <article className={cx("campaign-row", campaign.tone)}>
      <div>
        <span>{campaign.signal}</span>
        <h3>{campaign.name}</h3>
      </div>
      <div className="campaign-stats">
        <Stat label="Расход" value={campaign.spend} />
        <Stat label="ACoS" value={campaign.acos} />
        <Stat label="Выручка" value={campaign.revenue} />
      </div>
      <button className="secondary-button" type="button" onClick={() => onAction(campaign.action, campaign.name)}>
        {campaign.action}
      </button>
    </article>
  );
}

function FunnelBar({ label, onOpen, value, width }: { label: string; onOpen: () => void; value: string; width: number }) {
  return (
    <button className="funnel-bar detail-trigger" type="button" onClick={onOpen}>
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
      </div>
      <div>
        <span style={{ width: `${Math.max(width > 0 ? 4 : 0, Math.min(100,width))}%` }} />
      </div>
    </button>
  );
}

function CreativeCard({ active = false, label, onOpen, value }: { active?: boolean; label: string; onOpen: () => void; value: string }) {
  return (
    <button className={cx("creative-card", "detail-trigger", active && "active")} type="button" onClick={onOpen}>
      <div className="creative-thumb">
        <Package size={22} />
      </div>
      <strong>{label}</strong>
      <span>{value}</span>
    </button>
  );
}
