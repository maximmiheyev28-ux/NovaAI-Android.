package com.novaai.app

import android.app.ActivityManager
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
 * Private on-device inference. Model weights are downloaded once and verified before use.
 * Prompts are not sent to a server in local mode.
 */
object LocalLlm {
    // Pin a model revision so a remote file change cannot silently break installation.
    // A 3B Q4 model is a safer default for phones where only ~5 GB RAM is currently available.
    // The revision and SHA-256 are pinned so a changed upstream file cannot silently be accepted.
    private const val MODEL_URL =
        "https://huggingface.co/bartowski/Qwen2.5-3B-Instruct-GGUF/resolve/cce4abc6ec341d7091b89d3d851b9445b954ff81/Qwen2.5-3B-Instruct-Q4_K_M.gguf"
    private const val MODEL_FILE = "Qwen2.5-3B-Instruct-Q4_K_M.gguf"
    private const val MIN_MODEL_BYTES = 1_800_000_000L
    private const val ESTIMATED_MODEL_BYTES = 1_930_000_000L
    private const val DISK_SAFETY_BYTES = 400_000_000L
    private const val EXPECTED_SHA256 =
        "9c9f56a391a3abbd5b89d0245bf6106081bcc3173119d4229235dd9d23253f94"
    private const val MIN_FREE_MEMORY_BYTES = 2_500_000_000L

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val modelMutex = Mutex()
    private val inferenceMutex = Mutex()
    @Volatile private var loadedModel: LlamaModel? = null

    fun generate(context: Context, requestJson: String, callback: MainActivity.LocalCallback) {
        scope.launch {
            try {
                val modelFile = modelFile(context)
                if (!modelFile.exists() || modelFile.length() < MIN_MODEL_BYTES) {
                    if (modelFile.exists()) modelFile.delete()
                    val removedOldModel = ensureDownloadSpace(modelFile)
                    if (removedOldModel) callback.onProgress("Освобождаю место от старой модели…")
                    callback.onProgress("Скачиваю Qwen2.5 3B — примерно 1,93 ГБ. Загрузка нужна только один раз.")
                    downloadModel(modelFile) { percent ->
                        callback.onProgress("Загрузка Qwen2.5 3B: $percent%")
                    }
                }

                val response = inferenceMutex.withLock {
                    // Keep the inference lock from model lookup/load through completion so
                    // onTrimMemory cannot unload native weights between these operations.
                    val model = modelMutex.withLock {
                        val existing = loadedModel
                        if (existing != null && existing.isLoaded) {
                            existing
                        } else {
                            val freeBytes = availableMemoryBytes(context)
                            if (freeBytes < MIN_FREE_MEMORY_BYTES) {
                                throw IllegalStateException(
                                    "Недостаточно свободной оперативной памяти. Сейчас доступно " +
                                        formatGb(freeBytes) +
                                        " ГБ. Для этой модели нужно хотя бы около 2,5 ГБ доступной системной памяти. " +
                                        "Закрой тяжёлые приложения и попробуй снова."
                                )
                            }
                            var loaded: LlamaModel? = null
                            var lastLoadError: Throwable? = null
                            for (contextSize in recommendedContextSizes(freeBytes)) {
                                callback.onProgress(
                                    "Подготавливаю Qwen2.5 3B · контекст $contextSize · CPU-режим"
                                )
                                try {
                                    loaded = Llama.loadModel(
                                        modelFile.absolutePath,
                                        LlamaConfig(contextSize = contextSize, threads = recommendedThreads())
                                    )
                                    break
                                } catch (t: Throwable) {
                                    lastLoadError = t
                                    val memoryFailure = t is OutOfMemoryError ||
                                        t.message?.let { message ->
                                            message.contains("memory", ignoreCase = true) ||
                                                message.contains("alloc", ignoreCase = true) ||
                                                message.contains("памяти", ignoreCase = true)
                                        } == true
                                    if (!memoryFailure) throw t
                                    System.gc()
                                }
                            }
                            (loaded ?: throw IllegalStateException(
                                "Не удалось загрузить модель даже с уменьшенным контекстом. " +
                                    (lastLoadError?.message ?: "Освободи оперативную память и повтори попытку."),
                                lastLoadError
                            )).also { loadedModel = it }
                        }
                    }
                    callback.onProgress("Qwen2.5 3B готова. Формирую ответ на устройстве…")
                    val (systemPrompt, prompt) = makePrompt(requestJson)
                    Llama.complete(model, prompt, systemPrompt = systemPrompt, maxTokens = 640)
                }
                val cleanAnswer = removePrivateThinking(response.text).trim()
                if (cleanAnswer.isBlank()) {
                    throw IllegalStateException(
                        "Модель не выдала готовый ответ. Повтори запрос или сократи его."
                    )
                }
                val result = JSONObject()
                    .put("ok", true)
                    .put("answer", cleanAnswer)
                    .put("provider", "Qwen2.5 3B · локально")
                    .put("tokensGenerated", response.tokensGenerated)
                    .put("tokensPerSecond", response.tokensPerSecond)
                withContext(Dispatchers.Main) { callback.onResult(result.toString()) }
            } catch (t: Throwable) {
                val message = when {
                    t.message?.contains("Unable to resolve host", true) == true ||
                        t.message?.contains("Connect", true) == true ->
                        "Не удалось скачать модель. Проверь подключение к интернету и повтори запрос."
                    t.message?.contains("SHA-256", true) == true ->
                        "Проверка целостности не пройдена. Повтори запрос, чтобы скачать модель заново."
                    t.message?.contains("Недостаточно свободной оперативной памяти", true) == true ->
                        t.message ?: "Недостаточно свободной оперативной памяти."
                    else -> "Локальная модель не запустилась: ${t.message ?: t.javaClass.simpleName}"
                }
                val result = JSONObject().put("ok", false).put("error", message)
                withContext(Dispatchers.Main) { callback.onResult(result.toString()) }
            }
        }
    }

