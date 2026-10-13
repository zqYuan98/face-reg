package com.bonuos.facesdk.engine;

import java.util.Collections;
import java.util.List;

/**
 * Immutable 1:N search index: every enrolled feature L2-normalised into one row-major N x D
 * matrix, scanned in a single pass. Replaces one JNI compare call per enrolled person. Publish a
 * new instance to change the dataset; readers on other threads keep using the one they hold.
 *
 * @param <T> what an entry identifies, e.g. a user record
 */
public final class Gallery<T> {
    /** Optional replacement for the built-in cosine, for engines whose score is on another scale. */
    public interface Scorer {
        float score(float[] probe, float[] enrolled);
    }

    public static final class Entry<T> {
        public final T value;
        public final float[] feature;

        public Entry(T value, float[] feature) {
            this.value = value;
            this.feature = feature;
        }
    }

    /** Best and runner-up of one search. {@code best} is null only when the gallery is empty. */
    public static final class Match<T> {
        public final T best;
        public final float score;
        /** Runner-up score, or -inf with fewer than two entries. */
        public final float secondScore;

        Match(T best, float score, float secondScore) {
            this.best = best;
            this.score = score;
            this.secondScore = secondScore;
        }

        public float margin() { return score - secondScore; }
    }

    private final int dimension;
    private final float[] matrix;
    private final Object[] values;
    private final float[][] raw; // only kept when a custom scorer needs the original vectors
    private final Scorer scorer;
    private final int skipped;

    private Gallery(int dimension, float[] matrix, Object[] values, float[][] raw, Scorer scorer, int skipped) {
        this.dimension = dimension;
        this.matrix = matrix;
        this.values = values;
        this.raw = raw;
        this.scorer = scorer;
        this.skipped = skipped;
    }

    public static <T> Gallery<T> empty() {
        return new Gallery<>(0, new float[0], new Object[0], null, null, 0);
    }

    public static <T> Gallery<T> build(List<Entry<T>> entries, int dimension) {
        return build(entries, dimension, null);
    }

    /**
     * Entries whose feature is null, of another dimension, zero or not finite are skipped and
     * counted, never compared. A gallery silently mixing feature versions would score garbage.
     */
    public static <T> Gallery<T> build(List<Entry<T>> entries, int dimension, Scorer scorer) {
        if (dimension <= 0) throw new IllegalArgumentException("dimension");
        if (entries == null) entries = Collections.emptyList();
        int valid = 0;
        for (Entry<T> e : entries) if (normOf(e.feature, dimension) > 0) valid++;
        float[] matrix = new float[valid * dimension];
        Object[] values = new Object[valid];
        float[][] raw = scorer == null ? null : new float[valid][];
        int row = 0;
        for (Entry<T> e : entries) {
            double norm = normOf(e.feature, dimension);
            if (!(norm > 0)) continue;
            float scale = (float) (1.0 / norm);
            int base = row * dimension;
            for (int i = 0; i < dimension; i++) matrix[base + i] = e.feature[i] * scale;
            values[row] = e.value;
            if (raw != null) raw[row] = e.feature.clone();
            row++;
        }
        return new Gallery<>(dimension, matrix, values, raw, scorer, entries.size() - valid);
    }

    private static double normOf(float[] feature, int dimension) {
        if (feature == null || feature.length != dimension) return 0;
        double sum = 0;
        for (float v : feature) {
            if (!Float.isFinite(v)) return 0;
            sum += (double) v * v;
        }
        return Double.isFinite(sum) ? Math.sqrt(sum) : 0;
    }

    public int size() { return values.length; }
    public int dimension() { return dimension; }
    /** Entries rejected at build time (missing, wrong dimension, zero or non-finite). */
    public int skipped() { return skipped; }

    /** Top-2 search. Returns null when the probe is unusable; {@code best == null} when empty. */
    @SuppressWarnings("unchecked")
    public Match<T> search(float[] probe) {
        if (probe == null || probe.length != dimension && dimension != 0) return null;
        if (values.length == 0) return new Match<>(null, Float.NEGATIVE_INFINITY, Float.NEGATIVE_INFINITY);
        float best = Float.NEGATIVE_INFINITY, second = Float.NEGATIVE_INFINITY;
        int bestRow = -1;
        if (scorer != null) {
            for (int r = 0; r < values.length; r++) {
                float s = scorer.score(probe, raw[r]);
                if (s > best) { second = best; best = s; bestRow = r; } else if (s > second) second = s;
            }
        } else {
            double norm = normOf(probe, dimension);
            if (!(norm > 0)) return null;
            float inv = (float) (1.0 / norm);
            for (int r = 0, base = 0; r < values.length; r++, base += dimension) {
                float dot = 0f;
                for (int i = 0; i < dimension; i++) dot += probe[i] * matrix[base + i];
                float s = dot * inv;
                if (s > best) { second = best; best = s; bestRow = r; } else if (s > second) second = s;
            }
        }
        return bestRow < 0 ? null : new Match<>((T) values[bestRow], best, second);
    }

    /** Cosine of two raw vectors, for the self-check against another engine's compare. */
    public static float cosine(float[] a, float[] b) {
        if (a == null || b == null || a.length != b.length || a.length == 0) return Float.NaN;
        double dot = 0, na = 0, nb = 0;
        for (int i = 0; i < a.length; i++) {
            dot += (double) a[i] * b[i];
            na += (double) a[i] * a[i];
            nb += (double) b[i] * b[i];
        }
        return na > 0 && nb > 0 ? (float) (dot / Math.sqrt(na * nb)) : Float.NaN;
    }
}
