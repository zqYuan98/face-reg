package com.bonuos.facebench;

import android.Manifest;
import android.content.pm.PackageManager;
import android.graphics.ImageFormat;
import android.os.Build;
import android.os.Bundle;
import android.text.InputType;
import android.util.Size;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.Spinner;
import android.widget.TextView;
import android.widget.Toast;
import androidx.annotation.NonNull;
import androidx.appcompat.app.AppCompatActivity;
import androidx.camera.core.CameraSelector;
import androidx.camera.core.ImageAnalysis;
import androidx.camera.core.ImageProxy;
import androidx.camera.core.Preview;
import androidx.camera.core.resolutionselector.ResolutionSelector;
import androidx.camera.core.resolutionselector.ResolutionStrategy;
import androidx.camera.lifecycle.ProcessCameraProvider;
import androidx.camera.view.PreviewView;
import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;
import com.bonuos.facebench.bench.BenchRunner;
import com.bonuos.facebench.clip.ClipFile;
import com.bonuos.facebench.clip.ClipMeta;
import com.bonuos.facesdk.image.Nv21;
import com.google.common.util.concurrent.ListenableFuture;
import java.io.File;
import java.io.IOException;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Records one labelled presentation as raw upright NV21 frames. Nothing is compressed, so the
 * same pixels can later be replayed through every backend.
 */
public class RecordActivity extends AppCompatActivity {
    /** Store at most 10 frames per second: 2 s at 1280x720 is about 28 MB instead of 80 MB. */
    private static final long MIN_FRAME_INTERVAL_NS = 100_000_000L;
    private static final String[] LABELS = {"真人", "打印照", "屏幕翻拍", "视频回放", "其他攻击"};
    private static final String[] ATTACK_TYPES = {null, "print", "screen", "replay", "other"};

    private PreviewView preview;
    private EditText subject, medium, lighting, seconds;
    private Spinner label;
    private TextView status;
    private Button recordButton;
    private ExecutorService analysis;
    private boolean frontCamera = true;

    // Recording state, touched only on the analysis thread after start.
    private volatile Recording pending;
    private Recording active;

    private static final class Recording {
        ClipMeta meta;
        File clipFile, metaFile;
        long durationNs;
        long firstTimestamp = -1;
        long lastStoredTimestamp = Long.MIN_VALUE;
        ClipFile.Writer writer;
        byte[] buffer;
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        setTitle("录制样本");
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        preview = new PreviewView(this);
        root.addView(preview, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));

        LinearLayout form = new LinearLayout(this);
        form.setOrientation(LinearLayout.VERTICAL);
        form.setPadding(24, 12, 24, 12);
        subject = field(form, "人员编号（与注册照文件名一致）", InputType.TYPE_CLASS_TEXT);
        label = new Spinner(this);
        label.setAdapter(new ArrayAdapter<>(this, android.R.layout.simple_spinner_dropdown_item, LABELS));
        form.addView(label);
        medium = field(form, "攻击介质，如 iPhone13 屏幕 / A4 彩打（真人留空）", InputType.TYPE_CLASS_TEXT);
        lighting = field(form, "光照，如 室内正常 / 逆光 / 暗光", InputType.TYPE_CLASS_TEXT);
        seconds = field(form, "录制秒数", InputType.TYPE_CLASS_NUMBER);
        seconds.setText("2");

        LinearLayout buttons = new LinearLayout(this);
        Button switchCamera = new Button(this);
        switchCamera.setText("切换摄像头");
        switchCamera.setOnClickListener(v -> { frontCamera = !frontCamera; startCamera(); });
        recordButton = new Button(this);
        recordButton.setText("开始录制");
        recordButton.setOnClickListener(v -> startRecording());
        buttons.addView(switchCamera);
        buttons.addView(recordButton);
        form.addView(buttons);
        status = new TextView(this);
        form.addView(status);
        root.addView(form);
        setContentView(root);