    fun status(context: Context): JSONObject {
        val file = modelFile(context)
        val memory = availableMemoryBytes(context)
        return JSONObject()
            .put("ready", file.exists() && file.length() >= MIN_MODEL_BYTES)
            .put("model", "Qwen2.5 3B Instruct · Q4_K_M")
            .put("sizeBytes", if (file.exists()) file.length() else 0L)
            .put("sizeLabel", "≈1,93 ГБ")
            .put("availableMemoryBytes", memory)
            .put("minimumFreeMemoryBytes", MIN_FREE_MEMORY_BYTES)
            .put("recommendedContext", recommendedContextSizes(memory).first())
            .put("offlineAfterDownload", true)
    }

    fun unload() {
        scope.launch {
            // Wait for any active inference before releasing native model memory.
            inferenceMutex.withLock {
                modelMutex.withLock {
                    loadedModel?.let { runCatching { Llama.releaseModel(it) } }
                    loadedModel = null
                }
            }
        }
    }

    private fun modelFile(context: Context): File {
        val root = context.getExternalFilesDir("models") ?: File(context.filesDir, "models")
        if (!root.exists() && !root.mkdirs()) {
            throw IllegalStateException("Не удалось создать папку модели")
        }
        return File(root, MODEL_FILE)
    }

    /** Frees space left by the previous multi-gigabyte model only when the download needs it. */
    private fun ensureDownloadSpace(target: File): Boolean {
        val root = target.parentFile ?: throw IllegalStateException("Не найдена папка модели")
        val part = File(root, target.name + ".part")
        val alreadyDownloaded = if (part.exists()) part.length() else 0L
        val remaining = (ESTIMATED_MODEL_BYTES - alreadyDownloaded).coerceAtLeast(0L)
        val requiredFree = remaining + DISK_SAFETY_BYTES
        var free = root.usableSpace
        var removedOldModel = false

        if (free < requiredFree) {
            root.listFiles()?.filter { file ->
                file.isFile && file.name != target.name && file.name != part.name &&
                    file.name.startsWith("Qwen") &&
                    (file.name.endsWith(".gguf", ignoreCase = true) ||
                        file.name.endsWith(".gguf.part", ignoreCase = true))
            }?.forEach { file ->
                if (file.delete()) removedOldModel = true
            }
            free = root.usableSpace
        }
        if (free < requiredFree) {
            val missingGb = formatGb(requiredFree - free)
            throw IllegalStateException(
                "Недостаточно места для модели. Освободи ещё примерно $missingGb ГБ памяти устройства и повтори загрузку."
            )
        }
        return removedOldModel
    }

    private fun removeObsoleteModels(keep: File) {
        keep.parentFile?.listFiles()?.filter { file ->
            file.isFile && file.absolutePath != keep.absolutePath &&
                file.name != keep.name + ".part" && file.name.startsWith("Qwen") &&
                (file.name.endsWith(".gguf", ignoreCase = true) ||
                    file.name.endsWith(".gguf.part", ignoreCase = true))
        }?.forEach { file -> runCatching { file.delete() } }
    }

