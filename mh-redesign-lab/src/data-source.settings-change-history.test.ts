import { afterEach, describe, expect, it, vi } from "vitest";
import { getSettingsChangeHistory } from "./data-source";

describe("settings change history adapter", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("serializes filters and paging into the read-only endpoint", async () => {
    const payload = { items: [], total: 0, limit: 25, offset: 25 };
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => payload });
    vi.stubGlobal("fetch", fetch);

    await expect(getSettingsChangeHistory({ kind: "production_norm", nm_id: 308044778, limit: 25, offset: 25 })).resolves.toEqual(payload);

    const [url, options] = fetch.mock.calls[0] as [string, RequestInit];
    const parsed = new URL(url, "http://localhost");
    expect(parsed.pathname).toBe("/tenant-api/api/v1/settings/change-history");
    expect(parsed.searchParams.get("kind")).toBe("production_norm");
    expect(parsed.searchParams.get("nm_id")).toBe("308044778");
    expect(parsed.searchParams.get("limit")).toBe("25");
    expect(parsed.searchParams.get("offset")).toBe("25");
    expect(options.method).toBeUndefined();
  });
});
