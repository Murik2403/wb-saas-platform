import { afterEach, describe, expect, it, vi } from "vitest";

import { confirmSupplyPlan, previewSupplyPlan, SupplyPlanHttpError } from "./data-source";

const response = (body: unknown, ok = true, status = 200) => ({
  ok,
  status,
  json: async () => body,
  text: async () => JSON.stringify(body),
}) as Response;

afterEach(() => vi.unstubAllGlobals());

describe("supply planning data-source contract", () => {
  it("posts typed preview fields to the read-only preview route", async () => {
    const fetchMock = vi.fn().mockResolvedValue(response({
      start_date: "2026-09-01", end_date: "2026-09-30", preview_fingerprint: "a".repeat(64),
      lines: [], blockers: [], warnings: [], variant_demands: [],
    }));
    vi.stubGlobal("fetch", fetchMock);
    const input = { start_date: "2026-09-01", end_date: "2026-09-30", variant_allocations: [{ task_key: "job:1", chrt_id: 101, units: 2 }] };
    const result = await previewSupplyPlan(input);
    expect(fetchMock).toHaveBeenCalledWith("/tenant-api/api/v1/supply-planning/preview", {
      method: "POST",
      headers: expect.objectContaining({ "content-type": "application/json", "x-tenant-id": expect.any(String) }),
      body: JSON.stringify(input),
    });
    expect(result.preview_fingerprint).toHaveLength(64);
    expect(result.lines).toEqual([]);
  });

  it("posts exact confirmation identity and parses structured API errors", async () => {
    const input = {
      confirmation_key: "00000000-0000-4000-8000-000000000001", preview_fingerprint: "b".repeat(64),
      start_date: "2026-09-01", end_date: "2026-09-30", variant_allocations: [],
      edits: [{ material_key: "fabric-a", unit: "м", quantity: 2, supplier_id: 7, expected_date: "2026-09-10" }],
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ confirmation_key: input.confirmation_key, order_ids: [4], order_numbers: ["ЗАК-4"], replayed: false }))
      .mockResolvedValue(response({ detail: { code: "stale_preview", message: "Данные изменились." } }, false, 409));
    vi.stubGlobal("fetch", fetchMock);
    const result = await confirmSupplyPlan(input);
    expect(fetchMock).toHaveBeenNthCalledWith(1, "/tenant-api/api/v1/supply-planning/confirm", expect.objectContaining({ method: "POST", body: JSON.stringify(input) }));
    expect(result.order_ids).toEqual([4]);
    expect(result.replayed).toBe(false);
    await expect(previewSupplyPlan({ start_date: input.start_date, end_date: input.end_date, variant_allocations: [] })).rejects.toBeInstanceOf(SupplyPlanHttpError);
    try {
      await confirmSupplyPlan(input);
      throw new Error("Expected structured stale-preview error");
    } catch (error) {
      expect(error).toBeInstanceOf(SupplyPlanHttpError);
      expect(error).toMatchObject({ status: 409, code: "stale_preview", message: "Данные изменились." });
    }
  });
});
