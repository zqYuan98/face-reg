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
 * replacing {@link MvEngines}. One native conversion per frame feeds both. SCRFD keeps small and
 * distant faces far more stably than the minivision detector and also returns five points.
 * Use from the analysis thread only.
 */
public final class ScrfdEngines implements FaceSession.Detector, FaceSession.LivenessScorer, AutoCloseable {
    /**
     * The liveness models were trained on minivision's square boxes. Measured on 1169 faces
     * (2026-10-13): that square shares the SCRFD box centre and its side is 0.868 x the SCRFD
     * box height (IQR 0.057).
     */
    static final float LIVENESS_SIDE_PER_HEIGHT = 0.868f;

    private final NativeAnalyzer analyzer;
    private final int longSide;
    private final float threshold;
    private CameraFrame current;

    /**
     * @param longSide  detector input long side; 480 finds faces down to ~40 px in a 1280 px frame
     * @param threshold SCRFD score threshold (0.5 is insightface's default)
     */
    public ScrfdEngines(AssetManager assets, int longSide, float threshold) {
        this.longSide = longSide;
        this.threshold = threshold;
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
    }

    @Override
    public List<Detection> detect(CameraFrame frame) {
        current = null;
        if (!analyzer.setNv21(frame.nv21, frame.width, frame.height, frame.rotationDegrees)) return new ArrayList<>();
        current = frame;
        float[] packed = analyzer.detect(longSide, threshold);
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

    @Override
    public void close() {
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
