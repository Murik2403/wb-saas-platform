import { useCallback, useEffect, useRef, useState } from "react";
import { getProductGroups, type ProductGroup } from "./data-source";

const STORAGE_KEY = "mh-product-group-filter";
export const ALL_PRODUCTS = "all";

const readStored = (): string => {
  try { return window.localStorage.getItem(STORAGE_KEY) || ALL_PRODUCTS; } catch { return ALL_PRODUCTS; }
};

/** Groups from the API; a response older than the latest request is ignored. */
export function useProductGroups() {
  const [state, setState] = useState<{ groups: ProductGroup[]; loaded: boolean; error: string | null }>({ groups: [], loaded: false, error: null });
  const seq = useRef(0);
  const reload = useCallback(async () => {
    const mine = ++seq.current;
    const controller = new AbortController();
    try {
      const groups = await getProductGroups(controller.signal);
      if (mine === seq.current) setState({ groups, loaded: true, error: null });
    } catch (error) {
      if (mine === seq.current) setState((prev) => ({ ...prev, error: error instanceof Error ? error.message : "Не удалось загрузить группы" }));
    }
  }, []);
  useEffect(() => { void reload(); return () => { seq.current++; }; }, [reload]);
  return { ...state, reload };
}

/** Remembered choice. A group that no longer exists falls back to "all" (once the list has loaded). */
export function useGroupSelection(groups: ProductGroup[], loaded: boolean): [string, (id: string) => void] {
  const [stored, setStored] = useState(readStored);
  const set = useCallback((id: string) => {
    setStored(id);
    try { window.localStorage.setItem(STORAGE_KEY, id); } catch { /* storage unavailable: keep in memory */ }
  }, []);
  const known = stored === ALL_PRODUCTS || groups.some((g) => String(g.id) === stored);
  useEffect(() => { if (loaded && !known) set(ALL_PRODUCTS); }, [loaded, known, set]);
  return [known ? stored : ALL_PRODUCTS, set];
}
