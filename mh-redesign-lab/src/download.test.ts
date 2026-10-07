import { afterEach, describe, expect, it, vi } from "vitest";
import { saveBlob } from "./download";

const blob = new Blob(["a;b"], { type: "text/csv" });
function stubUrl() {
  Object.assign(URL, { createObjectURL: vi.fn(() => "blob:x"), revokeObjectURL: vi.fn() });
  return vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
}
afterEach(() => { vi.restoreAllMocks(); delete (window as any).Capacitor; Object.assign(navigator, { share: undefined, canShare: undefined }); });

describe("saveBlob", () => {
  it("uses an anchor download on the web", async () => {
    const click = stubUrl();
    await saveBlob(blob, "r.csv");
    expect(click).toHaveBeenCalled(); expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:x");
  });
  it("uses navigator.share in the app", async () => {
    vi.spyOn(navigator, "userAgent", "get").mockReturnValue("MarketsHelperApp/1");
    const share = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { share, canShare: () => true });
    const click = stubUrl();
    await saveBlob(blob, "r.csv");
    expect(share.mock.calls[0][0].files[0].name).toBe("r.csv"); expect(click).not.toHaveBeenCalled();
  });
  it("uses Filesystem+Share plugins when share is unavailable", async () => {
    vi.spyOn(navigator, "userAgent", "get").mockReturnValue("MarketsHelperApp/1");
    const Filesystem = { writeFile: vi.fn().mockResolvedValue({ uri: "file:///r.csv" }) }, Share = { share: vi.fn().mockResolvedValue(undefined) };
    (window as any).Capacitor = { Plugins: { Filesystem, Share } };
    const click = stubUrl();
    await saveBlob(blob, "r.csv");
    expect(Filesystem.writeFile).toHaveBeenCalled(); expect(Share.share).toHaveBeenCalledWith({ title: "r.csv", url: "file:///r.csv" });
    expect(click).not.toHaveBeenCalled();
  });
  it("falls back to the anchor when the app has no share and no plugins", async () => {
    vi.spyOn(navigator, "userAgent", "get").mockReturnValue("MarketsHelperApp/1");
    const click = stubUrl();
    await saveBlob(blob, "r.csv");
    expect(click).toHaveBeenCalled();
  });
});
