package com.tracememory.tracetodo;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.ClipData;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.text.InputType;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.HapticFeedbackConstants;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.view.WindowInsetsController;
import android.view.WindowManager;
import android.webkit.CookieManager;
import android.webkit.DownloadListener;
import android.webkit.JavascriptInterface;
import android.webkit.PermissionRequest;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

public class MainActivity extends Activity {

    private static final String PREFS_NAME = "tracetodo_native_prefs";
    private static final String KEY_SERVER_URL = "server_url";
    private static final String KEY_USER_CUSTOMIZED = "server_user_customized";
    private static final String KEY_LAST_THEME_BG = "last_theme_bg";

    private static final int REQ_FILE_CHOOSER = 1001;
    private static final int REQ_WEB_PERMISSIONS = 1002;
    private static final int REQ_POST_NOTIF = 1003;

    private FrameLayout rootLayout;
    private WebView webView;
    private ProgressBar progressBar;
    private LinearLayout offlineOverlay;
    private TextView offlineDetailText;

    private ValueCallback<Uri[]> fileUploadCallback;
    private PermissionRequest pendingWebPermissionRequest;

    private String currentBaseUrl;
    private boolean triedFallbackOnColdLoad = false;
    private boolean pageLoadedOnce = false;
    private String pendingDeepAction = null;
    private String pendingSharedText = null;
    private long lastBackPressMs = 0L;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        SharedPreferences sp = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        int initialBg = sp.getInt(KEY_LAST_THEME_BG, Color.parseColor("#F4F6FB"));
        applySystemBarColorsInt(initialBg);

        String defaultUrl = getString(R.string.default_server_url);
        boolean customized = sp.getBoolean(KEY_USER_CUSTOMIZED, false);
        String savedUrl = sp.getString(KEY_SERVER_URL, "");
        if (!customized || savedUrl == null || savedUrl.trim().isEmpty()) {
            currentBaseUrl = defaultUrl;
            sp.edit().putString(KEY_SERVER_URL, defaultUrl).apply();
        } else {
            currentBaseUrl = cleanServerUrl(savedUrl);
        }

        captureIncomingIntent(getIntent());
        buildRootLayout(initialBg);
        configureWebView();
        requestNotificationPermissionIfNeeded();

