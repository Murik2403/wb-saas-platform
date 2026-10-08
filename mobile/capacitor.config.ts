import type { CapacitorConfig } from "@capacitor/cli";
import { readFileSync } from "node:fs";
const app = JSON.parse(readFileSync(new URL("./app.json", import.meta.url), "utf8")) as Record<string, any>;

// appId / appName / URLs live in app.json (single place). After changing appId
// on an already generated native project, see README "Changing appId".
const config: CapacitorConfig = {
  appId: app.appId,
  appName: app.appName,
  webDir: "www",
  appendUserAgent: app.userAgentSuffix,
  server: {
    url: app.loginUrl,
    cleartext: false,
    allowNavigation: app.allowNavigation,
    errorPath: "index.html",
  },
  android: { allowMixedContent: false },
  ios: { contentInset: "never", limitsNavigationsToAppBoundDomains: false },
  plugins: {
    SplashScreen: { launchShowDuration: 800, launchAutoHide: true, backgroundColor: "#0b1115", showSpinner: false },
    StatusBar: { overlaysWebView: false, backgroundColor: "#0b1115", style: "DARK" },
    Keyboard: { resize: "body" },
    PushNotifications: { presentationOptions: ["badge", "sound", "alert"] },
  },
};

export default config;
