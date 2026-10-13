package com.bonuos.facebench.bench;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Matrix;
import android.media.ExifInterface;
import android.os.Build;
import com.bonuos.facebench.backend.BackendRegistry;
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
        run.put("runId", runId);
        run.put("startedAtEpochMs", System.currentTimeMillis());
        run.put("device", deviceInfo());
        run.put("config", config);
        run.put("clipCount", clips.size());
        Map<String, String> descriptions = new LinkedHashMap<>();
        Map<String, String> failures = new LinkedHashMap<>();

        try (CsvWriter frames = new CsvWriter(new File(out, "frames.csv"), "backend", "clip_id", "subject_id", "label",
                "attack_type", "frame_index", "timestamp_ns", "face_count", "box_left", "box_top", "box_right", "box_bottom",
                "preprocess_ms", "detect_ms", "liveness_ms", "feature_ms", "live_score", "live_status",
                "feature_attempted", "feature_ok", "warmup", "note");
             CsvWriter sweep = new CsvWriter(new File(out, "liveness_sweep.csv"), "backend", "clip_id", "frame_index",
                     "threshold", "status");
             CsvWriter scores = new CsvWriter(new File(out, "scores.csv"), "backend", "clip_id", "frame_index",
                     "subject_id", "label", "enrolled_id", "score");
             CsvWriter enroll = new CsvWriter(new File(out, "enroll.csv"), "backend", "enrolled_id", "ok", "ms", "note")) {
            for (String name : config.backends) {
                if (progress.cancelled()) break;
                progress.update("加载后端 " + name);
                try (FaceBackend backend = BackendRegistry.create(context, name, config)) {
                    descriptions.put(name, backend.describe());
                    Map<String, Object> references = enrollAll(backend, enrollDir(root), enroll, progress);
                    int clipNumber = 0;
                    for (File clipFile : clips) {
                        if (progress.cancelled()) break;
                        clipNumber++;
                        ClipMeta meta = ClipMeta.read(metaFile(clipFile));
                        progress.update(name + "：片段 " + clipNumber + "/" + clips.size() + " " + meta.clipId);
                        replayClip(backend, clipFile, meta, config, references, frames, sweep, scores);
                        frames.flush();
                    }
                } catch (Exception error) {
                    failures.put(name, error.getClass().getSimpleName() + ": " + error.getMessage());
                    progress.update(name + " 失败：" + error.getMessage());
                }
            }
        }
        run.put("backends", descriptions);
        run.put("failures", failures);
        run.put("cancelled", progress.cancelled());
        run.put("finishedAtEpochMs", System.currentTimeMillis());
        Files.write(new File(out, "run.json").toPath(),
                new GsonBuilder().setPrettyPrinting().serializeSpecialFloatingPointValues().create().toJson(run)
                        .getBytes(StandardCharsets.UTF_8));
        return out;
    }

    private static void replayClip(FaceBackend backend, File clipFile, ClipMeta meta, BenchConfig config,
                                   Map<String, Object> references, CsvWriter frames, CsvWriter sweep,
                                   CsvWriter scores) throws Exception {
        backend.beginClip(meta.sensorRotationDegrees);
        try (ClipFile.Reader reader = new ClipFile.Reader(clipFile)) {
            ClipFile.Frame frame;
            while ((frame = reader.next()) != null) {
                boolean runFeature = frame.index % config.featureEveryNth == 0;
                FrameOutcome outcome;
                try {
                    outcome = backend.process(frame, config.livenessEveryFrame, runFeature);
                } catch (Exception error) {
                    outcome = new FrameOutcome();
                    outcome.livenessStatus = FrameOutcome.LIVE_ERROR;
                    outcome.note = "error: " + error.getMessage();
                }
                int[] box = outcome.box;
                frames.row(backend.name(), meta.clipId, meta.subjectId, meta.label, meta.attackType, frame.index,
                        frame.timestampNs, outcome.faceCount,
                        box == null ? null : box[0], box == null ? null : box[1],
                        box == null ? null : box[2], box == null ? null : box[3],
                        outcome.preprocessMs, outcome.detectMs, outcome.livenessMs, outcome.featureMs,
                        outcome.livenessScore, outcome.livenessStatus,
                        outcome.featureAttempted ? 1 : 0, outcome.feature != null ? 1 : 0,
                        frame.index < config.warmupFramesPerClip ? 1 : 0, outcome.note);
                for (Map.Entry<Float, Integer> entry : outcome.livenessStatusByThreshold.entrySet())
                    sweep.row(backend.name(), meta.clipId, frame.index, entry.getKey(), entry.getValue());
                if (outcome.feature != null) {
                    for (Map.Entry<String, Object> reference : references.entrySet()) {
                        scores.row(backend.name(), meta.clipId, frame.index, meta.subjectId, meta.label,
                                reference.getKey(), backend.compare(outcome.feature, reference.getValue()));
                    }
                }
            }
        }
    }

    private static Map<String, Object> enrollAll(FaceBackend backend, File dir, CsvWriter csv, Progress progress)
            throws Exception {
        Map<String, Object> references = new LinkedHashMap<>();
        File[] photos = dir.listFiles((parent, file) -> file.matches("(?i)[A-Za-z0-9._-]+\\.(jpe?g|png)"));
        if (photos == null) return references;
        Arrays.sort(photos);
        for (File photo : photos) {
            if (progress.cancelled()) break;
            String subject = photo.getName().replaceFirst("(?i)\\.(jpe?g|png)$", "");
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
            if (feature != null) references.put(subject, feature);
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
        return new File(clip.getParentFile(), clip.getName().replaceFirst("\\.fclip$", ".json"));
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
