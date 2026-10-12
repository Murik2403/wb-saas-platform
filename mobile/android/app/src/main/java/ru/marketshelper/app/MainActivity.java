package ru.marketshelper.app;

import com.getcapacitor.BridgeActivity;
import com.getcapacitor.CapConfig;
import com.getcapacitor.JSExport;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginHandle;
import com.getcapacitor.PluginManager;
import com.getcapacitor.annotation.CapacitorPlugin;
import androidx.webkit.WebViewCompat;
import androidx.webkit.WebViewFeature;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.List;
import android.content.res.Configuration;
import android.graphics.Color;
import android.view.Gravity;
import android.widget.TextView;

public class MainActivity extends BridgeActivity {
    protected boolean supportsSecureNativeBridge() {
        return WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER)
            && WebViewFeature.isFeatureSupported(WebViewFeature.DOCUMENT_START_SCRIPT)
            && !CapConfig.loadDefault(this).isUsingLegacyBridge();
    }

    private void showWebViewUpdateRequired() {
        TextView message = new TextView(this);
        message.setTag("webview-update-required");
        message.setText("Обновите Android System WebView\n\nДля безопасного входа нужна актуальная версия WebView. После обновления закройте и откройте приложение.");
        message.setTextSize(20);
        message.setGravity(Gravity.CENTER);
        int inset = (int) (24 * getResources().getDisplayMetrics().density);
        message.setPadding(inset, inset, inset, inset);
        boolean dark = (getResources().getConfiguration().uiMode & Configuration.UI_MODE_NIGHT_MASK)
            == Configuration.UI_MODE_NIGHT_YES;
        message.setTextColor(Color.parseColor(dark ? "#e8f1ee" : "#10251f"));
        message.setBackgroundColor(Color.parseColor(dark ? "#0b1115" : "#f4f7f6"));
        setContentView(message);
    }

    @Override
    protected void load() {
        // BridgeActivity's lifecycle methods tolerate a null bridge. Do not
        // construct one on legacy WebViews: Capacitor falls back to a global
        // JavascriptInterface there, before any remote page is loaded.
        if (!supportsSecureNativeBridge()) {
            showWebViewUpdateRequired();
            return;
        }
        super.load();
        // If AndroidX unexpectedly rejects origin rules, discard Capacitor's
        // unrestricted JavascriptInterface fallback. Scoped WebMessageListener
        // is a separate API and is unaffected by this removal.
        bridge.getWebView().removeJavascriptInterface("androidBridge");
        // Capacitor 8.5.3's DOCUMENT_START_SCRIPT path injects only the initial
        // server origin. Tenant navigation and the local offline page need the
        // same bridge before their page scripts run. Keep this list narrower
        // than arbitrary navigation; never expose native plugins to all origins.
        try {
            List<PluginHandle> handles = new ArrayList<>();
            for (String id : Arrays.asList("CapacitorCookies", "CapacitorHttp", "WebView")) {
                PluginHandle handle = bridge.getPlugin(id);
                if (handle != null) handles.add(handle);
            }
            for (Class<? extends Plugin> plugin : new PluginManager(getAssets()).loadPluginClasses()) {
                CapacitorPlugin annotation = plugin.getAnnotation(CapacitorPlugin.class);
                if (annotation == null) continue;
                String id = annotation.name().isEmpty() ? plugin.getSimpleName() : annotation.name();
                PluginHandle handle = bridge.getPlugin(id);
                if (handle != null) handles.add(handle);
            }
            String script = JSExport.getGlobalJS(this, bridge.getConfig().isLoggingEnabled(), bridge.isDevMode())
                + "\n\nwindow.WEBVIEW_SERVER_URL = '" + bridge.getLocalUrl() + "';\n\n"
                + JSExport.getBridgeJS(this) + "\n\n" + JSExport.getPluginJS(handles) + "\n\n"
                + JSExport.getCordovaJS(this) + "\n\n" + JSExport.getCordovaPluginsFileJS(this)
                + "\n\n" + JSExport.getCordovaPluginJS(this);
            WebViewCompat.addDocumentStartJavaScript(bridge.getWebView(), script,
                new HashSet<>(Arrays.asList("https://*.app.marketshelper.ru", "https://localhost")));
        } catch (Exception error) {
            throw new IllegalStateException("Unable to initialize trusted tenant native bridge", error);
        }
    }
}
