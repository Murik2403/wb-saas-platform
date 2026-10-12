package ru.marketshelper.app;

import com.getcapacitor.BridgeActivity;
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

public class MainActivity extends BridgeActivity {
    @Override
    protected void load() {
        super.load();
        // Capacitor 8.5.3's DOCUMENT_START_SCRIPT path injects only the initial
        // server origin. Tenant navigation and the local offline page need the
        // same bridge before their page scripts run. Keep this list narrower
        // than arbitrary navigation; never expose native plugins to all origins.
        if (!WebViewFeature.isFeatureSupported(WebViewFeature.DOCUMENT_START_SCRIPT)) return;
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
