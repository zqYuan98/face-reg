package com.bonuos.facesdk;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.util.Log;
import com.bonuos.facesdk.engine.FaceSession;
import com.bonuos.facesdk.engine.Gallery;
import com.bonuos.facesdk.engine.NativeEngine;
import com.facesdk.wrapper.FaceSDKWrapper;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.util.ArrayList;
import java.util.List;

public class FaceRecognizer implements FaceSession.GallerySource<FaceRepository.FaceUser> {
    /**
     * Which pipeline extracts features. The first three produce feature version recognizer_v1_1024
     * and use the features delivered by sync; the InsightFace ones are a different feature space,
     * so their gallery is built on the device from the synced enrolment photos.
     */
    public enum Backend {
        /** Prebuilt libface_sdk_jni.so (no source). */
        LEGACY_SDK,
        /** libfaceengine.so built from src/main/cpp/engine; same models, about half the work per call. */
        NATIVE,
        /** NATIVE with the int8 recognizer; see {@link NativeEngine#RECOGNIZER_INT8}. */
        NATIVE_INT8,
        /** InsightFace SCRFD-500M + MobileFaceNet (w600k_mbf), 512-d. Research licence: evaluation only. */
        INSIGHTFACE_MBF,
        /** InsightFace SCRFD-500M + IResNet-50 (w600k_r50), 512-d. Research licence: evaluation only. */
        INSIGHTFACE_R50,
        /** INSIGHTFACE_R50 with the int8 recognizer, ~3.5x faster. */
        INSIGHTFACE_R50_INT8;

        public boolean insightFace() { return this == INSIGHTFACE_MBF || this == INSIGHTFACE_R50 || this == INSIGHTFACE_R50_INT8; }
    }

    private static final String TAG = "FaceRecognizer";
    /** Feature dimension of the bundled recognizer (feature version recognizer_v1_1024). */
    public static final int FEATURE_DIMENSION = 1024;
    private static final int INSIGHTFACE_DIMENSION = 512;
    /** Largest |java cosine - native compare| accepted before falling back to the native scorer. */
    private static final float SCORER_TOLERANCE = 1e-3f;

    private FaceSDKWrapper sdkWrapper;
    private NativeEngine nativeEngine;
    private NativeEngine.LandmarkMode landmarkMode = NativeEngine.LandmarkMode.TRUNCATED;
    private FaceRepository repository;
    private final Backend backend;
    /** InsightFace only: per-photo feature cache, so enrolment runs once per photo and model. */
    private final File featureCache;
    private volatile Gallery<FaceRepository.FaceUser> gallery = Gallery.empty();
    /** null until checked; true when Java cosine reproduces the native compare. */
    private Boolean javaScorerMatches;
    private float threshold = 0.65f;
    private volatile FaceSession.GallerySnapshot<FaceRepository.FaceUser> snapshot =
            new FaceSession.GallerySnapshot<>(gallery, FaceDatasetCoordinator.EPOCH,
                    FaceDatasetCoordinator.EPOCH.generation(), false);
    private final Runnable datasetListener = this::reloadUsersLocked;

    public FaceRecognizer(Context context) {
        this(context, Backend.LEGACY_SDK);
    }

    public FaceRecognizer(Context context, Backend backend) {
        this.backend = backend;
        featureCache = backend.insightFace() ? new File(context.getFilesDir(), "feature-cache/" + backend.name().toLowerCase()) : null;
        if (backend.insightFace()) {
            nativeEngine = NativeEngine.insightFace(context.getAssets(),
                    backend == Backend.INSIGHTFACE_R50 ? NativeEngine.INSIGHTFACE_R50
                            : backend == Backend.INSIGHTFACE_R50_INT8 ? NativeEngine.INSIGHTFACE_R50_INT8 : NativeEngine.INSIGHTFACE_MBF, 4);
            javaScorerMatches = true;
        } else if (backend != Backend.LEGACY_SDK) {
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
            reloadUsersLocked();
            FaceDatasetCoordinator.add(datasetListener);
        } finally { FaceDatasetCoordinator.LOCK.writeLock().unlock(); }
    }

    public void reloadUsers() {
        // Reload all participating feature spaces as one local generation.
        FaceDatasetCoordinator.publish(() -> { });
    }

