package com.bonuos.faceapp;

import android.Manifest;
import android.content.pm.PackageManager;
import android.graphics.ImageFormat;
import android.os.Bundle;
import android.util.Log;
import android.view.View;
import android.widget.TextView;
import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.appcompat.app.AppCompatActivity;
import androidx.camera.core.CameraSelector;
import androidx.camera.core.ImageAnalysis;
import androidx.camera.core.ImageProxy;
import androidx.camera.core.Preview;
import androidx.camera.lifecycle.ProcessCameraProvider;
import androidx.camera.view.PreviewView;
import androidx.constraintlayout.widget.Group;
import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;

import com.bonuos.facesdk.FaceRecognizer;
import com.bonuos.facesdk.FaceRepository;
import com.bonuos.facesdk.engine.AnalysisProfile;
import com.bonuos.facesdk.engine.CameraFrame;
import com.bonuos.facesdk.engine.FaceRect;
import com.bonuos.facesdk.engine.FaceSession;
import com.bonuos.facesdk.engine.MvEngines;
import com.bonuos.facesdk.engine.NativeAnalyzer;
import com.bonuos.facesdk.engine.NativeEngine;
import com.bonuos.facesdk.engine.NativeFeatureEngine;
import com.bonuos.facesdk.engine.ScrfdEngines;
import com.bonuos.facesdk.engine.SessionConfig;
import com.bonuos.facesdk.image.Nv21;
import com.bonuos.faceapp.widget.RectView;
import com.google.common.util.concurrent.ListenableFuture;

import java.util.List;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Camera recognition screen. All per-frame work goes through {@link FaceSession}: each person is
 * tracked, gated, liveness-checked and recognised once, and the UI only changes when a state does.
 */
public class FaceRecognitionActivity extends AppCompatActivity implements FaceSession.Listener<FaceRepository.FaceUser> {

    private static final String TAG = "FaceActivity";
    private static final int PERMISSION_REQUEST_CODE = 10;

    private PreviewView previewView;
    private RectView rectView;
    private TextView tvStatus;
    private View statusIndicator;
    private Group groupUserInfo;
    private TextView tvUserName;
    private TextView tvUserId;
    private TextView tvSimilarityValue;

