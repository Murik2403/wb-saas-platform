package ru.marketshelper.app;

import android.app.Activity;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import java.io.InputStream;

/** Test-APK-only receiver in a separate package: no network or persistent writes. */
public class AuditShareReceiverActivity extends Activity {
  @Override public void onCreate(Bundle state) {
    super.onCreate(state);
    Intent result = new Intent("ru.marketshelper.app.AUDIT_SHARE_RESULT").setPackage("ru.marketshelper.app");
    try {
      Uri uri = getIntent().getParcelableExtra(Intent.EXTRA_STREAM, Uri.class);
      if (uri == null || !"content".equals(uri.getScheme())) throw new Exception("Missing content URI");
      result.putExtra("readPermission", (getIntent().getFlags() & Intent.FLAG_GRANT_READ_URI_PERMISSION) != 0);
      try (InputStream stream = getContentResolver().openInputStream(uri)) {
        byte[] bytes = stream.readNBytes(262145);
        if (bytes.length > 262144) throw new Exception("Audit size limit exceeded");
        result.putExtra("bytes", bytes);
      }
    } catch (Exception failure) { result.putExtra("error", failure.getClass().getSimpleName()); }
    sendBroadcast(result);
    finish();
  }
}
