package com.bonuos.facebench.bench;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Matrix;
import android.media.ExifInterface;
import android.os.Build;
import com.bonuos.facebench.backend.BackendRegistry;
import com.bonuos.facebench.backend.BackendEvent;
import com.bonuos.facebench.backend.BenchConfig;
import com.bonuos.facebench.backend.FaceBackend;
import com.bonuos.facebench.backend.FrameOutcome;
import com.bonuos.facebench.backend.Timer;
import com.bonuos.facebench.clip.ClipFile;
import com.bonuos.facebench.clip.ClipMeta;
import com.google.gson.GsonBuilder;
import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Date;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * Replays every recorded clip through each selected backend, one backend at a time so they never
 * compete for CPU or memory, and writes CSVs for tools/face-eval.
 *
 * <p>Directory layout under the app's external files dir:
 * clips/*.fclip + *.json (recorded attempts), enroll/&lt;subjectId&gt;.jpg|png (registration photos),
 * runs/&lt;runId&gt;/ (output).
 */
public final class BenchRunner {
    public interface Progress {
        void update(String message);
        boolean cancelled();
    }

    public static File clipsDir(File root) { return new File(root, "clips"); }
    public static File enrollDir(File root) { return new File(root, "enroll"); }
    public static File runsDir(File root) { return new File(root, "runs"); }

