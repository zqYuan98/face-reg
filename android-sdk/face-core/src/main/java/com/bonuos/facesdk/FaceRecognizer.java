package com.bonuos.facesdk;

import android.content.Context;
import android.graphics.Bitmap;
import com.facesdk.wrapper.FaceSDKWrapper;
import java.util.List;

public class FaceRecognizer {
    private FaceSDKWrapper sdkWrapper;
    private FaceRepository repository;
    private volatile List<FaceRepository.FaceUser> cachedUsers;
    private float threshold = 0.65f;
    private final Runnable datasetListener = this::reloadUsers;

    public FaceRecognizer(Context context) {
        sdkWrapper = new FaceSDKWrapper();
        sdkWrapper.init(context);
        repository = new FaceRepository(context);
        FaceDatasetCoordinator.LOCK.writeLock().lock();
        try {
            reloadUsers();
            FaceDatasetCoordinator.add(datasetListener);
        } finally { FaceDatasetCoordinator.LOCK.writeLock().unlock(); }
    }

    public void reloadUsers() {
        FaceDatasetCoordinator.LOCK.writeLock().lock();
        try {
            // If loading fails, do not keep recognizing people from the previous dataset.
            cachedUsers = null;
            if (repository != null) cachedUsers = repository.getAllUsers();
        } finally { FaceDatasetCoordinator.LOCK.writeLock().unlock(); }
    }

    public float[] extractFeature(Bitmap bitmap) {
        FaceDatasetCoordinator.LOCK.readLock().lock();
        try { return sdkWrapper == null ? null : sdkWrapper.extractFeature(bitmap); }
        finally { FaceDatasetCoordinator.LOCK.readLock().unlock(); }
    }

    public RecognitionResult recognize(Bitmap bitmap) {
        FaceDatasetCoordinator.LOCK.readLock().lock();
        try {
            if (sdkWrapper == null) return new RecognitionResult(false, "SDK已释放");
            float[] feature = sdkWrapper.extractFeature(bitmap);
            if (feature == null) return new RecognitionResult(false, "未检测到人脸或质量不合格");
            List<FaceRepository.FaceUser> users = cachedUsers;
            if (users == null || users.isEmpty()) return new RecognitionResult(false, "本地人员库为空或正在恢复");
            FaceRepository.FaceUser best = null;
            float similarity = 0f;
            for (FaceRepository.FaceUser user : users) {
                if (user.feature == null) continue;
                float score = sdkWrapper.compare(feature, user.feature);
                if (score > similarity) { similarity = score; best = user; }
            }
            return similarity >= threshold && best != null ? new RecognitionResult(true, best, similarity) :
                    new RecognitionResult(false, "未识别到已知人员 (相似度: " + similarity + ")");
        } finally { FaceDatasetCoordinator.LOCK.readLock().unlock(); }
    }

    public void setThreshold(float threshold) { this.threshold = threshold; }

    public void release() {
        FaceDatasetCoordinator.LOCK.writeLock().lock();
        try {
            FaceDatasetCoordinator.remove(datasetListener);
            cachedUsers = null;
            if (sdkWrapper != null) { sdkWrapper.release(); sdkWrapper = null; }
            if (repository != null) { repository.close(); repository = null; }
        } finally { FaceDatasetCoordinator.LOCK.writeLock().unlock(); }
    }
}
