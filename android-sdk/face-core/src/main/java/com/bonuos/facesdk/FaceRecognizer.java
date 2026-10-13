package com.bonuos.facesdk;

import android.content.Context;
import android.graphics.Bitmap;
import android.util.Log;
import com.bonuos.facesdk.engine.FaceSession;
import com.bonuos.facesdk.engine.Gallery;
import com.bonuos.facesdk.engine.NativeEngine;
import com.facesdk.wrapper.FaceSDKWrapper;
import java.util.ArrayList;
import java.util.List;

public class FaceRecognizer implements FaceSession.GallerySource<FaceRepository.FaceUser> {
    /** Which implementation of feature version recognizer_v1_1024 extracts features. */
    public enum Backend {
        /** Prebuilt libface_sdk_jni.so (no source). */
        LEGACY_SDK,
        /** libfaceengine.so built from src/main/cpp/engine; same models, about half the work per call. */
        NATIVE,
        /** NATIVE with the int8 recognizer; see {@link NativeEngine#RECOGNIZER_INT8}. */
        NATIVE_INT8
    }

    private static final String TAG = "FaceRecognizer";
    /** Feature dimension of the bundled recognizer (feature version recognizer_v1_1024). */
    public static final int FEATURE_DIMENSION = 1024;
    /** Largest |java cosine - native compare| accepted before falling back to the native scorer. */
    private static final float SCORER_TOLERANCE = 1e-3f;

    private FaceSDKWrapper sdkWrapper;
    private NativeEngine nativeEngine;
    private NativeEngine.LandmarkMode landmarkMode = NativeEngine.LandmarkMode.TRUNCATED;
    private FaceRepository repository;
    private volatile Gallery<FaceRepository.FaceUser> gallery = Gallery.empty();
    /** null until checked; true when Java cosine reproduces the native compare. */
    private Boolean javaScorerMatches;
    private float threshold = 0.65f;
    private final Runnable datasetListener = this::reloadUsers;

    public FaceRecognizer(Context context) {
        this(context, Backend.LEGACY_SDK);
    }