    public File run(Context context, File root, BenchConfig config, Progress progress) throws Exception {
        config.validate();
        List<File> clips = listClips(clipsDir(root));
        if (clips.isEmpty()) throw new IllegalStateException("No recorded clips in " + clipsDir(root));
        String runId = new SimpleDateFormat("yyyyMMdd-HHmmss", Locale.ROOT).format(new Date());
        File out = new File(runsDir(root), runId);
        if (!out.mkdirs()) throw new IOException("Cannot create " + out);

        Map<String, Object> run = new LinkedHashMap<>();
        run.put("schemaVersion", 2);
        run.put("eventSchemaVersion", BackendEvent.SCHEMA_VERSION);
        run.put("diagnosticCoverage", "recognition/valid_liveness_completions, observable_worker_errors, capture_counts_bytes; "
                + "not full stage/frame/task/generation trace; liveness worker errors may have unknown track/frame IDs");
        run.put("completionSchemaVersion", 1);
        run.put("runId", runId);
        run.put("startedAtEpochMs", System.currentTimeMillis());
        run.put("device", deviceInfo());
        run.put("config", config);
        run.put("configSha256", HashManifest.fingerprint(config));
        run.put("clipCount", clips.size());
        run.put("lifePhotoChecked", !config.arcSoftIdCardModel);
        run.put("eventTimeOrigin", "monotonic_wall_ms_since_first_replay_frame");
        run.put("eventWindow", "elapsed_ms <= recorded_duration_ms; never clamp tail time");
        run.put("sessionTiming", "detect_ms = whole onFrame/analysis call; preprocess_ms = detector subset; "
                + "liveness_ms/feature_ms = analysis capture subsets, not background inference; never sum columns");
        List<Map<String, Object>> planned = new ArrayList<>();
        List<ClipCompletion> completed = new ArrayList<>();
        Map<String, String> descriptions = new LinkedHashMap<>(), failures = new LinkedHashMap<>();
        Map<String, List<String>> enrolledIds = new LinkedHashMap<>();
        run.put("plannedBackends", new ArrayList<>(config.backends));
        run.put("plannedBackendClips", planned);
        run.put("actualBackendClips", completed);
        run.put("actualEnrolledIds", enrolledIds);
        run.put("backends", descriptions);
        run.put("failures", failures);
        run.put("finished", false);
        run.put("cancelled", false);
        writeJson(new File(out, "run.json"), run);
        boolean cancelled = false;
        try {
            for (File clip : clips) {
                ClipMeta meta = ClipMeta.read(metaFile(clip));
                for (String name : config.backends) {
                    Map<String, Object> item = new LinkedHashMap<>();
                    item.put("backend", name); item.put("clipId", meta.clipId);
                    item.put("subjectId", meta.subjectId); item.put("label", meta.label);
                    item.put("attackType", meta.attackType == null ? "" : meta.attackType);
                    item.put("frameCount", meta.frameCount); item.put("durationMs", meta.durationMs);
                    planned.add(item);
                }
            }
            run.put("manifest", manifest(context, root, clips));
            writeJson(new File(out, "run.json"), run);
            try (CsvWriter frames = new CsvWriter(new File(out, "frames.csv"), "backend", "clip_id", "subject_id", "label",
                    "attack_type", "frame_index", "timestamp_ns", "face_count", "box_left", "box_top", "box_right", "box_bottom",
                    "preprocess_ms", "detect_ms", "liveness_ms", "feature_ms", "live_score", "live_status",
                    "feature_attempted", "feature_ok", "warmup", "note");
                 CsvWriter sweep = new CsvWriter(new File(out, "liveness_sweep.csv"), "backend", "clip_id", "frame_index", "threshold", "status");
                 CsvWriter scores = new CsvWriter(new File(out, "scores.csv"), "backend", "clip_id", "frame_index", "subject_id", "label", "enrolled_id", "score");
                 CsvWriter enroll = new CsvWriter(new File(out, "enroll.csv"), "backend", "enrolled_id", "ok", "ms", "note");
                 CsvWriter events = new CsvWriter(new File(out, "events.csv"), "schema_version", "backend", "clip_id", "epoch",
                         "sequence", "track_id", "frame_id", "kind", "expected_identity", "actual_identity", "elapsed_ms",
                         "score", "live_status", "error", "window")) {
                for (String name : config.backends) {
                    if (progress.cancelled()) { cancelled = true; break; }
                    progress.update("加载后端 " + name);
                    try (FaceBackend backend = BackendRegistry.create(context, name, config)) {
                        descriptions.put(name, backend.describe());
                        Map<String, Object> references = enrollAll(backend, enrollDir(root), enroll, progress);
                        enrolledIds.put(name, new ArrayList<>(references.keySet()));
                        int clipNumber = 0;
                        for (File clipFile : clips) {
                            if (progress.cancelled()) { cancelled = true; break; }
                            ClipMeta meta = ClipMeta.read(metaFile(clipFile));
                            progress.update(name + "：片段 " + (++clipNumber) + "/" + clips.size() + " " + meta.clipId);
                            ClipCompletion completion = new ClipCompletion(name, meta.clipId);
                            completed.add(completion);
                            replayClip(backend, clipFile, meta, config, references, frames, sweep, scores,
                                    events, progress, completion);
                            cancelled |= completion.cancelled;
                            frames.flush(); events.flush();
                            writeCompletions(out, completed);
                            if (!completion.drainOk || !completion.error.isEmpty())
                                throw new IllegalStateException("Clip incomplete: " + completion.error + " " + completion.drainError);
                            if (cancelled) break;
                        }
                    } catch (Exception error) {
                        failures.put(name, error.toString());
                        progress.update(name + " 失败：" + error.getMessage());
                    }
                }
            }
            run.put("finished", true); // Finished execution is distinct from complete/eligible coverage.
        } catch (Exception error) {
            failures.put("runner", error.toString());
            throw error;
        } finally {
            run.put("cancelled", cancelled || progress.cancelled());
            run.put("finishedAtEpochMs", System.currentTimeMillis());
            run.put("frameErrors", completed.stream().mapToInt(c -> c.frameErrors).sum());
            run.put("drainFailures", completed.stream().filter(c -> !c.drainOk).count());
            run.put("eventOverflow", completed.stream().mapToLong(c -> c.eventOverflow).sum());
            run.put("complete", Boolean.TRUE.equals(run.get("finished")) && !Boolean.TRUE.equals(run.get("cancelled"))
                    && failures.isEmpty() && completed.size() == planned.size() && completed.stream().allMatch(ClipCompletion::complete));
            writeCompletions(out, completed);
            writeJson(new File(out, "run.json"), run);
        }
        return out;
    }

