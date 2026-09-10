/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_MH_API_BASE_URL?: string;
  readonly VITE_MH_API_MODE?: "auto" | "mock" | "v1" | "legacy";
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
