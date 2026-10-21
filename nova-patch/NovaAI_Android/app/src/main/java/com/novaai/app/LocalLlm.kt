package com.novaai.app

import android.content.Context
import dev.ffmpegkit.llama.Llama
import dev.ffmpegkit.llama.LlamaConfig
import dev.ffmpegkit.llama.LlamaModel
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

/**
 * Genuine on-device model runtime. Model weights are downloaded once to app-private storage.
 * Generation after the first download is fully offline and does not send prompts to a server.
 */
object LocalLlm {
    private const val MODEL_URL =
        "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_0.gguf"
    private const val MODEL_FILE = "qwen2.5-0.5b-instruct-q4_0.gguf"
    private const val MIN_MODEL_BYTES = 400_000_000L
    private const val EXPECTED_SHA256 = "7671c0c304e6ce5a7fc577bcb12aba01e2c155cc2efd29b2213c95b18edaf6ed"

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val modelMutex = Mutex()
    private val inferenceMutex = Mutex()
    @Volatile private var loadedModel: LlamaModel? = null

    fun generate(context: Context, requestJson: String, callback: MainActivity.LocalCallback) {
        scope.launch {
            try {
                val modelFile = modelFile(context)
                if (!modelFile.exists() || modelFile.length() < MIN_MODEL_BYTES) {
                    callback.onProgress("Скачивание локальной модели Qwen — около 429 МБ. Это делается один раз.")
                    downloadModel(modelFile) { percent ->
                        callback.onProgress("Загрузка локальной модели: $percent%")
                    }
                }
                val model = modelMutex.withLock {
                    val existing = loadedModel
                    if (existing != null && existing.isLoaded) existing
                    else Llama.loadModel(
                        modelFile.absolutePath,
                        LlamaConfig(contextSize = 2048, threads = recommendedThreads())
                    ).also { loadedModel = it }
                }
                callback.onProgress("Модель загружена. Генерирую ответ на устройстве…")
                val (systemPrompt, prompt) = makePrompt(requestJson)
                val response = inferenceMutex.withLock {
                    Llama.complete(model, prompt, systemPrompt = systemPrompt, maxTokens = 384)
                }
                val result = JSONObject()
                    .put("ok", true)
                    .put("answer", response.text.trim())
                    .put("provider", "Локальная Qwen 0.5B")
                    .put("tokensGenerated", response.tokensGenerated)
                    .put("tokensPerSecond", response.tokensPerSecond)
                withContext(Dispatchers.Main) { callback.onResult(result.toString()) }
            } catch (t: Throwable) {
                val message = when {
                    t.message?.contains("Unable to resolve host", true) == true ||
                    t.message?.contains("Connect", true) == true ->
                        "Не удалось скачать модель. Проверь подключение к интернету и повтори запрос."
                    t.message?.contains("SHA-256", true) == true ->
                        "Загрузка модели повреждена. Повтори запрос, чтобы скачать файл заново."
                    else -> "Локальная модель не запустилась: ${t.message ?: t.javaClass.simpleName}"
                }
                val result = JSONObject().put("ok", false).put("error", message)
                withContext(Dispatchers.Main) { callback.onResult(result.toString()) }
            }
        }
    }

    fun status(context: Context): JSONObject {
        val file = modelFile(context)
        return JSONObject()
            .put("ready", file.exists() && file.length() >= MIN_MODEL_BYTES)
            .put("model", "Qwen2.5 0.5B Instruct · Q4_0")
            .put("sizeBytes", if (file.exists()) file.length() else 0L)
            .put("offlineAfterDownload", true)
    }

    fun unload() {
        scope.launch {
            modelMutex.withLock {
                loadedModel?.let { runCatching { Llama.releaseModel(it) } }
                loadedModel = null
            }
        }
    }

    private fun modelFile(context: Context): File {
        val root = context.getExternalFilesDir("models") ?: File(context.filesDir, "models")
        if (!root.exists() && !root.mkdirs()) throw IllegalStateException("Не удалось создать папку модели")
        return File(root, MODEL_FILE)
    }

    private fun recommendedThreads(): Int {
        val cores = Runtime.getRuntime().availableProcessors()
        return cores.coerceIn(2, 6)
    }