    static void replayClip(FaceBackend backend, File clipFile, ClipMeta meta, BenchConfig config,
                           Map<String, Object> references, CsvWriter frames, CsvWriter sweep, CsvWriter scores,
                           CsvWriter events, Progress progress, ClipCompletion completion) throws Exception {
        try {
            backend.clipTiming(meta.frameCount, meta.durationMs);
            completion.effectiveConfig = backend.effectiveConfig();
            completion.effectiveConfigSha256 = HashManifest.fingerprint(completion.effectiveConfig);
            backend.beginClip(meta.sensorRotationDegrees);
            completion.epoch = backend.clipEpoch();
            try (ClipFile.Reader reader = new ClipFile.Reader(clipFile)) {
                while (true) {
                    if (progress.cancelled()) {
                        completion.cancelled = true;
                        completion.stopReason = "cancelled";
                        break;
                    }
                    ClipFile.Frame frame = reader.next();
                    if (frame == null) {
                        completion.eof = true;
                        completion.stopReason = "eof";
                        break;
                    }
                    if (completion.receivedFrames > 0 && frame.timestampNs <= completion.lastTimestampNs)
                        completion.timestampErrors++;
                    if (completion.receivedFrames == 0) completion.firstTimestampNs = frame.timestampNs;
                    completion.lastTimestampNs = frame.timestampNs;
                    completion.receivedFrames++;
                    boolean runFeature = frame.index % config.featureEveryNth == 0;
                    FrameOutcome outcome;
                    try {
                        outcome = backend.process(frame, config.livenessEveryFrame, runFeature);
                    } catch (Exception error) {
                        outcome = new FrameOutcome();
                        outcome.livenessStatus = FrameOutcome.LIVE_ERROR;
                        outcome.note = "error: " + error;
                    }
                    if (outcome.livenessStatus == FrameOutcome.LIVE_ERROR || outcome.note.startsWith("error:"))
                        completion.frameErrors++;
                    if ("dropped".equals(outcome.note)) completion.droppedFrames++;
                    int[] box = outcome.box;
                    frames.row(backend.name(), meta.clipId, meta.subjectId, meta.label, meta.attackType, frame.index,
                            frame.timestampNs, outcome.faceCount, box == null ? null : box[0], box == null ? null : box[1],
                            box == null ? null : box[2], box == null ? null : box[3], outcome.preprocessMs, outcome.detectMs,
                            outcome.livenessMs, outcome.featureMs, outcome.livenessScore, outcome.livenessStatus,
                            outcome.featureAttempted ? 1 : 0, outcome.feature != null ? 1 : 0,
                            frame.index < config.warmupFramesPerClip ? 1 : 0, outcome.note);
                    for (Map.Entry<Float, Integer> entry : outcome.livenessStatusByThreshold.entrySet())
                        sweep.row(backend.name(), meta.clipId, frame.index, entry.getKey(), entry.getValue());
                    if (outcome.feature != null) {
                        for (Map.Entry<String, Object> reference : references.entrySet())
                            scores.row(backend.name(), meta.clipId, frame.index, meta.subjectId, meta.label,
                                    reference.getKey(), backend.compare(outcome.feature, reference.getValue()));
                    }
                    writeEvents(backend, meta, events, completion);
                }
            }
            if (completion.eof && completion.receivedFrames != meta.frameCount)
                completion.error = "Recorded frame count differs from metadata";
        } catch (Exception error) {
            completion.error = error.toString();
            completion.stopReason = "error";
        } finally {
            long started = System.nanoTime();
            try {
                completion.drainOk = backend.finishClip(config.finishTimeoutMs);
                if (!completion.drainOk) completion.drainError = "drain timeout or backend failed; handles quarantined";
            } catch (Exception error) { completion.drainError = error.toString(); }
            finally {
                completion.drainElapsedMs = (System.nanoTime() - started) / 1e6;
                completion.metrics = backend.clipMetrics();
                completion.eventOverflow = backend.eventOverflowCount();
                writeEvents(backend, meta, events, completion);
            }
        }
    }

    private static void writeEvents(FaceBackend backend, ClipMeta meta, CsvWriter csv, ClipCompletion completion) throws IOException {
        for (BackendEvent event : backend.drainEvents()) {
            boolean inWindow = event.elapsedMs <= meta.durationMs;
            csv.row(BackendEvent.SCHEMA_VERSION, backend.name(), meta.clipId, event.epoch, event.sequence,
                    event.trackId, event.frameId, event.kind, meta.subjectId, event.actualIdentity, event.elapsedMs,
                    event.score, event.livenessStatus, event.error, inWindow ? "recording" : "tail");
            completion.eventCount++;
            if ("error".equals(event.kind)) completion.eventErrors++;
            if (inWindow) completion.inWindowEvents++; else completion.tailEvents++;
        }
    }

