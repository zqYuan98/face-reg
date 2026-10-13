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
import com.bonuos.facesdk.engine.CameraFrame;
import com.bonuos.facesdk.engine.FaceRect;
import com.bonuos.facesdk.engine.FaceSession;
import com.bonuos.facesdk.engine.MvEngines;
import com.bonuos.facesdk.engine.NativeEngine;
import com.bonuos.facesdk.engine.NativeFeatureEngine;
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
    private MvEngines mvEngines;
    private volatile FaceSession<FaceRepository.FaceUser> session;
    private ExecutorService cameraExecutor;
    private ImageAnalysis imageAnalysis;
    private byte[] nv21Buffer;
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
        if (session != null) cameraExecutor.execute(session::reset);
        startCamera();
    }

    private void initFaceSDK() {
        try {
            // Our own engine (int8 recognizer, averaged landmarks) instead of the prebuilt library.
            faceRecognizer = new FaceRecognizer(this, FaceRecognizer.Backend.NATIVE_INT8);
            mvEngines = new MvEngines(this);
            SessionConfig config = new SessionConfig(); // thresholds uncalibrated; see SessionConfig
            NativeFeatureEngine features = new NativeFeatureEngine(faceRecognizer.nativeEngine(),
                    NativeEngine.LandmarkMode.TTA, 30f, 25f);
            session = new FaceSession<>(config, mvEngines, mvEngines, features, faceRecognizer, this);
            Log.i(TAG, "Loaded " + faceRecognizer.gallery().size() + " users into the gallery.");
        } catch (Exception e) {
            Log.e(TAG, "Failed to initialize FaceSDK", e);
            Toast.makeText(this, "SDK初始化失败", Toast.LENGTH_LONG).show();
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
        boolean imageClosed = false;
        FaceSession<FaceRepository.FaceUser> session = this.session;
        try {
            if (session == null || isFinishing() || isDestroyed() || image.getFormat() != ImageFormat.YUV_420_888) return;
            int width = image.getWidth(), height = image.getHeight();
            if (nv21Buffer == null || nv21Buffer.length != Nv21.size(width, height)) {
                nv21Buffer = new byte[Nv21.size(width, height)];
            }
            // Pack camera planes directly (no JPEG); rotation is applied in native code, once.
            ImageProxy.PlaneProxy[] planes = image.getPlanes();
            Nv21.fromYuv420(width, height, planes[0].getBuffer(), planes[0].getRowStride(),
                    planes[1].getBuffer(), planes[2].getBuffer(), planes[1].getRowStride(),
                    planes[1].getPixelStride(), nv21Buffer);
            CameraFrame frame = new CameraFrame(nv21Buffer, width, height,
                    image.getImageInfo().getRotationDegrees(), image.getImageInfo().getTimestamp() / 1_000_000L);
            image.close(); // release the camera buffer before running the models
            imageClosed = true;
            session.onFrame(frame);
        } catch (RuntimeException e) {
            Log.e(TAG, "Frame analysis failed", e);
        } finally {
            if (!imageClosed) image.close();
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
        final MvEngines closingEngines = mvEngines;
        final FaceRecognizer closingRecognizer = faceRecognizer;
        session = null;
        cameraExecutor.execute(() -> {
            if (closingSession != null) closingSession.close();
            if (closingEngines != null) closingEngines.close();
            // Waits for an in-flight feature extraction via the dataset lock.
            if (closingRecognizer != null) closingRecognizer.release();
        });
        cameraExecutor.shutdown();
    }
}