    private fun makePrompt(requestJson: String): Pair<String, String> {
        val root = JSONObject(requestJson)
        val messages = root.optJSONArray("messages")
        var system = "Ты — Nova, локальный ИИ-помощник. Отвечай по-русски, ясно и полезно. Не придумывай факты; обозначай неопределённость."
        if (messages == null || messages.length() == 0) return system to requestJson.take(12000)

        val first = messages.optJSONObject(0)
        if (first?.optString("role") == "system") {
            val provided = first.optString("content")
            if (provided.isNotBlank()) system = system + "\n" + provided
        }
        val start = if (first?.optString("role") == "system") 1 else 0
        val transcript = StringBuilder()
        val from = (start until messages.length()).maxOfOrNull { it }?.let { maxOf(start, it - 11) } ?: start
        for (i in from until messages.length()) {
            val item = messages.optJSONObject(i) ?: continue
            val role = item.optString("role")
            if (role != "user" && role != "assistant") continue
            val speaker = if (role == "assistant") "Nova" else "Пользователь"
            val value = item.optString("content").take(3000)
            transcript.append(speaker).append(": ").append(value).append("\n\n")
        }
        return system to transcript.toString().takeLast(9000)
    }

    private suspend fun downloadModel(target: File, onProgress: (Int) -> Unit) = withContext(Dispatchers.IO) {
        val part = File(target.parentFile, target.name + ".part")
        var attempt = 0
        while (attempt < 3) {
            attempt++
            var connection: HttpURLConnection? = null
            try {
                val existing = if (part.exists()) part.length() else 0L
                connection = (URL(MODEL_URL + "?download=true").openConnection() as HttpURLConnection).apply {
                    connectTimeout = 20000
                    readTimeout = 45000
                    instanceFollowRedirects = true
                    setRequestProperty("User-Agent", "NovaAI-Android/0.4")
                    if (existing > 0L) setRequestProperty("Range", "bytes=$existing-")
                }
                connection.connect()
                val code = connection.responseCode
                if (code != HttpURLConnection.HTTP_OK && code != HttpURLConnection.HTTP_PARTIAL) {
                    throw IllegalStateException("Сервер модели вернул HTTP $code")
                }
                val append = existing > 0L && code == HttpURLConnection.HTTP_PARTIAL
                if (existing > 0L && code == HttpURLConnection.HTTP_OK) part.delete()
                val startBytes = if (append) existing else 0L
                val total = if (connection.contentLengthLong > 0L) startBytes + connection.contentLengthLong else -1L
                connection.inputStream.use { input ->
                    FileOutputStream(part, append).use { output ->
                        val buffer = ByteArray(1024 * 128)
                        var read: Int
                        var lastPercent = -1
                        var downloaded = startBytes
                        while (input.read(buffer).also { read = it } != -1) {
                            output.write(buffer, 0, read)
                            downloaded += read
                            if (total > 0L) {
                                val percent = ((downloaded * 100L) / total).toInt().coerceIn(0, 100)
                                if (percent != lastPercent) {
                                    lastPercent = percent
                                    withContext(Dispatchers.Main) { onProgress(percent) }
                                }
                            }
                        }
                        output.fd.sync()
                    }
                }
                if (part.length() < MIN_MODEL_BYTES) throw IllegalStateException("Размер скачанной модели слишком мал")
                onProgress(100)
                val digest = sha256(part)
                if (!digest.equals(EXPECTED_SHA256, ignoreCase = true)) {
                    part.delete()
                    throw IllegalStateException("SHA-256 не совпал")
                }
                if (target.exists()) target.delete()
                if (!part.renameTo(target)) throw IllegalStateException("Не удалось сохранить модель")
                return@withContext
            } catch (e: Throwable) {
                if (attempt >= 3 || e.message?.contains("SHA-256", true) == true) throw e
                kotlinx.coroutines.delay(800L * attempt)
            } finally {
                connection?.disconnect()
            }
        }
    }

    private fun sha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().buffered(1024 * 1024).use { input ->
            val buffer = ByteArray(1024 * 1024)
            while (true) {
                val n = input.read(buffer)
                if (n <= 0) break
                digest.update(buffer, 0, n)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }
}