    private static void writeCompletions(File out, List<ClipCompletion> clips) throws IOException {
        Map<String, Object> value = new LinkedHashMap<>();
        value.put("schemaVersion", 1);
        value.put("clips", clips);
        writeJson(new File(out, "clip-completion.json"), value);
    }

    private static void writeJson(File file, Object value) throws IOException {
        File temporary = new File(file.getParentFile(), file.getName() + ".tmp");
        Files.write(temporary.toPath(), new GsonBuilder().setPrettyPrinting().create().toJson(value).getBytes(StandardCharsets.UTF_8));
        try {
            Files.move(temporary.toPath(), file.toPath(), java.nio.file.StandardCopyOption.ATOMIC_MOVE,
                    java.nio.file.StandardCopyOption.REPLACE_EXISTING);
        } catch (java.nio.file.AtomicMoveNotSupportedException unsupported) {
            Files.move(temporary.toPath(), file.toPath(), java.nio.file.StandardCopyOption.REPLACE_EXISTING);
        }
    }

    private static Map<String, Object> manifest(Context context, File root, List<File> clips) throws IOException {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("schemaVersion", 1);
        Map<String, Object> apks = new LinkedHashMap<>(), inputs = new LinkedHashMap<>(), photos = new LinkedHashMap<>();
        Map<String, Object> assets = new LinkedHashMap<>(), nativeLibraries = new LinkedHashMap<>();
        List<String> apkPaths = new ArrayList<>();
        apkPaths.add(context.getApplicationInfo().sourceDir);
        if (context.getApplicationInfo().splitSourceDirs != null)
            apkPaths.addAll(Arrays.asList(context.getApplicationInfo().splitSourceDirs));
        int index = 0;
        for (String apkPath : apkPaths) {
            File apk = new File(apkPath);
            apks.put("apk" + index, HashManifest.file(apk));
            try (java.util.zip.ZipFile zip = new java.util.zip.ZipFile(apk)) {
                java.util.Enumeration<? extends java.util.zip.ZipEntry> entries = zip.entries();
                while (entries.hasMoreElements()) {
                    java.util.zip.ZipEntry entry = entries.nextElement();
                    if (entry.getName().startsWith("lib/") && entry.getName().endsWith(".so")) {
                        try (java.io.InputStream input = zip.getInputStream(entry)) {
                            nativeLibraries.put("apk" + index + "/" + entry.getName(), HashManifest.stream(input));
                        }
                    }
                }
            }
            index++;
        }
        for (File clip : clips) {
            inputs.put(clip.getName(), HashManifest.file(clip));
            inputs.put(metaFile(clip).getName(), HashManifest.file(metaFile(clip)));
        }
        List<String> expectedIds = new ArrayList<>();
        for (File photo : enrollmentFiles(enrollDir(root))) {
            photos.put(photo.getName(), HashManifest.file(photo));
            expectedIds.add(photo.getName().replaceFirst("(?i)[.](jpe?g|png)$", ""));
        }
        hashAssets(context.getAssets(), "", assets);
        result.put("apks", apks); result.put("inputs", inputs); result.put("enrollment", photos);
        result.put("expectedEnrolledIds", expectedIds); result.put("modelAssets", assets);
        result.put("nativeLibraries", nativeLibraries);
        result.put("inputsSha256", HashManifest.fingerprint(inputs));
        result.put("enrollmentSha256", HashManifest.fingerprint(photos));
        result.put("modelAssetsSha256", HashManifest.fingerprint(assets));
        result.put("packageName", context.getPackageName());
        return result;
    }

    private static void hashAssets(android.content.res.AssetManager manager, String path, Map<String, Object> hashes) throws IOException {
        String[] children = manager.list(path);
        if (children == null) throw new IOException("Cannot enumerate model assets " + path);
        if (children.length > 0) {
            Arrays.sort(children);
            for (String child : children) hashAssets(manager, path.isEmpty() ? child : path + "/" + child, hashes);
        } else if (path.matches("(?i).+[.](onnx|bin|param|json)$")) {
            try (java.io.InputStream input = manager.open(path)) { hashes.put(path, HashManifest.stream(input)); }
        }
    }

