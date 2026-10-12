package ru.marketshelper.app;

/** Debug-only instrumented-test seam; no globals, intent bypass or release entry point. */
public class UnsupportedWebViewActivity extends MainActivity {
    @Override protected boolean supportsSecureNativeBridge() { return false; }
}
