import { capacitor, isNativeApp } from "./app-shell";

function anchorDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  try {
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.click();
  } finally {
    // Some browsers start the download asynchronously; revoking right after click() can cancel it.
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  }
}

function toBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1] ?? "");
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });
}

/** Saves a generated file. Web: anchor download. Native app (WKWebView ignores a.download): share sheet. */
export async function saveBlob(blob: Blob, filename: string): Promise<void> {
  if (isNativeApp()) {
    try {
      const file = new File([blob], filename, { type: blob.type });
      if (typeof navigator.share === "function" && navigator.canShare?.({ files: [file] })) {
        await navigator.share({ files: [file], title: filename });
        return;
      }
      const plugins = capacitor()?.Plugins;
      if (plugins?.Filesystem && plugins?.Share) {
        const written = await plugins.Filesystem.writeFile({ path: filename, data: await toBase64(blob), directory: "CACHE" });
        await plugins.Share.share({ title: filename, url: written.uri });
        return;
      }
    } catch (error) {
      if ((error as { name?: string })?.name === "AbortError") return; // user closed the share sheet
      // otherwise fall back to the web mechanism
    }
  }
  anchorDownload(blob, filename);
}