    private fun availableMemoryBytes(context: Context): Long {
        val manager = context.getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
        val info = ActivityManager.MemoryInfo()
        manager.getMemoryInfo(info)
        return info.availMem
    }

    private fun recommendedContextSizes(freeBytes: Long): List<Int> {
        // Start conservatively and retry with a smaller KV cache if allocation fails.
        return when {
            freeBytes >= 5_500_000_000L -> listOf(4096, 2048, 1024)
            freeBytes >= 3_500_000_000L -> listOf(2048, 1024)
            else -> listOf(1024)
        }
    }

    private fun recommendedThreads(): Int {
        // A small reserve reduces thermal throttling on a phone during long answers.
        return Runtime.getRuntime().availableProcessors().coerceIn(4, 6)
    }

    private fun formatGb(bytes: Long): String = String.format(
        java.util.Locale.ROOT, "%.1f", bytes.toDouble() / 1_000_000_000.0
    )

    private fun removePrivateThinking(text: String): String {
        val source = text.trim()
        val close = source.lastIndexOf("</think>")
        if (close >= 0) return source.substring(close + "</think>".length).trim()
        val open = source.indexOf("<think>")
        if (open >= 0) return source.substring(0, open).trim()
        return source
            .replace("<|im_start|>assistant", "")
            .replace("<|im_end|>", "")
            .trim()
    }

    private fun makePrompt(requestJson: String): Pair<String, String> {
        val root = JSONObject(requestJson)
        val messages = root.optJSONArray("messages")
        var system = """
            Ты — Nova, внимательный и компетентный ИИ-помощник. Тщательно анализируй задачу и проверяй результат перед ответом.
            Отвечай на языке пользователя. Сначала дай прямой ответ, затем объяснение или шаги, если они нужны.
            Для математики показывай необходимые вычисления и проверку результата; для программирования давай рабочий, законченный код с кратким объяснением.
            Не выдумывай факты, источники, выполненные действия или возможности. Если данных не хватает, обозначь неопределённость и укажи, что именно нужно проверить.
            Сохраняй важный контекст беседы, не повторяй вопрос и не подменяй решение общими определениями.
            Не выводи скрытые внутренние рассуждения дословно: вместо этого сообщай краткое обоснование, проверяемые шаги и вывод.
            Будь ясным и достаточно подробным, но не растягивай простой ответ.
        """.trimIndent()
        if (messages == null || messages.length() == 0) {
            return system to requestJson.take(12000)
        }

        val first = messages.optJSONObject(0)
        val start = if (first?.optString("role") == "system") 1 else 0
        if (start == 1) {
            val provided = first?.optString("content").orEmpty()
            if (provided.isNotBlank()) system += "\n\nИнструкции приложения: $provided"
        }

        val transcript = StringBuilder()
        val from = maxOf(start, messages.length() - 13)
        for (i in from until messages.length()) {
            val item = messages.optJSONObject(i) ?: continue
            val role = item.optString("role")
            if (role != "user" && role != "assistant") continue
            val speaker = if (role == "assistant") "Nova" else "Пользователь"
            val value = item.optString("content").take(3500)
            transcript.append(speaker).append(": ").append(value).append("\n\n")
        }
        return system to transcript.toString().takeLast(10000)
    }

    private suspend fun downloadModel(target: File, onProgress: (Int) -> Unit) =
        withContext(Dispatchers.IO) {
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
                        setRequestProperty("User-Agent", "NovaAI-Android/0.6")
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
                    val total = if (connection.contentLengthLong > 0L) {
                        startBytes + connection.contentLengthLong
                    } else {
                        -1L
                    }
                    connection.inputStream.use { input ->
                        FileOutputStream(part, append).use { output ->
                            val buffer = ByteArray(1024 * 128)
                            var lastPercent = -1
                            var downloaded = startBytes
                            while (true) {
                                val read = input.read(buffer)
                                if (read == -1) break
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
                    if (part.length() < MIN_MODEL_BYTES) {
                        throw IllegalStateException("Размер скачанной модели слишком мал")
                    }
                    val digest = sha256(part)
                    if (!digest.equals(EXPECTED_SHA256, ignoreCase = true)) {
                        part.delete()
                        throw IllegalStateException("SHA-256 не совпал")
                    }
                    onProgress(100)
                    if (target.exists()) target.delete()
                    if (!part.renameTo(target)) {
                        throw IllegalStateException("Не удалось сохранить модель")
                    }
                    removeObsoleteModels(target)
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