    private static File[] enrollmentFiles(File dir) {
        File[] photos = dir.listFiles((parent, file) -> file.matches("(?i)[A-Za-z0-9._-]+[.](jpe?g|png)"));
        if (photos == null) return new File[0];
        Arrays.sort(photos);
        return photos;
    }

    private static Map<String, Object> enrollAll(FaceBackend backend, File dir, CsvWriter csv, Progress progress)
            throws Exception {
        Map<String, Object> references = new LinkedHashMap<>();
        File[] photos = enrollmentFiles(dir);
        for (File photo : photos) {
            if (progress.cancelled()) break;
            String subject = photo.getName().replaceFirst("(?i)[.](jpe?g|png)$", "");
            Bitmap bitmap = decodeUpright(photo);
            Object feature = null;
            String note = "";
            Timer timer = Timer.start();
            if (bitmap == null) {
                note = "cannot decode";
            } else {
                try {
                    feature = backend.enroll(bitmap);
                    if (feature == null) note = "no usable face";
                } catch (Exception error) {
                    note = "error: " + error.getMessage();
                }
            }
            csv.row(backend.name(), subject, feature != null ? 1 : 0, timer.elapsedMs(), note);
            if (feature != null) {
                references.put(subject, feature);
                backend.enrolled(subject, feature);
            }
        }
        progress.update(backend.name() + "：注册照 " + references.size() + "/" + photos.length + " 张可用");
        return references;
    }

    /** Applies EXIF orientation so every backend enrolls the same upright photo. */
    static Bitmap decodeUpright(File photo) throws IOException {
        BitmapFactory.Options options = new BitmapFactory.Options();
        options.inPreferredConfig = Bitmap.Config.ARGB_8888;
        Bitmap bitmap = BitmapFactory.decodeFile(photo.getPath(), options);
        if (bitmap == null) return null;
        int orientation = new ExifInterface(photo.getPath())
                .getAttributeInt(ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL);
        Matrix matrix = new Matrix();
        switch (orientation) {
            case ExifInterface.ORIENTATION_ROTATE_90: matrix.postRotate(90); break;
            case ExifInterface.ORIENTATION_ROTATE_180: matrix.postRotate(180); break;
            case ExifInterface.ORIENTATION_ROTATE_270: matrix.postRotate(270); break;
            case ExifInterface.ORIENTATION_FLIP_HORIZONTAL: matrix.postScale(-1, 1); break;
            case ExifInterface.ORIENTATION_FLIP_VERTICAL: matrix.postScale(1, -1); break;
            case ExifInterface.ORIENTATION_TRANSPOSE: matrix.postRotate(90); matrix.postScale(-1, 1); break;
            case ExifInterface.ORIENTATION_TRANSVERSE: matrix.postRotate(270); matrix.postScale(-1, 1); break;
            default: return bitmap;
        }
        return Bitmap.createBitmap(bitmap, 0, 0, bitmap.getWidth(), bitmap.getHeight(), matrix, true);
    }

    static List<File> listClips(File dir) {
        List<File> clips = new ArrayList<>();
        File[] files = dir.listFiles((parent, file) -> file.endsWith(".fclip"));
        if (files == null) return clips;
        Arrays.sort(files);
        for (File file : files) if (metaFile(file).isFile()) clips.add(file);
        return clips;
    }

    static File metaFile(File clip) {
        return new File(clip.getParentFile(), clip.getName().replaceFirst("[.]fclip$", ".json"));
    }

    private static Map<String, Object> deviceInfo() {
        Map<String, Object> device = new LinkedHashMap<>();
        device.put("manufacturer", Build.MANUFACTURER);
        device.put("model", Build.MODEL);
        device.put("hardware", Build.HARDWARE);
        device.put("board", Build.BOARD);
        if (Build.VERSION.SDK_INT >= 31) device.put("socModel", Build.SOC_MODEL);
        device.put("androidRelease", Build.VERSION.RELEASE);
        device.put("sdkInt", Build.VERSION.SDK_INT);
        device.put("abis", Arrays.asList(Build.SUPPORTED_ABIS));
        device.put("cpuCount", Runtime.getRuntime().availableProcessors());
        return device;
    }
}