    private void reloadUsersLocked() {
        FaceDatasetCoordinator.LOCK.writeLock().lock();
        try {
            // If loading fails, do not keep recognizing people from the previous dataset.
            gallery = Gallery.empty();
            snapshot = new FaceSession.GallerySnapshot<>(gallery, FaceDatasetCoordinator.EPOCH,
                    FaceDatasetCoordinator.EPOCH.generation(), false);
            if (repository == null) return;
            List<FaceRepository.FaceUser> users = repository.getAllUsers();
            List<Gallery.Entry<FaceRepository.FaceUser>> entries = new ArrayList<>(users.size());
            long started = System.currentTimeMillis();
            for (FaceRepository.FaceUser user : users) {
                entries.add(new Gallery.Entry<>(user, backend.insightFace() ? enrolFromPhoto(user) : user.feature));
            }
            Gallery.Scorer scorer = javaScorerMatches(entries) ? null : this::nativeScore;
            Gallery<FaceRepository.FaceUser> built = Gallery.build(entries,
                    backend.insightFace() ? INSIGHTFACE_DIMENSION : FEATURE_DIMENSION, scorer);
            Log.i(TAG, backend + " gallery: " + built.size() + " users in " + (System.currentTimeMillis() - started) + " ms");
            // The gallery owns a normalised copy; drop the per-user arrays to halve resident memory.
            for (FaceRepository.FaceUser user : users) user.feature = null;
            if (built.skipped() > 0) Log.w(TAG, built.skipped() + " users skipped: missing or incompatible feature");
            gallery = built;
            snapshot = new FaceSession.GallerySnapshot<>(built, FaceDatasetCoordinator.EPOCH,
                    FaceDatasetCoordinator.EPOCH.generation(), true);
        } finally { FaceDatasetCoordinator.LOCK.writeLock().unlock(); }
    }

    /**
     * InsightFace feature of the user's synced enrolment photo, cached per photo file (the file
     * name already changes with every new photo revision). Null when there is no usable face.
     */
    private float[] enrolFromPhoto(FaceRepository.FaceUser user) {
        if (user.photoPath == null || user.photoPath.isEmpty()) return null;
        File photo = new File(user.photoPath);
        if (!photo.isFile()) return null;
        File cached = new File(featureCache, photo.getName() + ".f32");
        if (cached.length() == INSIGHTFACE_DIMENSION * 4L) {
            try (DataInputStream in = new DataInputStream(new FileInputStream(cached))) {
                float[] feature = new float[INSIGHTFACE_DIMENSION];
                for (int i = 0; i < feature.length; i++) feature[i] = in.readFloat();
                return feature;
            } catch (IOException ignored) {
                // fall through and re-extract
            }
        }
        BitmapFactory.Options options = new BitmapFactory.Options();
        options.inPreferredConfig = Bitmap.Config.ARGB_8888;
        Bitmap bitmap = BitmapFactory.decodeFile(photo.getPath(), options);
        if (bitmap == null) return null;
        float[] feature;
        try {
            feature = nativeEngine.extract(bitmap, landmarkMode, null);
        } finally {
            bitmap.recycle();
        }
        if (feature == null || feature.length != INSIGHTFACE_DIMENSION) {
            Log.w(TAG, "No usable face in enrolment photo of user " + user.id);
            return null;
        }
        if (featureCache.isDirectory() || featureCache.mkdirs()) {
            File partial = new File(featureCache, cached.getName() + ".part");
            try (DataOutputStream out = new DataOutputStream(new FileOutputStream(partial))) {
                for (float v : feature) out.writeFloat(v);
            } catch (IOException error) {
                Log.w(TAG, "Feature cache write failed", error);
            }
            if (!partial.renameTo(cached)) partial.delete();
        }
        return feature;
    }

    /** The feature space of this recognizer, for logs and reports. */
    public Backend backend() { return backend; }

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

    @Override public FaceSession.GallerySnapshot<FaceRepository.FaceUser> snapshot() { return snapshot; }

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
            FaceSession.GallerySnapshot<FaceRepository.FaceUser> view = snapshot;
            if (!view.ready || !view.epoch.isCurrent(view.generation))
                return new RecognitionResult(false, "本地人员库正在恢复");
            Gallery<FaceRepository.FaceUser> current = view.gallery;
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
            // Serialize individual-source removal with the same short authorization commit.
            synchronized (FaceDatasetCoordinator.EPOCH) {
                gallery = Gallery.empty();
                snapshot = new FaceSession.GallerySnapshot<>(gallery, FaceDatasetCoordinator.EPOCH,
                        FaceDatasetCoordinator.EPOCH.generation(), false);
            }
            if (sdkWrapper != null) { sdkWrapper.release(); sdkWrapper = null; }
            if (nativeEngine != null) { nativeEngine.close(); nativeEngine = null; }
            if (repository != null) { repository.close(); repository = null; }
        } finally { FaceDatasetCoordinator.LOCK.writeLock().unlock(); }
    }
}
