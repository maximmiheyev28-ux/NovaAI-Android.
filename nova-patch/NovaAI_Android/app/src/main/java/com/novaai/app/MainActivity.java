package com.novaai.app;

import android.app.Activity;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.WindowManager;
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

    public interface LocalCallback {
        void onProgress(String progress);
        void onResult(String result);
    }

    public class NovaBridge {
        @JavascriptInterface
        public void localChat(String requestJson, String callbackId) {
            try {
                LocalLlm.INSTANCE.generate(getApplicationContext(), requestJson, new LocalCallback() {
                    @Override public void onProgress(String progress) {
                        mainHandler.post(() -> {
                            if (webView != null) webView.evaluateJavascript(
                                "window.__novaLocalProgress && window.__novaLocalProgress(" +
                                JSONObject.quote(callbackId) + "," + JSONObject.quote(progress) + ")", null);
                        });
                    }
                    @Override public void onResult(String result) {
                        mainHandler.post(() -> {
                            if (webView != null) webView.evaluateJavascript(
                                "window.__novaApiCallback(" + JSONObject.quote(callbackId) + "," +
                                JSONObject.quote(result) + ")", null);
                        });
                    }
                });
            } catch (Exception e) {
                try {
                    JSONObject error = new JSONObject().put("ok", false).put("error",
                        e.getMessage() == null ? "Ошибка локальной модели" : e.getMessage());
                    mainHandler.post(() -> {
                        if (webView != null) webView.evaluateJavascript(
                            "window.__novaApiCallback(" + JSONObject.quote(callbackId) + "," +
                            JSONObject.quote(error.toString()) + ")", null);
                    });
                } catch (Exception ignored) { }
            }
        }

        @JavascriptInterface
        public String getLocalModelStatus() {
            try { return LocalLlm.INSTANCE.status(getApplicationContext()).toString(); }
            catch (Exception e) {
                return "{\"ready\":false,\"error\":\"Не удалось получить статус модели\"}";
            }
        }

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
                    connection.setReadTimeout(90000);
                    connection.setDoOutput(true);
                    connection.setUseCaches(false);
                    connection.setRequestProperty("Content-Type", "application/json");
                    connection.setRequestProperty("Accept", "application/json");
                    if (!key.isEmpty()) connection.setRequestProperty("Authorization", "Bearer " + key);
                    if (endpoint.toLowerCase(java.util.Locale.ROOT).contains("openrouter.ai")) {
                        connection.setRequestProperty("HTTP-Referer", "https://github.com/maximmiheyev28-ux/NovaAI-Android.");
                        connection.setRequestProperty("X-Title", "Nova AI");
                    }
                    JSONObject body = new JSONObject();
                    body.put("model", model);
                    body.put("messages", req.getJSONArray("messages"));
                    body.put("temperature", 0.7);
                    body.put("max_tokens", 1200);
                    try (OutputStream out = connection.getOutputStream()) {
                        out.write(body.toString().getBytes(StandardCharsets.UTF_8));
                    }
                    int status = connection.getResponseCode();
                    InputStream stream = status >= 200 && status < 300
                        ? connection.getInputStream() : connection.getErrorStream();
                    StringBuilder responseText = new StringBuilder();
                    if (stream != null) {
                        try (BufferedReader reader = new BufferedReader(
                                new InputStreamReader(stream, StandardCharsets.UTF_8))) {
                            String line;
                            while ((line = reader.readLine()) != null) responseText.append(line);
                        }
                    }
                    String rawResponse = responseText.toString();
                    if (status < 200 || status >= 300) {
                        throw new Exception("API вернул HTTP " + status + ". " +
                            rawResponse.substring(0, Math.min(500, rawResponse.length())));
                    }
                    JSONObject response = new JSONObject(rawResponse);
                    String answer = null;
                    JSONArray choices = response.optJSONArray("choices");
                    if (choices != null && choices.length() > 0) {
                        JSONObject choice = choices.optJSONObject(0);
                        JSONObject message = choice == null ? null : choice.optJSONObject("message");
                        if (message != null) {
                            Object content = message.opt("content");
                            if (content instanceof String) {
                                answer = (String) content;
                            } else if (content instanceof JSONArray) {
                                JSONArray parts = (JSONArray) content;
                                StringBuilder combined = new StringBuilder();
                                for (int i = 0; i < parts.length(); i++) {
                                    JSONObject part = parts.optJSONObject(i);
                                    if (part != null) combined.append(part.optString("text", ""));
                                }
                                answer = combined.toString();
                            }
                        }
                    }
                    if (answer == null || answer.trim().isEmpty()) {
                        answer = response.optString("output_text", null);
                    }
                    if (answer == null || answer.trim().isEmpty()) {
                        throw new Exception("Ответ API не содержит текста или использует несовместимый формат.");
                    }
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
        getWindow().setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE);
        getWindow().setStatusBarColor(0xFF212121);
        getWindow().setNavigationBarColor(0xFF212121);
        webView = new WebView(this);
        webView.setBackgroundColor(0xFF212121);
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