package com.bonuos.facesdk.engine;

import com.bonuos.facesdk.image.Nv21;

/**
 * Cheap image-quality measures on the face luma, sampled to a fixed grid so the cost does not
 * depend on face size. Runs on the analysis thread before a crop is sent to the recognizer.
 */
public final class LumaQuality {
    public static final int GRID = 64;
    private final float[] grid = new float[GRID * GRID];

    public float brightness;
    /** Variance of the 4-neighbour Laplacian over the grid; larger is sharper. */
    public float sharpness;

    /** Measures the central 80% of the face box, which keeps background edges out of the score. */
    public void measure(CameraFrame frame, FaceRect face) {
        float mx = face.width() * 0.1f, my = face.height() * 0.1f;
        Nv21.sampleLuma(frame.nv21, frame.width, frame.height, frame.rotationDegrees,
                face.left + mx, face.top + my, face.right - mx, face.bottom - my, GRID, GRID, grid);
        brightness = mean(grid);
        sharpness = laplacianVariance(grid, GRID, GRID);
    }

    static float mean(float[] values) {
        double sum = 0;
        for (float v : values) sum += v;
        return (float) (sum / values.length);
    }

    static float laplacianVariance(float[] g, int w, int h) {
        double sum = 0, sumSq = 0;
        int n = 0;
        for (int y = 1; y < h - 1; y++) {
            for (int x = 1; x < w - 1; x++) {
                int i = y * w + x;
                double lap = g[i - 1] + g[i + 1] + g[i - w] + g[i + w] - 4 * g[i];
                sum += lap;
                sumSq += lap * lap;
                n++;
            }
        }
        double m = sum / n;
        return (float) (sumSq / n - m * m);
    }
}
