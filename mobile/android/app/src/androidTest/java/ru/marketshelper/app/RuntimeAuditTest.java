package ru.marketshelper.app;

import android.app.Instrumentation;
import android.content.Intent;
import androidx.test.platform.app.InstrumentationRegistry;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import org.junit.Test;
import org.junit.runner.RunWith;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import static org.junit.Assert.*;

/** Isolated audit harness: public pages only; no account registration or login. */
@RunWith(AndroidJUnit4.class)
public class RuntimeAuditTest {
  private Instrumentation inst;
  private MainActivity app;
  private String js(String script) throws Exception {
    CountDownLatch done = new CountDownLatch(1);
    AtomicReference<String> value = new AtomicReference<>();
    inst.runOnMainSync(() -> app.getBridge().getWebView().evaluateJavascript(script, r -> { value.set(r); done.countDown(); }));
    assertTrue("Javascript callback timed out", done.await(10, TimeUnit.SECONDS));
    return value.get();
  }
  private void waitJs(String expression, long seconds) throws Exception {
    long end = System.nanoTime() + seconds * 1000000000L;
    String last = "";
    while (System.nanoTime() < end) {
      last = js(expression);
      if ("true".equals(last)) return;
      Thread.sleep(250);
    }
    fail("Condition not reached: " + expression + " last=" + last + " state=" + js("JSON.stringify({url:location.href,ready:document.readyState,title:document.title,text:document.body?.innerText.slice(0,180)})"));
  }
  private void load(String url) {
    inst.runOnMainSync(() -> app.getBridge().getWebView().loadUrl(url));
  }
  @Test public void unsupportedWebViewNeverCreatesBridge() throws Exception {
    inst = InstrumentationRegistry.getInstrumentation();
    Intent intent = new Intent().setClassName(inst.getTargetContext(), "ru.marketshelper.app.UnsupportedWebViewActivity")
      .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
    app = (MainActivity) inst.startActivitySync(intent);
    assertNull("Legacy WebView must not construct a remote/native bridge", app.getBridge());
    inst.runOnMainSync(() -> {
      android.widget.TextView notice = app.getWindow().getDecorView().findViewWithTag("webview-update-required");
      assertNotNull(notice);
      assertTrue(notice.getText().toString().contains("Обновите Android System WebView"));
      app.onConfigurationChanged(new android.content.res.Configuration(app.getResources().getConfiguration()));
      app.finish();
    });
    inst.waitForIdleSync();
    System.out.println("AUDIT_LEGACY_WEBVIEW_FAIL_CLOSED_OK");
  }
  @Test public void publicRemoteWebviewAndNativeFilesystem() throws Exception {
    org.junit.Assume.assumeTrue("Public remote acceptance is opt-in (-e auditRemote true)",
      "true".equals(InstrumentationRegistry.getArguments().getString("auditRemote")));
    inst = InstrumentationRegistry.getInstrumentation();
    Intent intent = new Intent(inst.getTargetContext(), MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
    app = (MainActivity) inst.startActivitySync(intent);
    waitJs("location.hostname==='marketshelper.ru' && document.readyState==='complete' && !!document.querySelector('input[type=password]')", 60);
    assertEquals("true", js("navigator.userAgent.includes('MarketsHelperApp/1.0')"));
    waitJs("window.__mhAppShell===true && !!window.Capacitor.Plugins.Filesystem && document.documentElement.classList.contains('platform-android')", 15);
    System.out.println("AUDIT_GATEWAY_NATIVE_BRIDGE_OK");
    load("https://demo.app.marketshelper.ru/?view=finance&period=30d");
    waitJs("location.hostname==='demo.app.marketshelper.ru' && document.readyState==='complete' && document.body.innerText.includes('Финансы')", 90);
    assertEquals("true", js("!!window.Capacitor.Plugins.Filesystem && window.Capacitor.isNativePlatform()"));
    System.out.println("AUDIT_PUBLIC_DEMO_NAVIGATION_OK");
    inst.runOnMainSync(() -> app.getOnBackPressedDispatcher().onBackPressed());
    waitJs("location.hostname==='marketshelper.ru' && !!document.querySelector('input[type=password]')", 30);
    System.out.println("AUDIT_NATIVE_BACK_TO_GATEWAY_OK");
    load("https://demo.app.marketshelper.ru/?view=finance&period=30d");
    waitJs("location.hostname==='demo.app.marketshelper.ru' && !!window.Capacitor?.Plugins?.Filesystem", 30);
    // Same native Filesystem cache path used by saveBlob; share-sheet user choice is not claimed.
    js("window.__auditFile={done:false}; window.Capacitor.Plugins.Filesystem.writeFile({path:'audit-synthetic.txt',data:btoa('MARKETSHELPER-AUDIT-SYNTHETIC'),directory:'CACHE'}).then(w=>window.Capacitor.Plugins.Filesystem.readFile({path:'audit-synthetic.txt',directory:'CACHE'}).then(r=>window.__auditFile={done:true,uri:w.uri,data:r.data})).catch(e=>window.__auditFile={done:true,error:String(e)})");
    waitJs("window.__auditFile.done", 20);
    assertEquals("true", js("!window.__auditFile.error && atob(window.__auditFile.data)==='MARKETSHELPER-AUDIT-SYNTHETIC' && window.__auditFile.uri.startsWith('file:')"));
    System.out.println("AUDIT_NATIVE_CACHE_FILE_ROUNDTRIP_OK");
    js("window.__auditExternal={done:false}; window.Capacitor.Plugins.Browser.open({url:'https://developer.android.com/'}).then(()=>window.__auditExternal={done:true}).catch(e=>window.__auditExternal={done:true,error:String(e)})");
    waitJs("window.__auditExternal.done", 20);
    assertEquals("true", js("!window.__auditExternal.error && location.hostname==='demo.app.marketshelper.ru'"));
    js("window.Capacitor.Plugins.Browser.close()");
    System.out.println("AUDIT_BROWSER_PLUGIN_OPEN_CLOSE_OK");
    inst.runOnMainSync(() -> app.getBridge().getWebView().loadDataWithBaseURL(
      "https://untrusted.invalid/", "<html><body>Untrusted origin probe</body></html>", "text/html", "UTF-8", null));
    waitJs("document.readyState==='complete' && document.body?.innerText==='Untrusted origin probe'", 10);
    assertEquals("true", js("typeof window.Capacitor==='undefined' && typeof window.androidBridge==='undefined'"));
    System.out.println("AUDIT_UNTRUSTED_ORIGIN_NO_BRIDGE_OK");
    // Block only this test WebView's network; localhost assets remain intercepted by Capacitor.
    inst.runOnMainSync(() -> {
      app.getBridge().getWebView().getSettings().setCacheMode(android.webkit.WebSettings.LOAD_NO_CACHE);
      app.getBridge().getWebView().getSettings().setBlockNetworkLoads(true);
      app.getBridge().getWebView().loadUrl("https://marketshelper.ru/login");
    });
    waitJs("document.body?.innerText.includes('Нет соединения') && !!document.getElementById('retry')", 30);
    assertEquals("true", js("getComputedStyle(document.documentElement).getPropertyValue('--bg').trim()===(matchMedia('(prefers-color-scheme:dark)').matches?'#0b1115':'#f4f7f6')"));
    System.out.println("AUDIT_LOCAL_OFFLINE_PAGE_THEME_OK");
    inst.runOnMainSync(() -> app.getBridge().getWebView().getSettings().setBlockNetworkLoads(false));
    load("https://marketshelper.ru/login");
    waitJs("location.hostname==='marketshelper.ru' && !!document.querySelector('input[type=password]')", 60);
    System.out.println("AUDIT_NETWORK_RESTORED_GATEWAY_OK");
    inst.runOnMainSync(() -> app.finish());
  }
}
