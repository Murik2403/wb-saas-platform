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
  private String shell(String command) throws Exception {
    try (android.os.ParcelFileDescriptor fd = inst.getUiAutomation().executeShellCommand(command);
      java.io.InputStream input = new android.os.ParcelFileDescriptor.AutoCloseInputStream(fd)) {
      return new String(input.readAllBytes(), java.nio.charset.StandardCharsets.UTF_8);
    }
  }
  private void waitForChooser() throws Exception {
    long end = System.nanoTime() + TimeUnit.SECONDS.toNanos(20);
    while (System.nanoTime() < end) {
      if (shell("dumpsys activity activities").contains("topResumedActivity=ActivityRecord") &&
          shell("dumpsys activity activities").contains("ChooserActivity")) return;
      Thread.sleep(250);
    }
    fail("Native share chooser did not appear");
  }
  private void chooseAuditReceiver() throws Exception {
    long end = System.nanoTime() + TimeUnit.SECONDS.toNanos(15);
    while (System.nanoTime() < end) {
      android.view.accessibility.AccessibilityNodeInfo root = inst.getUiAutomation().getRootInActiveWindow();
      if (root != null) {
        for (android.view.accessibility.AccessibilityNodeInfo node : root.findAccessibilityNodeInfosByText("Audit receiver")) {
          android.view.accessibility.AccessibilityNodeInfo clickable = node;
          while (clickable != null && !clickable.isClickable()) clickable = clickable.getParent();
          if (clickable != null && clickable.performAction(android.view.accessibility.AccessibilityNodeInfo.ACTION_CLICK)) return;
        }
      }
      Thread.sleep(250);
    }
    fail("Synthetic local receiver absent from native chooser");
  }
  private byte[] syntheticXlsx() throws Exception {
    java.io.ByteArrayOutputStream bytes = new java.io.ByteArrayOutputStream();
    String[][] parts = {
      {"[Content_Types].xml", "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\"><Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/><Default Extension=\"xml\" ContentType=\"application/xml\"/><Override PartName=\"/xl/workbook.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml\"/><Override PartName=\"/xl/worksheets/sheet1.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml\"/></Types>"},
      {"_rels/.rels", "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\"><Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\" Target=\"xl/workbook.xml\"/></Relationships>"},
      {"xl/workbook.xml", "<workbook xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\" xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\"><sheets><sheet name=\"Audit\" sheetId=\"1\" r:id=\"rId1\"/></sheets></workbook>"},
      {"xl/_rels/workbook.xml.rels", "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\"><Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet\" Target=\"worksheets/sheet1.xml\"/></Relationships>"},
      {"xl/worksheets/sheet1.xml", "<worksheet xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\"><sheetData><row r=\"1\"><c r=\"A1\" t=\"inlineStr\"><is><t>AUDIT</t></is></c><c r=\"B1\"><v>100</v></c></row></sheetData></worksheet>"}
    };
    try (java.util.zip.ZipOutputStream zip = new java.util.zip.ZipOutputStream(bytes)) {
      for (String[] part : parts) { zip.putNextEntry(new java.util.zip.ZipEntry(part[0])); zip.write(part[1].getBytes(java.nio.charset.StandardCharsets.UTF_8)); zip.closeEntry(); }
    }
    return bytes.toByteArray();
  }
  @Test public void generatedFilesShareCancelAndResume() throws Exception {
    org.junit.Assume.assumeTrue("File/share acceptance is opt-in (-e auditDownload true)",
      "true".equals(InstrumentationRegistry.getArguments().getString("auditDownload")));
    inst = InstrumentationRegistry.getInstrumentation();
    app = (MainActivity) inst.startActivitySync(new Intent(inst.getTargetContext(), MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
    load("https://demo.app.marketshelper.ru/?view=finance&period=30d");
    waitJs("location.hostname==='demo.app.marketshelper.ru' && document.readyState==='complete' && !!window.Capacitor?.Plugins?.Share", 90);
    String fixture;
    try (java.io.InputStream input = inst.getContext().getAssets().open("runtime-download-fixture.js")) {
      fixture = new String(input.readAllBytes(), java.nio.charset.StandardCharsets.UTF_8);
    }
    js(fixture);
    // Force the real Capacitor fallback, even if a newer WebView exposes Web Share.
    js("Object.defineProperty(navigator,'share',{value:undefined,configurable:true}); window.__auditAnchors=0; HTMLAnchorElement.prototype.click=function(){window.__auditAnchors++}; const originalShare=window.Capacitor.Plugins.Share.share; window.Capacitor.Plugins.Share.share=async function(o){try{return await originalShare(o)}catch(e){window.__auditShareError={name:e.name,message:e.message,code:e.code};throw e}};");
    String csv = "SKU;Количество;Цена\r\nAUDIT;2;100\r\n";
    String csv64 = android.util.Base64.encodeToString(csv.getBytes(java.nio.charset.StandardCharsets.UTF_8), android.util.Base64.NO_WRAP);
    js("window.__auditExport={done:false}; window.__auditSaveBlob(new Blob([Uint8Array.from(atob('"+csv64+"'),c=>c.charCodeAt(0))],{type:'text/csv'}),'audit-synthetic.csv').then(()=>window.__auditExport={done:true}).catch(e=>window.__auditExport={done:true,error:String(e)});");
    waitForChooser();
    shell("input keyevent 4");
    waitJs("window.__auditExport.done", 20);
    assertEquals("Native cancel diagnostic: " + js("JSON.stringify({export:window.__auditExport,anchors:window.__auditAnchors,error:window.__auditShareError})"), "true", js("!window.__auditExport.error && window.__auditAnchors===0 && location.hostname==='demo.app.marketshelper.ru'"));
    System.out.println("AUDIT_GENERATED_CSV_NATIVE_SHARE_CANCEL_NO_FALLBACK_OK");
    AtomicReference<Intent> received = new AtomicReference<>();
    android.content.BroadcastReceiver receiver = new android.content.BroadcastReceiver() {
      @Override public void onReceive(android.content.Context context, Intent intent) { received.set(intent); }
    };
    inst.getTargetContext().registerReceiver(receiver, new android.content.IntentFilter("ru.marketshelper.app.AUDIT_SHARE_RESULT"), android.content.Context.RECEIVER_EXPORTED);
    js("window.__auditExport={done:false}; window.__auditSaveBlob(new Blob([Uint8Array.from(atob('"+csv64+"'),c=>c.charCodeAt(0))],{type:'text/csv'}),'audit-synthetic.csv').then(()=>window.__auditExport={done:true}).catch(e=>window.__auditExport={done:true,error:String(e)});");
    waitForChooser();
    chooseAuditReceiver();
    waitJs("window.__auditExport.done", 20);
    for (int tries = 0; tries < 40 && received.get() == null; tries++) Thread.sleep(100);
    assertNotNull(received.get());
    assertNull(received.get().getStringExtra("error"));
    assertArrayEquals(csv.getBytes(java.nio.charset.StandardCharsets.UTF_8), received.get().getByteArrayExtra("bytes"));
    assertTrue("Read permission must reach the receiver", received.get().getBooleanExtra("readPermission", false));
    assertEquals("true", js("!window.__auditExport.error && window.__auditAnchors===0 && location.hostname==='demo.app.marketshelper.ru' && !!window.Capacitor?.Plugins?.Filesystem"));
    System.out.println("AUDIT_GENERATED_CSV_LOCAL_RECEIVER_BYTES_AND_RESUME_OK");
    byte[] xlsx = syntheticXlsx();
    String xlsx64 = android.util.Base64.encodeToString(xlsx, android.util.Base64.NO_WRAP);
    received.set(null);
    js("window.__auditExport={done:false}; window.__auditSaveBlob(new Blob([Uint8Array.from(atob('"+xlsx64+"'),c=>c.charCodeAt(0))],{type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}),'audit-synthetic.xlsx').then(()=>window.__auditExport={done:true}).catch(e=>window.__auditExport={done:true,error:String(e)});");
    waitForChooser();
    chooseAuditReceiver();
    waitJs("window.__auditExport.done", 20);
    for (int tries = 0; tries < 40 && received.get() == null; tries++) Thread.sleep(100);
    assertNotNull(received.get());
    assertNull(received.get().getStringExtra("error"));
    assertArrayEquals(xlsx, received.get().getByteArrayExtra("bytes"));
    assertTrue(received.get().getBooleanExtra("readPermission", false));
    assertEquals("true", js("!window.__auditExport.error && window.__auditAnchors===0 && location.hostname==='demo.app.marketshelper.ru'"));
    System.out.println("AUDIT_GENERATED_XLSX_LOCAL_RECEIVER_BYTES_AND_RESUME_OK");
    js("window.__auditDelete={done:false}; Promise.all(['audit-synthetic.csv','audit-synthetic.xlsx'].map(path=>window.Capacitor.Plugins.Filesystem.deleteFile({path,directory:'CACHE'}))).then(()=>window.__auditDelete={done:true});");
    waitJs("window.__auditDelete.done", 10);
    inst.getTargetContext().unregisterReceiver(receiver);
    inst.runOnMainSync(() -> app.finish());
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