        analysis = Executors.newSingleThreadExecutor();
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) {
            startCamera();
        } else {
            ActivityCompat.requestPermissions(this, new String[]{Manifest.permission.CAMERA}, 1);
        }
    }

    private EditText field(LinearLayout parent, String hint, int type) {
        EditText edit = new EditText(this);
        edit.setHint(hint);
        edit.setInputType(type);
        edit.setSingleLine(true);
        parent.addView(edit);
        return edit;
    }

    @Override
    public void onRequestPermissionsResult(int code, @NonNull String[] permissions, @NonNull int[] results) {
        super.onRequestPermissionsResult(code, permissions, results);
        if (results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED) startCamera();
        else { Toast.makeText(this, "需要相机权限", Toast.LENGTH_LONG).show(); finish(); }
    }

    private void startCamera() {
        ListenableFuture<ProcessCameraProvider> future = ProcessCameraProvider.getInstance(this);
        future.addListener(() -> {
            try {
                ProcessCameraProvider provider = future.get();
                Preview previewUseCase = new Preview.Builder().build();
                previewUseCase.setSurfaceProvider(preview.getSurfaceProvider());
                // Same analysis resolution as ArcSoft's published benchmark (1280x720).
                ResolutionSelector selector = new ResolutionSelector.Builder()
                        .setResolutionStrategy(new ResolutionStrategy(new Size(1280, 720),
                                ResolutionStrategy.FALLBACK_RULE_CLOSEST_HIGHER_THEN_LOWER))
                        .build();
                ImageAnalysis imageAnalysis = new ImageAnalysis.Builder()
                        .setResolutionSelector(selector)
                        .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                        .build();
                imageAnalysis.setAnalyzer(analysis, this::onFrame);
                CameraSelector camera = new CameraSelector.Builder()
                        .requireLensFacing(frontCamera ? CameraSelector.LENS_FACING_FRONT : CameraSelector.LENS_FACING_BACK)
                        .build();
                provider.unbindAll();
                provider.bindToLifecycle(this, camera, previewUseCase, imageAnalysis);
            } catch (Exception error) {
                status.setText("相机启动失败：" + error.getMessage());
            }
        }, ContextCompat.getMainExecutor(this));
    }

    private void startRecording() {
        if (pending != null || active != null) return;
        int labelIndex = label.getSelectedItemPosition();
        Recording recording = new Recording();
        ClipMeta meta = new ClipMeta();
        meta.subjectId = subject.getText().toString().trim();
        meta.label = labelIndex == 0 ? ClipMeta.LIVE : ClipMeta.ATTACK;
        meta.attackType = ATTACK_TYPES[labelIndex];
        meta.attackMedium = emptyToNull(medium.getText().toString().trim());
        meta.lighting = emptyToNull(lighting.getText().toString().trim());
        meta.deviceManufacturer = Build.MANUFACTURER;
        meta.deviceModel = Build.MODEL;
        meta.frontCamera = frontCamera;
        meta.createdAtEpochMs = System.currentTimeMillis();
        String stamp = new SimpleDateFormat("yyyyMMdd-HHmmss", Locale.ROOT).format(new Date());
        meta.clipId = meta.subjectId + "_" + (meta.attackType == null ? "live" : meta.attackType) + "_" + stamp;
        int durationSeconds;
        try {
            durationSeconds = Integer.parseInt(seconds.getText().toString().trim());
            if (durationSeconds < 1 || durationSeconds > 15) throw new NumberFormatException();
            meta.validate();
        } catch (NumberFormatException error) {
            status.setText("录制秒数需为 1–15");
            return;
        } catch (IllegalArgumentException error) {
            status.setText("信息有误：" + error.getMessage());
            return;
        }
        File dir = BenchRunner.clipsDir(storageRoot(this));
        if (!dir.isDirectory() && !dir.mkdirs()) {
            status.setText("无法创建目录 " + dir);
            return;
        }
        recording.meta = meta;
        recording.clipFile = new File(dir, meta.clipId + ".fclip");
        recording.metaFile = new File(dir, meta.clipId + ".json");
        recording.durationNs = durationSeconds * 1_000_000_000L;
        recordButton.setEnabled(false);
        status.setText("录制中……");
        pending = recording;
    }

    private void onFrame(@NonNull ImageProxy image) {
        try {
            if (active == null && pending != null) { active = pending; pending = null; }
            Recording recording = active;
            if (recording == null || image.getFormat() != ImageFormat.YUV_420_888) return;
            long frameTimestamp = image.getImageInfo().getTimestamp();
            if (recording.writer != null && frameTimestamp - recording.lastStoredTimestamp < MIN_FRAME_INTERVAL_NS) return;
            int width = image.getWidth(), height = image.getHeight();
            int rotation = image.getImageInfo().getRotationDegrees();
            long timestamp = image.getImageInfo().getTimestamp();
            if (recording.buffer == null) recording.buffer = new byte[Nv21.size(width, height)];
            ImageProxy.PlaneProxy[] planes = image.getPlanes();
            Nv21.fromYuv420(width, height, planes[0].getBuffer(), planes[0].getRowStride(),
                    planes[1].getBuffer(), planes[2].getBuffer(), planes[1].getRowStride(),
                    planes[1].getPixelStride(), recording.buffer);
            byte[] upright = Nv21.rotate(recording.buffer, width, height, rotation);
            if (recording.writer == null) {
                recording.meta.width = rotation % 180 == 0 ? width : height;
                recording.meta.height = rotation % 180 == 0 ? height : width;
                recording.meta.sensorRotationDegrees = rotation;
                recording.writer = new ClipFile.Writer(recording.clipFile, recording.meta.width, recording.meta.height);
                recording.firstTimestamp = timestamp;
            }
            recording.writer.write(timestamp, upright);
            recording.lastStoredTimestamp = timestamp;
            if (timestamp - recording.firstTimestamp >= recording.durationNs) finish(recording, timestamp);
        } catch (Exception error) {
            abort(error);
        } finally {
            image.close();
        }
    }

    private void finish(Recording recording, long lastTimestamp) throws IOException {
        recording.writer.close();
        recording.meta.frameCount = recording.writer.frames();
        recording.meta.durationMs = (lastTimestamp - recording.firstTimestamp) / 1_000_000L;
        recording.meta.write(recording.metaFile);
        active = null;
        String message = "已保存 " + recording.meta.clipId + "：" + recording.meta.frameCount + " 帧，"
                + recording.meta.width + "x" + recording.meta.height;
        runOnUiThread(() -> { status.setText(message); recordButton.setEnabled(true); });
    }

    private void abort(Exception error) {
        Recording recording = active;
        active = null;
        if (recording != null) {
            try { if (recording.writer != null) recording.writer.close(); } catch (IOException ignored) { }
            //noinspection ResultOfMethodCallIgnored
            recording.clipFile.delete(); // never leave an unlabelled or truncated clip behind
        }
        runOnUiThread(() -> { status.setText("录制失败：" + error.getMessage()); recordButton.setEnabled(true); });
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        analysis.execute(() -> { if (active != null) abort(new IOException("录制被中断")); });
        analysis.shutdown();
    }

    static File storageRoot(android.content.Context context) {
        File external = context.getExternalFilesDir(null);
        return external != null ? external : context.getFilesDir();
    }

    private static String emptyToNull(String text) { return text.isEmpty() ? null : text; }
}