    private com.bonuos.facesdk.SyncManager syncManager;
    private FaceRecognizer faceRecognizer;
    /** Cascade second stage (IResNet-50 int8) when face.engine is insightface_cascade, else null. */
    private FaceRecognizer verifyRecognizer;
    /** Per-frame detector + liveness: ScrfdEngines with InsightFace, MvEngines with the v1 engines. */
    private AutoCloseable frameEngines;
    private volatile FaceSession<FaceRepository.FaceUser> session;
    private ExecutorService cameraExecutor;
    private ImageAnalysis imageAnalysis;
    private byte[] nv21Buffer;
    private final CameraFrameAdmission.Counters frameCounters = new CameraFrameAdmission.Counters();
    private int cameraLensFacing = CameraSelector.LENS_FACING_FRONT;
    /** Last status shown, so the text only changes on state changes (UI thread only). */
    private String shownStatus;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_face_recognition);

        cameraExecutor = Executors.newSingleThreadExecutor();
        initViews();
        initFaceSDK();

        syncManager = new com.bonuos.facesdk.SyncManager(this);

        if (allPermissionsGranted()) {
            startCamera();
        } else {
            ActivityCompat.requestPermissions(this,
                    new String[] { Manifest.permission.CAMERA }, PERMISSION_REQUEST_CODE);
        }
    }

    private void initViews() {
        previewView = findViewById(R.id.previewView);
        rectView = findViewById(R.id.rectView);
        tvStatus = findViewById(R.id.tv_status);
        statusIndicator = findViewById(R.id.status_indicator);
        groupUserInfo = findViewById(R.id.group_user_info);
        tvUserName = findViewById(R.id.tv_user_name);
        tvUserId = findViewById(R.id.tv_user_id);
        tvSimilarityValue = findViewById(R.id.tv_similarity_value);

        findViewById(R.id.btn_back).setOnClickListener(v -> finish());
        findViewById(R.id.btn_switch_camera).setOnClickListener(v -> toggleCamera());
    }

    private void toggleCamera() {
        cameraLensFacing = cameraLensFacing == CameraSelector.LENS_FACING_BACK
                ? CameraSelector.LENS_FACING_FRONT : CameraSelector.LENS_FACING_BACK;
        // Tracks from the other camera are meaningless; reset on the analysis thread that owns the session.
        if (session != null) cameraExecutor.execute(() -> {
            session.reset();
            if (frameEngines instanceof ScrfdEngines) ((ScrfdEngines) frameEngines).reset();
        });
        startCamera();
    }

    private void initFaceSDK() {
        try {
            boolean cascade = cascadeFromBuild();
            FaceRecognizer.Backend backend = cascade ? FaceRecognizer.Backend.INSIGHTFACE_MBF : engineFromBuild();
            faceRecognizer = new FaceRecognizer(this, backend);
            if (cascade) verifyRecognizer = new FaceRecognizer(this, FaceRecognizer.Backend.INSIGHTFACE_R50_INT8);
            SessionConfig config = new SessionConfig();
            // Candidate switches are opt-in until the paired device comparison passes.
            AnalysisProfile profile = AnalysisProfile.forVariant(BuildConfig.SHARE_FEATURE_CAPTURE ? "shared_capture" : "baseline");
            profile.applyTo(config);
            FaceSession.Detector detector;
            FaceSession.LivenessScorer liveness;
            if (backend.insightFace()) {
                ScrfdEngines scrfd = new ScrfdEngines(getAssets(), profile);
                frameEngines = scrfd;
                detector = scrfd;
                liveness = scrfd;
                // SCRFD boxes are narrower than minivision squares (width ~0.84x); 64 px keeps the same reach.
                config.minFaceSize = 64;
            } else {
                MvEngines mv = new MvEngines(this);
                frameEngines = mv;
                detector = mv;
                liveness = mv;
            }
            if (backend.insightFace()) {
                // InsightFace scores live on another scale. LFW all-pairs, 2026-10-13: MobileFaceNet
                // FAR 1e-5 at 0.332, 1e-4 at 0.283; IResNet-50 fp32 and int8 0.314 / 0.257. Calibrate on site data.
                boolean r50 = backend != FaceRecognizer.Backend.INSIGHTFACE_MBF;
                config.thresholdHigh = r50 ? 0.31f : 0.33f;
                config.thresholdLow = r50 ? 0.26f : 0.28f;
            } // v1 thresholds: SessionConfig defaults, uncalibrated
            NativeFeatureEngine features = new NativeFeatureEngine(faceRecognizer.nativeEngine(),
                    NativeEngine.LandmarkMode.TTA, 30f, 25f);
            Log.i(TAG, "Face engine " + backend + ", threshold " + config.thresholdHigh);
            session = new FaceSession<>(config, detector, liveness, features, faceRecognizer, this);
            if (verifyRecognizer != null) {
                // MobileFaceNet decides clear cases; IResNet-50 int8 checks the borderline ones.
                // Thresholds: tools/engine-eval/cascade_lfw.py (LFW all pairs, FAR 1e-5).
                session.withVerifier(new FaceSession.Verifier<>(
                        new NativeFeatureEngine(verifyRecognizer.nativeEngine(), NativeEngine.LandmarkMode.TTA, 30f, 25f),
                        verifyRecognizer, 0.487f, 0.098f, 0.31f));
            }
            Log.i(TAG, "Loaded " + faceRecognizer.gallery().size() + " users into the gallery.");
        } catch (Exception e) {
            Log.e(TAG, "Failed to initialize FaceSDK", e);
            Toast.makeText(this, "SDK初始化失败", Toast.LENGTH_LONG).show();
        }
    }

    /**
     * local.properties face.engine: insightface_r50_int8 (default), insightface_mbf, native_int8,
     * native. The app APK does not ship the prebuilt library (see app/build.gradle.kts).
     */
    /** Default engine: MobileFaceNet -> IResNet-50 int8 cascade (speed of the first, accuracy of the second). */
    private static boolean cascadeFromBuild() {
        return BuildConfig.FACE_ENGINE.isEmpty() || "insightface_cascade".equals(BuildConfig.FACE_ENGINE);
    }

    private static FaceRecognizer.Backend engineFromBuild() {
        switch (BuildConfig.FACE_ENGINE) {
            case "native": return FaceRecognizer.Backend.NATIVE;
            case "insightface_mbf": return FaceRecognizer.Backend.INSIGHTFACE_MBF;
            case "insightface_r50": return FaceRecognizer.Backend.INSIGHTFACE_R50;
            case "insightface_r50_int8": return FaceRecognizer.Backend.INSIGHTFACE_R50_INT8;
            case "native_int8": return FaceRecognizer.Backend.NATIVE_INT8;
            default: return FaceRecognizer.Backend.INSIGHTFACE_R50_INT8; // best measured: LFW TAR@1e-5 99.57%
        }
    }

    private void startCamera() {
        ListenableFuture<ProcessCameraProvider> cameraProviderFuture = ProcessCameraProvider.getInstance(this);

        cameraProviderFuture.addListener(() -> {
            try {
                ProcessCameraProvider cameraProvider = cameraProviderFuture.get();

                Preview preview = new Preview.Builder().build();
                preview.setSurfaceProvider(previewView.getSurfaceProvider());

                imageAnalysis = new ImageAnalysis.Builder()
                        .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                        .build();
                imageAnalysis.setAnalyzer(cameraExecutor, this::analyzeImage);

                CameraSelector cameraSelector = new CameraSelector.Builder()
                        .requireLensFacing(cameraLensFacing)
                        .build();

                cameraProvider.unbindAll();
                cameraProvider.bindToLifecycle(this, cameraSelector, preview, imageAnalysis);
            } catch (ExecutionException | InterruptedException e) {
                Log.e(TAG, "Camera initialization failed", e);
            }
        }, ContextCompat.getMainExecutor(this));
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (syncManager != null) {
            syncManager.startAutoSync(30, new com.bonuos.facesdk.SyncManager.SyncCallback() {
                @Override
                public void onSuccess(int count) {
                    // SyncManager publishes the recognition cache even when the final dataset is empty.
                    runOnUiThread(() -> Toast.makeText(FaceRecognitionActivity.this,
                            "同步完成，本地人员 " + count + " 人", Toast.LENGTH_SHORT).show());
                }

                @Override
                public void onError(String msg) {
                    Log.e(TAG, "Sync error in recognition screen: " + msg);
                }
            });
        }
    }

    @Override
    protected void onPause() {
        super.onPause();
        if (syncManager != null) {
            syncManager.stopAutoSync();
        }
    }

    private void analyzeImage(@NonNull ImageProxy image) {
        final FaceSession<FaceRepository.FaceUser> current = this.session;
        try {
            CameraFrameAdmission.analyze(image, new CameraFrameAdmission.Processor() {
                @Override public boolean shouldProcess(long timestampMs) {
                    return current != null && !isFinishing() && !isDestroyed()
                            && image.getFormat() == ImageFormat.YUV_420_888
                            && (!BuildConfig.EARLY_FRAME_ADMISSION || current.shouldProcessFrame(timestampMs));
                }

                @Override public CameraFrame capture(ImageProxy source, long timestampMs) {
                    int width = source.getWidth(), height = source.getHeight();
                    if (nv21Buffer == null || nv21Buffer.length != Nv21.size(width, height)) {
                        nv21Buffer = new byte[Nv21.size(width, height)];
                    }
                    ImageProxy.PlaneProxy[] planes = source.getPlanes();
                    if (!NativeAnalyzer.packNv21(width, height, planes[0].getBuffer(), planes[0].getRowStride(),
                            planes[1].getBuffer(), planes[2].getBuffer(), planes[1].getRowStride(),
                            planes[1].getPixelStride(), nv21Buffer)) {
                        Nv21.fromYuv420(width, height, planes[0].getBuffer(), planes[0].getRowStride(),
                                planes[1].getBuffer(), planes[2].getBuffer(), planes[1].getRowStride(),
                                planes[1].getPixelStride(), nv21Buffer);
                    }
                    return new CameraFrame(nv21Buffer, width, height,
                            source.getImageInfo().getRotationDegrees(), timestampMs);
                }

                @Override public void process(CameraFrame frame) { current.onFrame(frame); }
            }, frameCounters);
        } catch (RuntimeException e) {
            Log.e(TAG, "Frame analysis failed", e);
        }
    }

    // ---- FaceSession.Listener: called on the analysis thread ----

    @Override
    public void onFaces(List<FaceSession.FaceState<FaceRepository.FaceUser>> faces, CameraFrame frame) {
        FaceSession.FaceState<FaceRepository.FaceUser> main = null;
        for (FaceSession.FaceState<FaceRepository.FaceUser> face : faces) {
            if (face.reason == FaceSession.Reason.LOST) continue;
            if (main == null || face.rect.area() > main.rect.area()) main = face;
        }
        final FaceSession.FaceState<FaceRepository.FaceUser> shown = main;
        final int frameWidth = frame.uprightWidth(), frameHeight = frame.uprightHeight();
        runOnUiThread(() -> {
            if (isFinishing() || isDestroyed()) return;
            if (shown == null) {
                clearFaceBox();
                showStatus("请正对摄像头", android.R.color.darker_gray, false);
                return;
            }
            drawFaceBox(shown, frameWidth, frameHeight);
            if (shown.recognition == FaceSession.Recognition.SUCCEED) showSuccess(shown.user, shown.score);
            else showStatus(prompt(shown), colorOf(shown), false);
        });
    }

    @Override
    public void onRecognized(FaceSession.RecognitionEvent<FaceRepository.FaceUser> event) {
        // The place to record an access event or open a door; the UI follows onFaces.
        Log.i(TAG, "Recognised track " + event.trackId + " score " + event.score + " second " + event.secondScore
                + " fused " + event.fusedFrames + " liveness " + event.livenessScore);
    }

    @Override
    public void onRecognitionFailed(long trackId, float bestScore, FaceSession.Reason reason) {
        Log.i(TAG, "Track " + trackId + " not recognised: " + reason + " best " + bestScore);
    }

    @Override
    public void onMatchScored(long trackId, float score, float secondScore, int fusedFrames,
                              FaceSession.Liveness liveness, FaceSession.Reason outcome) {
        Log.i(TAG, String.format(java.util.Locale.ROOT, "Match track %d score %.4f fused %d liveness %s -> %s",
                trackId, score, fusedFrames, liveness, outcome == FaceSession.Reason.NOT_ALIVE ? "WAITING_LIVENESS" : outcome));
    }

    private static String prompt(FaceSession.FaceState<?> face) {
        if (face.liveness == FaceSession.Liveness.NOT_ALIVE) return "非活体";
        switch (face.reason) {
            case FACE_TOO_SMALL: return "请靠近一些";
            case BEYOND_BOUNDARY: return "请将脸移到画面中央";
            case NOT_STABLE: case BLURRY: return "请保持不动";
            case MULTIPLE_FACES: return "请保持画面中只有一人";
            case TOO_DARK: return "光线太暗";
            case TOO_BRIGHT: return "光线太强";
            case UNKNOWN_PERSON: return "未知人员";
            case GALLERY_EMPTY: return "本地人员库为空";
            default: return "正在识别…";
        }
    }

    private static int colorOf(FaceSession.FaceState<?> face) {
        if (face.liveness == FaceSession.Liveness.NOT_ALIVE) return android.R.color.holo_red_dark;
        if (face.reason == FaceSession.Reason.UNKNOWN_PERSON) return android.R.color.holo_orange_dark;
        return android.R.color.darker_gray;
    }

    /** Box coordinates are in the upright frame; map them onto the FILL_CENTER preview. */
    private void drawFaceBox(FaceSession.FaceState<?> face, int frameWidth, int frameHeight) {
        FaceRect box = face.rect;
        float viewWidth = previewView.getWidth(), viewHeight = previewView.getHeight();
        float scale = Math.max(viewWidth / frameWidth, viewHeight / frameHeight);
        float dx = (viewWidth - frameWidth * scale) / 2f, dy = (viewHeight - frameHeight * scale) / 2f;
        float left = box.left * scale + dx, right = box.right * scale + dx;
        if (cameraLensFacing == CameraSelector.LENS_FACING_FRONT) { // preview is mirrored
            float mirroredLeft = viewWidth - right;
            right = viewWidth - left;
            left = mirroredLeft;
        }
        rectView.setX1((int) left);
        rectView.setY1((int) (box.top * scale + dy));
        rectView.setX2((int) right);
        rectView.setY2((int) (box.bottom * scale + dy));
        rectView.setConfidence(Float.isNaN(face.livenessScore) ? 0f : face.livenessScore);
        boolean good = face.recognition == FaceSession.Recognition.SUCCEED || face.liveness == FaceSession.Liveness.ALIVE;
        rectView.setColor(good ? getColor(R.color.tech_success) : getColor(android.R.color.holo_red_dark));
        rectView.postInvalidate();
    }

    private void clearFaceBox() {
        rectView.setX1(0);
        rectView.setY1(0);
        rectView.setX2(0);
        rectView.setY2(0);
        rectView.postInvalidate();
    }

    private void showSuccess(FaceRepository.FaceUser user, float score) {
        String key = "ok:" + user.id;
        if (key.equals(shownStatus)) return;
        shownStatus = key;
        tvStatus.setText(R.string.status_success);
        tvStatus.setTextColor(getColor(R.color.tech_success));
        statusIndicator.setBackgroundTintList(ContextCompat.getColorStateList(this, R.color.tech_success));
        groupUserInfo.setVisibility(View.VISIBLE);
        tvUserName.setText(user.name);
        tvUserId.setText("ID: " + user.userNo);
        tvSimilarityValue.setText((int) (score * 100) + "%");
    }

    private void showStatus(String text, int colorRes, boolean showUser) {
        if (text.equals(shownStatus)) return;
        shownStatus = text;
        tvStatus.setText(text);
        tvStatus.setTextColor(getColor(colorRes));
        statusIndicator.setBackgroundTintList(ContextCompat.getColorStateList(this, colorRes));
        groupUserInfo.setVisibility(showUser ? View.VISIBLE : View.GONE);
    }

    private boolean allPermissionsGranted() {
        return ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED;
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, @NonNull String[] permissions,
            @NonNull int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == PERMISSION_REQUEST_CODE) {
            if (allPermissionsGranted()) {
                startCamera();
            } else {
                Toast.makeText(this, "需要相机权限才能运行", Toast.LENGTH_SHORT).show();
                finish();
            }
        }
    }

    @Override
    protected void onDestroy() {
        if (syncManager != null) syncManager.close();
        if (imageAnalysis != null) imageAnalysis.clearAnalyzer();
        super.onDestroy();
        // Tear down on the analysis thread, after any frame still in flight; the native engines
        // must not be freed under a running onFrame.
        final FaceSession<FaceRepository.FaceUser> closingSession = session;
        final AutoCloseable closingEngines = frameEngines;
        final FaceRecognizer closingRecognizer = faceRecognizer, closingVerifier = verifyRecognizer;
        session = null;
        cameraExecutor.execute(() -> {
            if (BuildConfig.DEBUG) Log.d(TAG, "Camera frames received=" + frameCounters.received
                    + " packed=" + frameCounters.packed + " skipped_before_pack=" + frameCounters.skipped
                    + " packed_bytes=" + frameCounters.captureBytes + " pack_ms=" + frameCounters.captureNanos / 1e6
                    + " errors=" + frameCounters.errors);
            if (closingSession != null) {
                closingSession.close(); // revoke callbacks before waiting for uninterruptible native work
                if (!closingSession.finish(2000)) {
                    Log.e(TAG, "Session did not stop within deadline; native handles retained, not released under inference");
                    return;
                }
            }
            if (closingEngines != null) {
                try {
                    closingEngines.close();
                } catch (Exception e) {
                    Log.w(TAG, "Engine close failed", e);
                }
            }
            // Waits for an in-flight feature extraction via the dataset lock.
            if (closingRecognizer != null) closingRecognizer.release();
            if (closingVerifier != null) closingVerifier.release();
        });
        cameraExecutor.shutdown();
    }
}
