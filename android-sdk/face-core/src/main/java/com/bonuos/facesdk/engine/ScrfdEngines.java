package com.bonuos.facesdk.engine;

import android.content.res.AssetManager;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/**
 * SCRFD-500M detection plus the bundled silent-liveness models behind the session interfaces,
 * replacing {@link MvEngines}. SCRFD keeps small and distant faces far more stably than the
 * minivision detector and also returns five points. Detection follows faces with a
 * {@link RoiTracker}: a whole-frame scan every few frames, a small region around each face
 * otherwise; each scan converts only the pixels it feeds the detector. Analysis thread only.
 */
public final class ScrfdEngines implements FaceSession.Detector, FaceSession.LivenessScorer,
        FaceSession.LivenessEngine, AutoCloseable {
    /**
     * The liveness models were trained on minivision's square boxes. Measured on 1169 faces
     * (2026-10-13): that square shares the SCRFD box centre and its side is 0.868 x the SCRFD
     * box height (IQR 0.057).
     */
    static final float LIVENESS_SIDE_PER_HEIGHT = 0.868f;

    /** Whole-frame interval in actually analyzed frames, not a wall-clock discovery guarantee. */
    public static final int FULL_SCAN_EVERY = AnalysisProfile.FULL_SCAN_EVERY;
    /** Search square around a tracked face: twice its size, sampled to this long side. */
    public static final float REGION_SCALE = AnalysisProfile.REGION_SCALE;
    public static final int REGION_LONG_SIDE = AnalysisProfile.REGION_LONG_SIDE;

    /** Package-local seam: JVM tests exercise the production failure path without loading JNI. */
    interface Analyzer {
        boolean setNv21(byte[] nv21, int width, int height, int rotation);
        float[] detect(int longSide, float threshold);
        float[] detectRegion(float x, float y, float w, float h, int longSide, float threshold);
        float liveness(int left, int top, int right, int bottom);
        int threads();
        void close();
    }

    interface LivenessWorker {
        float score(byte[] nv21, int width, int height, int rotation, int left, int top, int right, int bottom);
        void close();
    }

    private final Analyzer analyzer;
    /** Second copy of the liveness models for the session's liveness worker thread. */
    private final LivenessWorker asyncLiveness;
    private final int longSide, regionLongSide;
    private final float threshold;
    private final RoiTracker tracker;
    private CameraFrame current;

    /**
     * @param longSide  whole-frame detector input long side; 320 finds faces down to ~60 px in a
     *                  1280 px frame (the session's minimum face size is 64)
     * @param threshold SCRFD score threshold (0.5 is insightface's default)
     */
    public ScrfdEngines(AssetManager assets, int longSide, float threshold) {
        this(assets, longSide, threshold, FULL_SCAN_EVERY);
    }

    public ScrfdEngines(AssetManager assets, AnalysisProfile profile) {
        this(assets, profile.detectorLongSide, profile.detectorThreshold, profile.fullScanEvery,
                profile.regionLongSide, profile.regionScale, profile.regionMinIou, profile.detectorThreads);
    }

    /** {@code fullScanEvery} 1 scans every frame (no tracking), for measurements. */
    public ScrfdEngines(AssetManager assets, int longSide, float threshold, int fullScanEvery) {
        this(assets, longSide, threshold, fullScanEvery, REGION_LONG_SIDE, REGION_SCALE,
                AnalysisProfile.REGION_MIN_IOU, AnalysisProfile.DETECTOR_THREADS);
    }

    private ScrfdEngines(AssetManager assets, int longSide, float threshold, int fullScanEvery,
                         int regionLongSide, float regionScale, float regionMinIou, int threads) {
        validateSettings(longSide, threshold, regionLongSide, threads);
        this.longSide = longSide;
        this.regionLongSide = regionLongSide;
        this.threshold = threshold;
        this.tracker = new RoiTracker(fullScanEvery, regionScale, regionMinIou);
        String[] names;
        float[] scales;
        int[] sizes;
        try {
            JSONArray configs = new JSONArray(read(assets, "live/config.json"));
            names = new String[configs.length()];
            scales = new float[configs.length()];
            sizes = new int[configs.length()];
            for (int i = 0; i < configs.length(); i++) {
                JSONObject c = configs.getJSONObject(i);
                names[i] = c.getString("name");
                scales[i] = (float) c.getDouble("scale");
                sizes[i] = c.getInt("width");
            }
        } catch (IOException | JSONException error) {
            throw new IllegalStateException("Cannot read live/config.json", error);
        }
        analyzer = new NativeAnalyzer(assets, NativeEngine.INSIGHTFACE_DETECTOR, threads, names, scales, sizes);
        try {
            asyncLiveness = new NativeLiveness(assets, names, scales, sizes);
        } catch (RuntimeException | Error error) {
            analyzer.close();
            throw error;
        }
    }

    ScrfdEngines(Analyzer analyzer, LivenessWorker asyncLiveness, AnalysisProfile profile) {
        if (analyzer == null || asyncLiveness == null || profile == null)
            throw new IllegalArgumentException("Analyzer, liveness and profile are required");
        validateSettings(profile.detectorLongSide, profile.detectorThreshold, profile.regionLongSide, analyzer.threads());
        if (analyzer.threads() != profile.detectorThreads) throw new IllegalArgumentException("Detector threads differ from profile");
        this.analyzer = analyzer;
        this.asyncLiveness = asyncLiveness;
        this.longSide = profile.detectorLongSide;
        this.regionLongSide = profile.regionLongSide;
        this.threshold = profile.detectorThreshold;
        this.tracker = new RoiTracker(profile.fullScanEvery, profile.regionScale, profile.regionMinIou);
    }

    private static void validateSettings(int longSide, float threshold, int regionLongSide, int threads) {
        if (longSide < 32 || regionLongSide < 32 || !Float.isFinite(threshold)
                || threshold < 0 || threshold > 1 || threads < 1)
            throw new IllegalArgumentException("Invalid detector settings");
    }

    @Override
    public List<Detection> detect(CameraFrame frame) {
        current = null;
        try {
            if (!analyzer.setNv21(frame.nv21, frame.width, frame.height, frame.rotationDegrees))
                throw new IllegalStateException("Native analyzer rejected NV21 frame");
            List<Detection> detections = tracker.next(new RoiTracker.Source() {
                @Override public List<Detection> full() {
                    return unpack(analyzer.detect(longSide, threshold));
                }

                @Override public List<Detection> region(FaceRect r) {
                    return unpack(analyzer.detectRegion(r.left, r.top, r.width(), r.height(), regionLongSide, threshold));
                }
            });
            current = frame; // only a successful, complete observation enables synchronous scoring
            return detections;
        } catch (RuntimeException | Error failure) {
            tracker.reset();
            throw failure;
        }
    }

    /** Forget tracked faces, e.g. after a camera switch or between unrelated clips. */
    public void reset() {
        current = null;
        tracker.reset();
    }

    public RoiTracker tracker() { return tracker; }

    /** Construction values actually used by this detector, not a claim about measured timing/provider. */
    public java.util.Map<String, Object> effectiveSettings() {
        java.util.Map<String, Object> out = new java.util.LinkedHashMap<>();
        out.put("longSide", longSide);
        out.put("threshold", threshold);
        out.put("fullScanEvery", tracker.fullEvery());
        out.put("regionLongSide", regionLongSide);
        out.put("regionScale", tracker.regionScale());
        out.put("regionMinIou", tracker.minIou());
        out.put("detectorThreads", analyzer.threads());
        return java.util.Collections.unmodifiableMap(out);
    }

    private static List<Detection> unpack(float[] packed) {
        if (packed == null) throw new IllegalStateException("Native detector failed");
        if (packed.length % 15 != 0) throw new IllegalStateException("Malformed native detection output");
        List<Detection> out = new ArrayList<>(packed.length / 15);
        for (int i = 0; i < packed.length; i += 15) {
            for (int k = 0; k < 15; k++)
                if (!Float.isFinite(packed[i + k])) throw new IllegalStateException("Non-finite native detection output");
            if (!(packed[i + 2] > packed[i]) || !(packed[i + 3] > packed[i + 1])
                    || !Float.isFinite(packed[i + 2] - packed[i]) || !Float.isFinite(packed[i + 3] - packed[i + 1])
                    || packed[i + 4] < 0 || packed[i + 4] > 1)
                throw new IllegalStateException("Invalid native detection geometry or score");
            float[] kps = new float[10];
            System.arraycopy(packed, i + 5, kps, 0, 10);
            out.add(new Detection(new FaceRect(packed[i], packed[i + 1], packed[i + 2], packed[i + 3]), packed[i + 4], kps));
        }
        return out;
    }

    @Override
    public float score(CameraFrame frame, FaceRect face) {
        if (frame != current) throw new IllegalStateException("score() must follow detect() on the same frame");
        float side = face.height() * LIVENESS_SIDE_PER_HEIGHT;
        float left = face.centerX() - side / 2, top = face.centerY() - side / 2;
        return analyzer.liveness(Math.round(left), Math.round(top), Math.round(left + side) - 1, Math.round(top + side) - 1);
    }

    /**
     * Copies the frame (one array clone) and scores it on the caller's worker thread with this
     * object's second set of liveness models. One task at a time: FaceSession keeps a single
     * liveness evaluation in flight.
     */
    @Override
    public java.util.concurrent.Callable<Float> captureLiveness(CameraFrame frame, FaceRect face) {
        if (frame != current) throw new IllegalStateException("captureLiveness() must follow detect() on the same frame");
        final byte[] nv21 = frame.nv21.clone();
        final int w = frame.width, h = frame.height, rotation = frame.rotationDegrees;
        float side = face.height() * LIVENESS_SIDE_PER_HEIGHT;
        final int left = Math.round(face.centerX() - side / 2), top = Math.round(face.centerY() - side / 2);
        final int right = Math.round(face.centerX() - side / 2 + side) - 1, bottom = Math.round(face.centerY() - side / 2 + side) - 1;
        return () -> asyncLiveness.score(nv21, w, h, rotation, left, top, right, bottom);
    }

    @Override
    public void close() {
        asyncLiveness.close();
        analyzer.close();
    }

    private static String read(AssetManager assets, String name) throws IOException {
        try (InputStream in = assets.open(name); ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[4096];
            int n;
            while ((n = in.read(buffer)) > 0) out.write(buffer, 0, n);
            return new String(out.toByteArray(), StandardCharsets.UTF_8);
        }
    }
}
