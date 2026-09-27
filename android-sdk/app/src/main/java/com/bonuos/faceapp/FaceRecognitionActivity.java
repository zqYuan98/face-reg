package com.bonuos.faceapp;

import android.Manifest;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.ImageFormat;
import android.graphics.Matrix;
import android.graphics.Rect;
import android.graphics.RectF;
import android.graphics.YuvImage;
import android.os.Bundle;
import android.util.Log;
import android.view.View;
import android.widget.ImageView;
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
import com.bonuos.facesdk.RecognitionResult;
import com.bonuos.facesdk.LivenessResult;
import com.bonuos.faceapp.widget.RectView;
import com.bumptech.glide.Glide;
import com.google.common.util.concurrent.ListenableFuture;

import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class FaceRecognitionActivity extends AppCompatActivity {

    private static final String TAG = "FaceActivity";
    private static final int PERMISSION_REQUEST_CODE = 10;
    private static final long RECOGNITION_INTERVAL_MS = 1; // 500ms 识别间隔

    private PreviewView previewView;
    private RectView rectView;
    private TextView tvStatus;
    private View statusIndicator;
    private Group groupUserInfo;
    // private ImageView ivAvatar;
    private TextView tvUserName;
    private TextView tvUserId;
    private TextView tvSimilarityValue;

    private com.bonuos.facesdk.SyncManager syncManager; // 添加 SyncManager
    private FaceRecognizer faceRecognizer;
    private com.bonuos.facesdk.LivenessDetector livenessDetector;
    private ExecutorService cameraExecutor;
    private long lastAnalysisTime = 0;
    private int cameraLensFacing = CameraSelector.LENS_FACING_FRONT;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_face_recognition);

        initViews();
        initFaceSDK();
        
        syncManager = new com.bonuos.facesdk.SyncManager(this); // 初始化 SyncManager

        if (allPermissionsGranted()) {
            startCamera();
        } else {
            ActivityCompat.requestPermissions(this,
                    new String[] { Manifest.permission.CAMERA }, PERMISSION_REQUEST_CODE);
        }

        cameraExecutor = Executors.newSingleThreadExecutor();
    }

    private void initViews() {
        previewView = findViewById(R.id.previewView);
        rectView = findViewById(R.id.rectView);
        tvStatus = findViewById(R.id.tv_status);
        statusIndicator = findViewById(R.id.status_indicator);
        groupUserInfo = findViewById(R.id.group_user_info);
        // ivAvatar = findViewById(R.id.iv_avatar);
        tvUserName = findViewById(R.id.tv_user_name);
        tvUserId = findViewById(R.id.tv_user_id);
        tvSimilarityValue = findViewById(R.id.tv_similarity_value);

        findViewById(R.id.btn_back).setOnClickListener(v -> finish());
        findViewById(R.id.btn_switch_camera).setOnClickListener(v -> toggleCamera());
    }

    private void toggleCamera() {
        if (cameraLensFacing == CameraSelector.LENS_FACING_BACK) {
            cameraLensFacing = CameraSelector.LENS_FACING_FRONT;
        } else {
            cameraLensFacing = CameraSelector.LENS_FACING_BACK;
        }
        startCamera();
    }

    private void initFaceSDK() {
        try {
            faceRecognizer = new FaceRecognizer(this);
            livenessDetector = new com.bonuos.facesdk.LivenessDetector();
            livenessDetector.init(this);

            // 预加载数据
            FaceRepository repository = new FaceRepository(this);
            int userCount = repository.getAllUsers().size();
            repository.close(); // 及时关闭
            Log.i(TAG, "Loaded " + userCount + " users from database.");
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

                ImageAnalysis imageAnalysis = new ImageAnalysis.Builder()
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
            // 启动自动同步（WebSocket）
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
        if (isFinishing() || isDestroyed()) {
            image.close();
            return;
        }
        long currentTime = System.currentTimeMillis();

        // 性能优化：控制识别频率 (500ms)
        if (currentTime - lastAnalysisTime < RECOGNITION_INTERVAL_MS) {
            image.close();
            return;
        }
        lastAnalysisTime = currentTime;

        Bitmap bitmap = imageProxyToBitmap(image);
        if (bitmap != null) {
            // 先进行活体检测 (同步调用)
            LivenessResult livenessResult = livenessDetector.detect(bitmap);
            float livenessScore = livenessResult.score;
            boolean isLive = livenessScore > 0.7f;

            // Draw face box
            if (livenessResult.faceBox != null) {
                int rotationDegrees = image.getImageInfo().getRotationDegrees();
                runOnUiThread(() -> {
                    com.mv.engine.FaceBox box = livenessResult.faceBox;
                    
                    Matrix matrix = new Matrix();
                    
                    // Image dimensions
                    float iw = bitmap.getWidth();
                    float ih = bitmap.getHeight();
                    
                    // View dimensions
                    float vw = previewView.getWidth();
                    float vh = previewView.getHeight();
                    
                    // 1. Translate to center
                    matrix.postTranslate(-iw / 2f, -ih / 2f);
                    
                    // 2. Rotate
                    matrix.postRotate(rotationDegrees);
                    
                    // 3. Mirror (if front)
                    if (cameraLensFacing == CameraSelector.LENS_FACING_FRONT) {
                        matrix.postScale(-1, 1);
                    }
                    
                    // 4. Scale to fill
                    boolean rotated = rotationDegrees == 90 || rotationDegrees == 270;
                    float rotatedW = rotated ? ih : iw;
                    float rotatedH = rotated ? iw : ih;
                    
                    float scale = Math.max(vw / rotatedW, vh / rotatedH);
                    matrix.postScale(scale, scale);
                    
                    // 5. Translate to view center
                    matrix.postTranslate(vw / 2f, vh / 2f);
                    
                    RectF src = new RectF(box.getLeft(), box.getTop(), box.getRight(), box.getBottom());
                    RectF dst = new RectF();
                    matrix.mapRect(dst, src);
                    
                    rectView.setX1((int) dst.left);
                    rectView.setY1((int) dst.top);
                    rectView.setX2((int) dst.right);
                    rectView.setY2((int) dst.bottom);
                    rectView.setConfidence(livenessScore);
                    rectView.setColor(isLive ? getColor(R.color.tech_success) : getColor(android.R.color.holo_red_dark));
                    rectView.postInvalidate();
                });
            } else {
                 runOnUiThread(() -> {
                    rectView.setX1(0);
                    rectView.setY1(0);
                    rectView.setX2(0);
                    rectView.setY2(0);
                    rectView.postInvalidate();
                 });
            }

            if (isLive) {
                // 活体检测通过，进行人脸识别
                // 再次检查是否已销毁，避免使用已释放的SDK
                if (isFinishing() || isDestroyed()) return;
                
                try {
                    RecognitionResult result = faceRecognizer.recognize(bitmap);
                    runOnUiThread(() -> updateUI(result));
                } catch (Exception e) {
                    Log.e(TAG, "Recognition failed", e);
                }
            } else {
                // 活体检测失败
                runOnUiThread(() -> {
                    tvStatus.setText("非活体 (分数: " + String.format("%.2f", livenessScore) + ")");
                    tvStatus.setTextColor(getColor(android.R.color.holo_red_dark));
                    statusIndicator.setBackgroundTintList(
                            ContextCompat.getColorStateList(this, android.R.color.holo_red_dark));
                    groupUserInfo.setVisibility(View.GONE);
                });
            }
        }

        image.close();
    }

    private void updateUI(RecognitionResult result) {
        if (result.isSuccess()) {
            // 识别成功
            tvStatus.setText(R.string.status_success);
            tvStatus.setTextColor(getColor(R.color.tech_success));
            statusIndicator.setBackgroundTintList(ContextCompat.getColorStateList(this, R.color.tech_success));

            groupUserInfo.setVisibility(View.VISIBLE);

            FaceRepository.FaceUser user = result.getUser();
            tvUserName.setText(user.name);
            tvUserId.setText("ID: " + user.userNo);

            int similarityPercent = (int) (result.getSimilarity() * 100);
            tvSimilarityValue.setText(similarityPercent + "%");

        } else {
             // 识别失败 / 未知人员
            tvStatus.setText("未知人员");
            tvStatus.setTextColor(getColor(android.R.color.holo_orange_dark));
            statusIndicator.setBackgroundTintList(ContextCompat.getColorStateList(this, android.R.color.holo_orange_dark));

            groupUserInfo.setVisibility(View.GONE);
        }
    }

    private Bitmap imageProxyToBitmap(ImageProxy image) {
        if (image.getFormat() != ImageFormat.YUV_420_888) {
            return null;
        }
        ByteBuffer yBuffer = image.getPlanes()[0].getBuffer();
        ByteBuffer uBuffer = image.getPlanes()[1].getBuffer();
        ByteBuffer vBuffer = image.getPlanes()[2].getBuffer();

        int ySize = yBuffer.remaining();
        int uSize = uBuffer.remaining();
        int vSize = vBuffer.remaining();

        byte[] nv21 = new byte[ySize + uSize + vSize];

        yBuffer.get(nv21, 0, ySize);
        vBuffer.get(nv21, ySize, vSize);
        uBuffer.get(nv21, ySize + vSize, uSize);

        YuvImage yuvImage = new YuvImage(nv21, ImageFormat.NV21, image.getWidth(), image.getHeight(), null);
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        yuvImage.compressToJpeg(new Rect(0, 0, image.getWidth(), image.getHeight()), 100, out);
        byte[] imageBytes = out.toByteArray();

        // 注意：这里可能需要处理旋转，视设备而定
        return BitmapFactory.decodeByteArray(imageBytes, 0, imageBytes.length);
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
        super.onDestroy();
        if (cameraExecutor != null) {
            cameraExecutor.shutdown();
        }
        if (faceRecognizer != null) {
            faceRecognizer.release();
        }
        if (livenessDetector != null) {
            livenessDetector.release();
        }
    }
}
