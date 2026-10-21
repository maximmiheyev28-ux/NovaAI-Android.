package com.novaai.app;

import android.app.Activity;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class MainActivity extends Activity {
    private WebView webView;
    private final ExecutorService network = Executors.newFixedThreadPool(3);
    private final Handler mainHandler = new Handler(Looper.getMainLooper());

    public class NovaBridge {
        @JavascriptInterface
        public void chat(String requestJson, String callbackId) {
            network.execute(() -> {
                String result;
                HttpURLConnection connection = null;
                try {
                    JSONObject req = new JSONObject(requestJson);
                    String endpoint = req.getString("endpoint");
                    String model = req.getString("modelName");
                    String key = req.optString("apiKey", "");
                    if (!endpoint.startsWith("https://")) throw new Exception("Для безопасности endpoint должен использовать HTTPS.");
                    if (model.trim().isEmpty()) throw new Exception("Укажи идентификатор модели в настройках.");
                    connection = (HttpURLConnection) new URL(endpoint).openConnection();
                    connection.setRequestMethod("POST");
                    connection.setConnectTimeout(20000);
                    connection.setReadTimeout(60000);
                    connection.setDoOutput(true);
                    connection.setRequestProperty("Content-Type", "application/json");
                    if (!key.isEmpty()) connection.setRequestProperty("Authorization", "Bearer " + key);
                    JSONObject body = new JSONObject();
                    body.put("model", model);
                    body.put("messages", req.getJSONArray("messages"));
                    body.put("temperature", 0.7);
                    body.put("max_tokens", 1200);
                    try (OutputStream out = connection.getOutputStream()) {
                        out.write(body.toString().getBytes(StandardCharsets.UTF_8));
                    }
                    int status = connection.getResponseCode();
                    InputStream stream = status >= 200 && status < 300 ? connection.getInputStream() : connection.getErrorStream();
                    StringBuilder text = new StringBuilder();
                    try (BufferedReader reader = new BufferedReader(new InputStreamReader(stream, StandardCharsets.UTF_8))) {
                        String line;
                        while ((line = reader.readLine()) != null) text.append(line);
                    }
                    if (status < 200 || status >= 300) {
                        throw new Exception("API вернул HTTP " + status + ". " + text.toString().substring(0, Math.min(350, text.length())));
                    }
                    JSONObject response = new JSONObject(text.toString());
                    String answer = null;
                    JSONArray choices = response.optJSONArray("choices");
                    if (choices != null && choices.length() > 0) {
                        answer = choices.getJSONObject(0).getJSONObject("message").optString("content", null);
                    }
                    if (answer == null || answer.trim().isEmpty()) answer = response.optString("output_text", null);
                    if (answer == null || answer.trim().isEmpty()) throw new Exception("Ответ API не содержит текста или использует несовместимый формат.");
                    JSONObject out = new JSONObject();
                    out.put("ok", true);
                    out.put("answer", answer);
                    result = out.toString();
                } catch (Exception e) {
                    try {
                        JSONObject out = new JSONObject();
                        out.put("ok", false);
                        out.put("error", e.getMessage() == null ? "Неизвестная ошибка подключения" : e.getMessage());
                        result = out.toString();
                    } catch (Exception ignored) {
                        result = "{\"ok\":false,\"error\":\"Ошибка API\"}";
                    }
                } finally {
                    if (connection != null) connection.disconnect();
                }
                final String payload = result;
                mainHandler.post(() -> {
                    if (webView != null) webView.evaluateJavascript(
                        "window.__novaApiCallback(" + JSONObject.quote(callbackId) + "," + JSONObject.quote(payload) + ")", null);
                });
            });
        }
    }

    @Override public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().setStatusBarColor(0xFF10121A);
        getWindow().setNavigationBarColor(0xFF10121A);
        webView = new WebView(this);
        webView.setBackgroundColor(0xFF0D0F16);
        WebSettings s = webView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setAllowFileAccess(true);
        s.setAllowContentAccess(false);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        s.setSupportZoom(false);
        webView.setWebViewClient(new WebViewClient());
        webView.setWebChromeClient(new WebChromeClient());
        webView.addJavascriptInterface(new NovaBridge(), "NovaBridge");
        setContentView(webView);
        webView.loadUrl("file:///android_asset/www/index.html");
    }

    @Override public void onBackPressed() {
        if (webView != null && webView.canGoBack()) webView.goBack();
        else super.onBackPressed();
    }

    @Override protected void onDestroy() {
        if (webView != null) {
            webView.loadUrl("about:blank");
            webView.destroy();
            webView = null;
        }
        network.shutdownNow();
        super.onDestroy();
    }
}