    public FaceRecognizer(Context context, Backend backend) {
        if (backend != Backend.LEGACY_SDK) {
            nativeEngine = new NativeEngine(context.getAssets(),
                    backend == Backend.NATIVE_INT8 ? NativeEngine.RECOGNIZER_INT8 : NativeEngine.RECOGNIZER_FP32, 4, true);
            javaScorerMatches = true; // our engine's similarity is defined as cosine
        } else {
            sdkWrapper = new FaceSDKWrapper();
            sdkWrapper.init(context);
        }
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
            gallery = Gallery.empty();
            if (repository == null) return;
            List<FaceRepository.FaceUser> users = repository.getAllUsers();
            List<Gallery.Entry<FaceRepository.FaceUser>> entries = new ArrayList<>(users.size());
            for (FaceRepository.FaceUser user : users) {
                entries.add(new Gallery.Entry<>(user, user.feature));
            }
            Gallery.Scorer scorer = javaScorerMatches(entries) ? null : this::nativeScore;
            Gallery<FaceRepository.FaceUser> built = Gallery.build(entries, FEATURE_DIMENSION, scorer);
            // The gallery owns a normalised copy; drop the per-user arrays to halve resident memory.
            for (FaceRepository.FaceUser user : users) user.feature = null;
            if (built.skipped() > 0) Log.w(TAG, built.skipped() + " users skipped: missing or incompatible feature");
            gallery = built;
        } finally { FaceDatasetCoordinator.LOCK.writeLock().unlock(); }
    }

    /**
     * The prebuilt library's compare is assumed to be cosine on normalised features (SeetaFace
     * style). Verify that once on real enrolled pairs; if it is not, keep its scale by scoring with
     * the native call, so thresholds tuned on the old path stay valid either way.
     */
    private boolean javaScorerMatches(List<Gallery.Entry<FaceRepository.FaceUser>> entries) {
        if (javaScorerMatches != null) return javaScorerMatches;
        List<float[]> sample = new ArrayList<>();
        for (Gallery.Entry<FaceRepository.FaceUser> entry : entries) {
            if (entry.feature != null && entry.feature.length == FEATURE_DIMENSION) sample.add(entry.feature);
            if (sample.size() == 6) break;
        }
        if (sample.size() < 2 || sdkWrapper == null) return true; // nothing to check yet; re-check next load
        float worst = 0f;
        for (int i = 0; i + 1 < sample.size(); i++) {
            float expected = sdkWrapper.compare(sample.get(i), sample.get(i + 1));
            worst = Math.max(worst, Math.abs(expected - Gallery.cosine(sample.get(i), sample.get(i + 1))));
        }
        javaScorerMatches = worst <= SCORER_TOLERANCE;
        if (!javaScorerMatches) Log.w(TAG, "Native compare is not plain cosine (diff " + worst + "); using native scorer");
        return javaScorerMatches;
    }

    private float nativeScore(float[] probe, float[] enrolled) {
        FaceSDKWrapper wrapper = sdkWrapper;
        return wrapper == null ? Float.NEGATIVE_INFINITY : wrapper.compare(probe, enrolled);
    }

    /** Current immutable snapshot; safe to search from any thread. */
    @Override
    public Gallery<FaceRepository.FaceUser> gallery() { return gallery; }

    /** Native backend only; null for the legacy one. Use it to build a NativeFeatureEngine. */
    public NativeEngine nativeEngine() { return nativeEngine; }

    /** Landmark mode for still images on the native backend. */
    public void setLandmarkMode(NativeEngine.LandmarkMode mode) { landmarkMode = mode; }

    public float[] extractFeature(Bitmap bitmap) {
        FaceDatasetCoordinator.LOCK.readLock().lock();
        try { return extractLocked(bitmap); }
        finally { FaceDatasetCoordinator.LOCK.readLock().unlock(); }
    }

    private float[] extractLocked(Bitmap bitmap) {
        if (nativeEngine != null) return nativeEngine.extract(bitmap, landmarkMode, null);
        return sdkWrapper == null ? null : sdkWrapper.extractFeature(bitmap);
    }

    public RecognitionResult recognize(Bitmap bitmap) {
        FaceDatasetCoordinator.LOCK.readLock().lock();
        try {
            if (sdkWrapper == null && nativeEngine == null) return new RecognitionResult(false, "SDK已释放");
            float[] feature = extractLocked(bitmap);
            if (feature == null) return new RecognitionResult(false, "未检测到人脸或质量不合格");
            Gallery<FaceRepository.FaceUser> current = gallery;
            if (current.size() == 0) return new RecognitionResult(false, "本地人员库为空或正在恢复");
            Gallery.Match<FaceRepository.FaceUser> match = current.search(feature);
            if (match == null) return new RecognitionResult(false, "特征维度与本地人员库不一致");
            return match.score >= threshold && match.best != null ? new RecognitionResult(true, match.best, match.score) :
                    new RecognitionResult(false, "未识别到已知人员 (相似度: " + match.score + ")");
        } finally { FaceDatasetCoordinator.LOCK.readLock().unlock(); }
    }

    public void setThreshold(float threshold) { this.threshold = threshold; }

    public void release() {
        FaceDatasetCoordinator.LOCK.writeLock().lock();
        try {
            FaceDatasetCoordinator.remove(datasetListener);
            gallery = Gallery.empty();
            if (sdkWrapper != null) { sdkWrapper.release(); sdkWrapper = null; }
            if (nativeEngine != null) { nativeEngine.close(); nativeEngine = null; }
            if (repository != null) { repository.close(); repository = null; }
        } finally { FaceDatasetCoordinator.LOCK.writeLock().unlock(); }
    }
}