        loadServerHome(false);
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        captureIncomingIntent(intent);
        if (pageLoadedOnce) {
            dispatchPendingIntentActions();
        }
    }

    private void captureIncomingIntent(Intent intent) {
        if (intent == null) return;
        String action = intent.getAction();
        if (Intent.ACTION_SEND.equals(action) && "text/plain".equals(intent.getType())) {
            String shared = intent.getStringExtra(Intent.EXTRA_TEXT);
            if (shared != null && !shared.trim().isEmpty()) {
                pendingSharedText = shared.trim();
            }
        } else if (Intent.ACTION_VIEW.equals(action) && intent.getData() != null) {
            Uri data = intent.getData();
            if ("tracetodo".equalsIgnoreCase(data.getScheme())) {
                pendingDeepAction = data.getHost() != null ? data.getHost().toLowerCase() : "";
            }
        }
    }

    private void buildRootLayout(int initialBg) {
        rootLayout = new FrameLayout(this);
        rootLayout.setBackgroundColor(initialBg);

        webView = new WebView(this);
        webView.setBackgroundColor(initialBg);
        rootLayout.addView(webView, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
        ));

        progressBar = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progressBar.setMax(100);
        progressBar.setVisibility(View.GONE);
        FrameLayout.LayoutParams pbParams = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                dp(3)
        );
        pbParams.gravity = Gravity.TOP;
        rootLayout.addView(progressBar, pbParams);

        offlineOverlay = buildOfflineOverlay();
        offlineOverlay.setVisibility(View.GONE);
        rootLayout.addView(offlineOverlay, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
        ));

        setContentView(rootLayout);
    }

    private LinearLayout buildOfflineOverlay() {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setGravity(Gravity.CENTER);
        box.setPadding(dp(28), dp(28), dp(28), dp(28));
        box.setBackgroundColor(Color.parseColor("#F4F6FB"));

        TextView icon = new TextView(this);
        icon.setText("☁️");
        icon.setTextSize(TypedValue.COMPLEX_UNIT_SP, 42);
        icon.setGravity(Gravity.CENTER);
        box.addView(icon);

        TextView title = new TextView(this);
        title.setText("无法连接到 TraceTodo 云服务");
        title.setTextSize(TypedValue.COMPLEX_UNIT_SP, 18);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        title.setTextColor(Color.parseColor("#0F172A"));
        title.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams titleLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        );
        titleLp.topMargin = dp(12);
        box.addView(title, titleLp);

        offlineDetailText = new TextView(this);
        offlineDetailText.setText("请检查移动网络或 Wi-Fi 连接后重试");
        offlineDetailText.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13);
        offlineDetailText.setTextColor(Color.parseColor("#64748B"));
        offlineDetailText.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams subLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        );
        subLp.topMargin = dp(8);
        box.addView(offlineDetailText, subLp);

        Button retryBtn = new Button(this);
        retryBtn.setText("立即重连");
        retryBtn.setTextColor(Color.WHITE);
        retryBtn.setAllCaps(false);
        GradientDrawable primaryBg = new GradientDrawable();
        primaryBg.setColor(Color.parseColor("#2563EB"));
        primaryBg.setCornerRadius(dp(999));
        retryBtn.setBackground(primaryBg);
        retryBtn.setPadding(dp(26), dp(10), dp(26), dp(10));
        retryBtn.setOnClickListener(v -> {
            offlineOverlay.setVisibility(View.GONE);
            loadServerHome(true);
        });
        LinearLayout.LayoutParams retryLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        );
        retryLp.topMargin = dp(22);
        box.addView(retryBtn, retryLp);

        Button switchBtn = new Button(this);
        switchBtn.setText("切换服务端地址");
        switchBtn.setTextColor(Color.parseColor("#334155"));
        switchBtn.setAllCaps(false);
        GradientDrawable secBg = new GradientDrawable();
        secBg.setColor(Color.WHITE);
        secBg.setStroke(dp(1), Color.parseColor("#CBD5E1"));
        secBg.setCornerRadius(dp(999));
        switchBtn.setBackground(secBg);
        switchBtn.setPadding(dp(22), dp(8), dp(22), dp(8));
        switchBtn.setOnClickListener(v -> showServerSwitchDialog());
        LinearLayout.LayoutParams swLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        );
        swLp.topMargin = dp(10);
        box.addView(switchBtn, swLp);

        return box;
    }

    @SuppressLint({"SetJavaScriptEnabled", "AddJavascriptInterface"})
    private void configureWebView() {
        CookieManager cm = CookieManager.getInstance();
        cm.setAcceptCookie(true);
        cm.setAcceptThirdPartyCookies(webView, true);

        WebSettings ws = webView.getSettings();
        ws.setJavaScriptEnabled(true);
        ws.setDomStorageEnabled(true);
        ws.setDatabaseEnabled(true);
        ws.setMediaPlaybackRequiresUserGesture(false);
        ws.setAllowFileAccess(true);
        ws.setAllowContentAccess(true);
        ws.setLoadWithOverviewMode(true);
        ws.setUseWideViewPort(true);
        ws.setBuiltInZoomControls(false);
        ws.setDisplayZoomControls(false);
        ws.setTextZoom(100);
        ws.setCacheMode(WebSettings.LOAD_DEFAULT);
        ws.setMixedContentMode(WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE);
        ws.setUserAgentString(ws.getUserAgentString() + " TraceTodoAndroid/1.4.0");

        webView.addJavascriptInterface(new NativeBridge(), "TraceTodoNative");

        webView.setDownloadListener(new DownloadListener() {
            @Override
            public void onDownloadStart(String url, String userAgent, String contentDisposition, String mimetype, long contentLength) {
                try {
                    if (url != null && (url.startsWith("http://") || url.startsWith("https://"))) {
                        Intent i = new Intent(Intent.ACTION_VIEW, Uri.parse(url));
                        startActivity(i);
                    }
                } catch (Exception ignored) {
                }
            }
        });

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                if (uri == null) return false;
                String scheme = uri.getScheme() != null ? uri.getScheme().toLowerCase() : "";
                if ("http".equals(scheme) || "https".equals(scheme)) {
                    String targetHost = uri.getHost() != null ? uri.getHost() : "";
                    Uri baseUri = Uri.parse(currentBaseUrl);
                    String baseHost = baseUri.getHost() != null ? baseUri.getHost() : "";
                    if (targetHost.equalsIgnoreCase(baseHost) || "43.228.79.133".equals(targetHost) || "todo.531party.cn".equalsIgnoreCase(targetHost)) {
                        return false;
                    }
                    try {
                        startActivity(new Intent(Intent.ACTION_VIEW, uri));
                    } catch (Exception ignored) {
                    }
                    return true;
                }
                return true;
            }

            @Override
            public void onPageStarted(WebView view, String url, Bitmap favicon) {
                super.onPageStarted(view, url, favicon);
                offlineOverlay.setVisibility(View.GONE);
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);
                pageLoadedOnce = true;
                injectNativeEnhancementBridge();
                dispatchPendingIntentActions();
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                super.onReceivedError(view, request, error);
                if (request != null && request.isForMainFrame()) {
                    String fallbackUrl = getString(R.string.fallback_server_url);
                    SharedPreferences sp = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
                    boolean customized = sp.getBoolean(KEY_USER_CUSTOMIZED, false);
                    if (!customized && !triedFallbackOnColdLoad && !currentBaseUrl.equals(fallbackUrl)) {
                        triedFallbackOnColdLoad = true;
                        currentBaseUrl = fallbackUrl;
                        view.loadUrl(currentBaseUrl + "/console");
                        return;
                    }
                    String desc = (error != null && error.getDescription() != null)
                            ? error.getDescription().toString()
                            : "网络连接不可用";
                    offlineDetailText.setText("当前地址：" + currentBaseUrl + "\n原因：" + desc);
                    offlineOverlay.setVisibility(View.VISIBLE);
                }
            }
        });

        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onProgressChanged(WebView view, int newProgress) {
                if (newProgress >= 100) {
                    progressBar.setVisibility(View.GONE);
                } else {
                    if (progressBar.getVisibility() != View.VISIBLE) {
                        progressBar.setVisibility(View.VISIBLE);
                    }
                    progressBar.setProgress(newProgress);
                }
            }

            @Override
            public boolean onShowFileChooser(WebView webView, ValueCallback<Uri[]> filePathCallback, FileChooserParams fileChooserParams) {
                if (fileUploadCallback != null) {
                    fileUploadCallback.onReceiveValue(null);
                }
                fileUploadCallback = filePathCallback;

                Intent contentIntent = new Intent(Intent.ACTION_GET_CONTENT);
                contentIntent.addCategory(Intent.CATEGORY_OPENABLE);
                contentIntent.setType("image/*");
                if (fileChooserParams != null && fileChooserParams.getMode() == FileChooserParams.MODE_OPEN_MULTIPLE) {
                    contentIntent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
                }
                Intent chooser = Intent.createChooser(contentIntent, "选择图片附加到记忆或随记");
                try {
                    startActivityForResult(chooser, REQ_FILE_CHOOSER);
                    return true;
                } catch (Exception e) {
                    fileUploadCallback = null;
                    return false;
                }
            }

            @Override
            public void onPermissionRequest(final PermissionRequest request) {
                runOnUiThread(() -> {
                    String[] resources = request.getResources();
                    List<String> osPermsNeeded = new ArrayList<>();
                    for (String r : resources) {
                        if (PermissionRequest.RESOURCE_AUDIO_CAPTURE.equals(r)) {
                            if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                                osPermsNeeded.add(Manifest.permission.RECORD_AUDIO);
                            }
                        } else if (PermissionRequest.RESOURCE_VIDEO_CAPTURE.equals(r)) {
                            if (checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
                                osPermsNeeded.add(Manifest.permission.CAMERA);
                            }
                        }
                    }
                    if (osPermsNeeded.isEmpty()) {
                        request.grant(resources);
                    } else {
                        pendingWebPermissionRequest = request;
                        requestPermissions(osPermsNeeded.toArray(new String[0]), REQ_WEB_PERMISSIONS);
                    }
                });
            }
        });
    }

    private void injectNativeEnhancementBridge() {
        String js = "(function(){"
                + "if(window.__ttAndroidBridgeInstalled)return;"
                + "window.__ttAndroidBridgeInstalled=true;"
                + "function syncBar(){"
                + "  try{"
                + "    var bg=getComputedStyle(document.body).backgroundColor||'';"
                + "    if(window.TraceTodoNative&&window.TraceTodoNative.onThemeBgChanged){"
                + "      window.TraceTodoNative.onThemeBgChanged(bg);"
                + "    }"
                + "  }catch(e){}"
                + "}"
                + "syncBar();"
                + "setTimeout(syncBar,180);"
                + "new MutationObserver(function(){setTimeout(syncBar,40);})"
                + ".observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});"
                + "var fo=document.getElementById('focusOverlay');"
                + "if(fo){"
                + "  new MutationObserver(function(){"
                + "    if(window.TraceTodoNative&&window.TraceTodoNative.setKeepScreenOn){"
                + "      window.TraceTodoNative.setKeepScreenOn(Boolean(fo.open));"
                + "    }"
                + "  }).observe(fo,{attributes:true,attributeFilter:['open']});"
                + "}"
                + "document.addEventListener('click',function(ev){"
                + "  var btn=ev.target&&ev.target.closest?ev.target.closest('button,.scene-card-item,.theme-card-item'):null;"
                + "  if(btn&&window.TraceTodoNative&&window.TraceTodoNative.lightTap){"
                + "    window.TraceTodoNative.lightTap();"
                + "  }"
                + "  var a=ev.target&&ev.target.closest?ev.target.closest('a[download]'):null;"
                + "  if(a&&a.href&&a.href.indexOf('blob:')===0&&window.TraceTodoNative&&window.TraceTodoNative.shareExportedText){"
                + "    ev.preventDefault();"
                + "    var fname=a.getAttribute('download')||'tracetodo-export.md';"
                + "    fetch(a.href).then(function(r){return r.text();}).then(function(txt){"
                + "      window.TraceTodoNative.shareExportedText(fname,txt);"
                + "    }).catch(function(){});"
                + "  }"
                + "},true);"
                + "})();";
        webView.evaluateJavascript(js, null);
    }

    private void dispatchPendingIntentActions() {
        if (pendingSharedText != null && !pendingSharedText.isEmpty()) {
            final String textToInject = pendingSharedText;
            pendingSharedText = null;
            String quoted = JSONObject.quote(textToInject);
            String js = "(function(){"
                    + "if(typeof switchTab==='function')switchTab('tasks');"
                    + "var inp=document.getElementById('quickInput')||document.querySelector('input.main-input');"
                    + "if(inp){inp.value=" + quoted + ";inp.dispatchEvent(new Event('input',{bubbles:true}));inp.focus();}"
                    + "if(typeof showToast==='function')showToast('✨ 已带入分享文本，点击「AI 录入」即可归档');"
                    + "})();";
            webView.postDelayed(() -> webView.evaluateJavascript(js, null), 280);
        }
        if (pendingDeepAction != null && !pendingDeepAction.isEmpty()) {
            final String act = pendingDeepAction;
            pendingDeepAction = null;
            String js;
            if ("focus".equals(act)) {
                js = "if(typeof switchTab==='function')switchTab('focus');";
            } else if ("calendar".equals(act)) {
                js = "if(typeof switchTab==='function')switchTab('calendar');";
            } else if ("login".equals(act) || "auth".equals(act) || "account".equals(act)) {
                js = "if(typeof openAuthModal==='function')openAuthModal();";
            } else if ("ask".equals(act)) {
                js = "if(typeof toggleAskFloatPanel==='function')toggleAskFloatPanel();else if(typeof switchTab==='function')switchTab('ask');";
            } else {
                js = "if(typeof switchTab==='function')switchTab('tasks');var q=document.getElementById('quickInput')||document.querySelector('input.main-input');if(q)q.focus();";
            }
            webView.postDelayed(() -> webView.evaluateJavascript(js, null), 260);
        }
    }

    private final class NativeBridge {
        @JavascriptInterface
        public void onThemeBgChanged(final String cssColor) {
            runOnUiThread(() -> {
                Integer parsed = parseCssColor(cssColor);
                if (parsed != null) {
                    applySystemBarColorsInt(parsed);
                    rootLayout.setBackgroundColor(parsed);
                    webView.setBackgroundColor(parsed);
                    getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
                            .edit()
                            .putInt(KEY_LAST_THEME_BG, parsed)
                            .apply();
                }
            });
        }

        @JavascriptInterface
        public void setKeepScreenOn(final boolean keepOn) {
            runOnUiThread(() -> {
                Window w = getWindow();
                if (w == null) return;
                if (keepOn) {
                    w.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
                } else {
                    w.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
                }
            });
        }

        @JavascriptInterface
        public void lightTap() {
            runOnUiThread(() -> {
                if (webView != null) {
                    webView.performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK);
                }
            });
        }

        @JavascriptInterface
        public void shareExportedText(final String filename, final String content) {
            runOnUiThread(() -> {
                if (content == null || content.isEmpty()) return;
                Intent send = new Intent(Intent.ACTION_SEND);
                send.setType("text/markdown");
                send.putExtra(Intent.EXTRA_SUBJECT, filename != null ? filename : "TraceTodo-Export.md");
                send.putExtra(Intent.EXTRA_TEXT, content);
                try {
                    startActivity(Intent.createChooser(send, "导出 / 分享知识库 Markdown"));
                } catch (Exception ignored) {
                }
            });
        }

        @JavascriptInterface
        public void openServerSwitcher() {
            runOnUiThread(MainActivity.this::showServerSwitchDialog);
        }
    }

    private Integer parseCssColor(String raw) {
        if (raw == null) return null;
        String s = raw.trim();
        try {
            if (s.startsWith("#")) {
                return Color.parseColor(s);
            }
            if (s.startsWith("rgb")) {
                int l = s.indexOf('(');
                int r = s.indexOf(')');
                if (l > 0 && r > l) {
                    String[] parts = s.substring(l + 1, r).split(",");
                    if (parts.length >= 3) {
                        int red = Integer.parseInt(parts[0].trim());
                        int green = Integer.parseInt(parts[1].trim());
                        int blue = Integer.parseInt(parts[2].trim());
                        return Color.rgb(red, green, blue);
                    }
                }
            }
        } catch (Exception ignored) {
        }
        return null;
    }

    private void applySystemBarColorsInt(int color) {
        Window window = getWindow();
        if (window == null) return;
        window.setStatusBarColor(color);
        window.setNavigationBarColor(color);

        double lum = (0.299 * Color.red(color) + 0.587 * Color.green(color) + 0.114 * Color.blue(color)) / 255.0;
        boolean isLightBg = lum > 0.55;

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            WindowInsetsController ctrl = window.getInsetsController();
            if (ctrl != null) {
                int mask = WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS
                        | WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS;
                ctrl.setSystemBarsAppearance(isLightBg ? mask : 0, mask);
            }
        } else {
            View decor = window.getDecorView();
            int flags = decor.getSystemUiVisibility();
            if (isLightBg) {
                flags |= View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR;
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    flags |= View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR;
                }
            } else {
                flags &= ~View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR;
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    flags &= ~View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR;
                }
            }
            decor.setSystemUiVisibility(flags);
        }
    }

    private void showServerSwitchDialog() {
        final String prodHttps = getString(R.string.default_server_url);
        final String prodIp = getString(R.string.fallback_server_url);
        final String testIp = getString(R.string.test_server_url);

        LinearLayout container = new LinearLayout(this);
        container.setOrientation(LinearLayout.VERTICAL);
        container.setPadding(dp(20), dp(12), dp(20), dp(4));

        final EditText input = new EditText(this);
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        input.setText(currentBaseUrl);
        input.setSelection(currentBaseUrl.length());
        container.addView(input);

        String[] presets = new String[]{
                "☁️ 线上正式域名 (" + prodHttps + ")",
                "🌐 线上正式直连 (" + prodIp + ")",
                "🧪 云端测试环境 (" + testIp + ")"
        };

        new AlertDialog.Builder(this)
                .setTitle("切换 TraceTodo 服务端环境")
                .setItems(presets, (dialog, which) -> {
                    if (which == 0) applyNewServerUrl(prodHttps, false);
                    else if (which == 1) applyNewServerUrl(prodIp, true);
                    else if (which == 2) applyNewServerUrl(testIp, true);
                })
                .setView(container)
                .setPositiveButton("保存输入的地址", (dialog, which) -> {
                    String entered = input.getText() != null ? input.getText().toString() : "";
                    if (!entered.trim().isEmpty()) {
                        applyNewServerUrl(entered, true);
                    }
                })
                .setNegativeButton("取消", null)
                .show();
    }

    private void applyNewServerUrl(String rawUrl, boolean userCustomized) {
        String cleaned = cleanServerUrl(rawUrl);
        currentBaseUrl = cleaned;
        getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
                .edit()
                .putString(KEY_SERVER_URL, cleaned)
                .putBoolean(KEY_USER_CUSTOMIZED, userCustomized)
                .apply();
        offlineOverlay.setVisibility(View.GONE);
        loadServerHome(true);
    }

    private String cleanServerUrl(String raw) {
        String s = raw == null ? "" : raw.trim();
        while (s.endsWith("/")) {
            s = s.substring(0, s.length() - 1);
        }
        if (!s.startsWith("http://") && !s.startsWith("https://")) {
            s = "https://" + s;
        }
        return s;
    }

    private void loadServerHome(boolean isRetry) {
        if (!isRetry) {
            triedFallbackOnColdLoad = false;
        }
        webView.loadUrl(currentBaseUrl + "/console");
    }

    private void requestNotificationPermissionIfNeeded() {
        if (Build.VERSION.SDK_INT >= 33) {
            if (checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
                requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, REQ_POST_NOTIF);
            }
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == REQ_FILE_CHOOSER) {
            if (fileUploadCallback == null) return;
            Uri[] results = null;
            if (resultCode == Activity.RESULT_OK && data != null) {
                if (data.getClipData() != null) {
                    ClipData clip = data.getClipData();
                    results = new Uri[clip.getItemCount()];
                    for (int i = 0; i < clip.getItemCount(); i++) {
                        results[i] = clip.getItemAt(i).getUri();
                    }
                } else if (data.getData() != null) {
                    results = new Uri[]{data.getData()};
                }
            }
            fileUploadCallback.onReceiveValue(results);
            fileUploadCallback = null;
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == REQ_WEB_PERMISSIONS && pendingWebPermissionRequest != null) {
            boolean allGranted = grantResults.length > 0;
            for (int r : grantResults) {
                if (r != PackageManager.PERMISSION_GRANTED) {
                    allGranted = false;
                    break;
                }
            }
            if (allGranted) {
                pendingWebPermissionRequest.grant(pendingWebPermissionRequest.getResources());
            } else {
                pendingWebPermissionRequest.deny();
                Toast.makeText(this, "未授予麦克风/相机权限，语音或拍照功能暂不可用", Toast.LENGTH_SHORT).show();
            }
            pendingWebPermissionRequest = null;
        }
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_BACK && event.getAction() == KeyEvent.ACTION_DOWN) {
            handleSmartBack();
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }

    private void handleSmartBack() {
        if (offlineOverlay.getVisibility() == View.VISIBLE) {
            finish();
            return;
        }
        String closeModalJs = "(function(){"
                + "var fo=document.getElementById('focusOverlay');"
                + "if(fo&&fo.open){if(typeof exitImmersiveLock==='function')exitImmersiveLock();else fo.close();return 'closed';}"
                + "var ids=['confirmDialogModal','mediaLightboxModal','noteDetailModal','detailModal','prefsModal','calSlotModal','countdownEditorModal','askFloatPanel'];"
                + "for(var i=0;i<ids.length;i++){"
                + "  var el=document.getElementById(ids[i]);"
                + "  if(el&&el.classList.contains('open')){el.classList.remove('open');return 'closed';}"
                + "}"
                + "return 'none';"
                + "})();";
        webView.evaluateJavascript(closeModalJs, value -> {
            if (value != null && value.contains("closed")) {
                return;
            }
            if (webView.canGoBack()) {
                webView.goBack();
                return;
            }
            long now = System.currentTimeMillis();
            if (now - lastBackPressMs < 2000L) {
                finish();
            } else {
                lastBackPressMs = now;
                Toast.makeText(MainActivity.this, "再按一次返回退出 TraceTodo", Toast.LENGTH_SHORT).show();
            }
        });
    }

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (webView != null) webView.onResume();
    }

    @Override
    protected void onPause() {
        if (webView != null) webView.onPause();
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        if (webView != null) {
            webView.destroy();
        }
        super.onDestroy();
    }
}
