import { useEffect, useState } from "react";
import { getAdsAnalytics, getProductsPrevious, getProductsTrend, type AdsAnalytics, type DashboardPeriod, type ProductsPreviousTotals, type ProductsTrend } from "./data-source";

type Lazy<T> = { data: T | null; loading: boolean; error: string | null };

/** Loads lazily after mount; a response for an outdated period is ignored. */
function useLazy<T>(period: DashboardPeriod, load: (period: DashboardPeriod, signal: AbortSignal) => Promise<T>): Lazy<T> {
  const [state, setState] = useState<Lazy<T>>({ data: null, loading: true, error: null });
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setState({ data: null, loading: true, error: null });
    load(period, controller.signal).then(
      (data) => { if (active) setState({ data, loading: false, error: null }); },
      (error: unknown) => { if (active) setState({ data: null, loading: false, error: error instanceof Error ? error.message : "error" }); },
    );
    return () => { active = false; controller.abort(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [period.id, period.start, period.end]);
  return state;
}

export const useProductsTrend = (period: DashboardPeriod) => useLazy<ProductsTrend>(period, getProductsTrend);
export const useProductsPrevious = (period: DashboardPeriod) => useLazy<ProductsPreviousTotals>(period, getProductsPrevious);
export const useAdsAnalytics = (period: DashboardPeriod) => useLazy<AdsAnalytics>(period, getAdsAnalytics);
