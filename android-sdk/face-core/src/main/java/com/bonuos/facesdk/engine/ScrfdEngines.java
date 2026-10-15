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

    /**
     * Whole-frame scan at least every this many frames: a new face is found within ~330 ms at 30 fps.
     * The whole-frame scan costs ~2x a region scan on a Helio P60 (44 vs 22 ms).
     */
    public static final int FULL_SCAN_EVERY = 10;
    /** Search square around a tracked face: twice its size, sampled to this long side. */
    public static final float REGION_SCALE = 2f;
    public static final int REGION_LONG_SIDE = 160;

    private final NativeAnalyzer analyzer;
    /** Second copy of the liveness models for the session's liveness worker thread. */
    private final NativeLiveness asyncLiveness;
    private final int longSide;
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

    /** {@code fullScanEvery} 1 scans every frame (no tracking), for measurements. */
    public ScrfdEngines(AssetManager assets, int longSide, float threshold, int fullScanEvery) {
        this.longSide = longSide;
        this.threshold = threshold;
        this.tracker = new RoiTracker(fullScanEvery, REGION_SCALE, 0.2f);
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
        analyzer = new NativeAnalyzer(assets, NativeEngine.INSIGHTFACE_DETECTOR, 2, names, scales, sizes);
        try {
            asyncLiveness = new NativeLiveness(assets, names, scales, sizes);
        } catch (RuntimeException error) {
            analyzer.close();
            throw error;
        }
    }

    @Override
    public List<Detection> detect(CameraFrame frame) {
        current = null;
        if (!analyzer.setNv21(frame.nv21, frame.width, frame.height, frame.rotationDegrees)) {
            tracker.reset();
            return new ArrayList<>();
        }
        current = frame;
        return tracker.next(new RoiTracker.Source() {
            @Override public List<Detection> full() {
                return unpack(analyzer.detect(longSide, threshold));
            }

            @Override public List<Detection> region(FaceRect r) {
                return unpack(analyzer.detectRegion(r.left, r.top, r.width(), r.height(), REGION_LONG_SIDE, threshold));
            }
        });
    }

    /** Forget tracked faces, e.g. after a camera switch or between unrelated clips. */
    public void reset() {
        tracker.reset();
    }

    public RoiTracker tracker() { return tracker; }

    private static List<Detection> unpack(float[] packed) {
        List<Detection> out = new ArrayList<>(packed == null ? 0 : packed.length / 15);
        if (packed == null) return out;
        for (int i = 0; i + 15 <= packed.length; i += 15) {
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
