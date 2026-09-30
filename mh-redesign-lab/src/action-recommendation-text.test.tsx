import { afterEach, expect, it, vi } from "vitest";
import { cleanup, renderHook, waitFor } from "@testing-library/react";
vi.hoisted(() => {
  vi.stubEnv("VITE_MH_API_MODE", "v1");
  vi.stubEnv("VITE_MH_API_BASE_URL", "");
});
import { useDashboardData } from "./data-source";
import { clearShopContext, selectShop } from "./selected-shop";

afterEach(() => { cleanup(); clearShopContext(); vi.unstubAllGlobals(); });

it("keeps the complete instruction from the API for the main signal", async () => {
  const instruction = "Обновить факт и статус либо перенести задание";
  selectShop({ role: "owner", selected_shop_id: 0, shops: [{ id: 0, name: "Primary", active: true,
    verified_identity: null, snapshot_revision: null, can_write: true, allowed_actions: [] }], route_scopes: [] }, 0);
  vi.stubGlobal("fetch", vi.fn(async (input) => new Response(JSON.stringify(
    String(input).includes("/dashboard/today") ? { actions: [{ id: "overdue-1", title: "Просрочено",
      sku: "SKU-1", issue: "Задание от 2026-09-10", recommended_action: instruction }] } : {}
  ), { headers: { "Content-Type": "application/json" } })));
  const period = { id: "30d" as const };
  const { result } = renderHook(() => useDashboardData("today", period));
  await waitFor(() => expect(result.current.refreshing).toBe(false));
  expect(result.current.actions[0]?.owner).toBe(instruction);
});
