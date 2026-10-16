package com.bonuos.face.util;

import com.fasterxml.jackson.core.JsonFactory;
import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.core.JsonToken;

import java.util.Arrays;
import java.util.List;

public class FaceUtils {

    private static final JsonFactory JSON = new JsonFactory();

    /**
     * 计算两个特征向量的余弦相似度
     *
     * @param feature1 特征向量1
     * @param feature2 特征向量2
     * @return 相似度 [-1, 1]
     */
    public static float cosineSimilarity(List<Float> feature1, List<Float> feature2) {
        if (feature1 == null || feature2 == null || feature1.size() != feature2.size() || feature1.isEmpty()) {
            return -1f;
        }
        double dot = 0, normA = 0, normB = 0;
        for (int i = 0; i < feature1.size(); i++) {
            double a = feature1.get(i), b = feature2.get(i);
            dot += a * b;
            normA += a * a;
            normB += b * b;
        }
        if (normA == 0 || normB == 0) return 0.0f;
        return (float) (dot / (Math.sqrt(normA) * Math.sqrt(normB)));
    }

    /**
     * Unit-length copy of {@code feature}, or null when it is empty, zero or not finite. Searching
     * with a pre-normalised probe turns each comparison into one dot product and one norm.
     */
    public static float[] normalizedCopy(List<Float> feature) {
        if (feature == null || feature.isEmpty()) return null;
        float[] out = new float[feature.size()];
        double norm = 0;
        for (int i = 0; i < out.length; i++) {
            Float value = feature.get(i);
            if (value == null || !Float.isFinite(value)) return null;
            out[i] = value;
            norm += (double) value * value;
        }
        if (!(norm > 0) || !Double.isFinite(norm)) return null;
        float scale = (float) (1.0 / Math.sqrt(norm));
        for (int i = 0; i < out.length; i++) out[i] *= scale;
        return out;
    }

    /**
     * Cosine similarity of a unit-length probe against a raw stored vector, -1 when the
     * dimensions differ and 0 when the stored vector is zero (same conventions as above).
     */
    public static float cosineToUnit(float[] unitProbe, float[] stored) {
        if (stored == null || stored.length != unitProbe.length || stored.length == 0) return -1f;
        double dot = 0, norm = 0;
        for (int i = 0; i < stored.length; i++) {
            double b = stored[i];
            dot += unitProbe[i] * b;
            norm += b * b;
        }
        if (norm == 0) return 0.0f;
        return (float) (dot / Math.sqrt(norm));
    }

    /** Scales {@code v} to unit length; false (v untouched) when it is empty, zero or not finite. */
    public static boolean normalizeInPlace(float[] v) {
        if (v == null || v.length == 0) return false;
        double norm = 0;
        for (float x : v) {
            if (!Float.isFinite(x)) return false;
            norm += (double) x * x;
        }
        if (!(norm > 0) || !Double.isFinite(norm)) return false;
        float scale = (float) (1.0 / Math.sqrt(norm));
        for (int i = 0; i < v.length; i++) v[i] *= scale;
        return true;
    }

    /**
     * Dot product of {@code probe} with the row of {@code matrix} starting at {@code offset}. Four
     * independent accumulators let the JIT keep several multiply-adds in flight.
     */
    public static float dot(float[] probe, float[] matrix, int offset) {
        int n = probe.length, i = 0;
        float s0 = 0, s1 = 0, s2 = 0, s3 = 0;
        for (; i + 3 < n; i += 4) {
            s0 += probe[i] * matrix[offset + i];
            s1 += probe[i + 1] * matrix[offset + i + 1];
            s2 += probe[i + 2] * matrix[offset + i + 2];
            s3 += probe[i + 3] * matrix[offset + i + 3];
        }
        for (; i < n; i++) s0 += probe[i] * matrix[offset + i];
        return (s0 + s1) + (s2 + s3);
    }

    /**
     * 解析特征数据字符串
     *
     * @param featureDataJson JSON字符串 [0.1, 0.2, ...]
     * @return 特征列表
     */
    public static List<Float> parseFeatureData(String featureDataJson) {
        float[] values = parseFeatureArray(featureDataJson);
        if (values == null) return null;
        Float[] boxed = new Float[values.length];
        for (int i = 0; i < values.length; i++) boxed[i] = values[i];
        return Arrays.asList(boxed);
    }

    /**
     * Streams a JSON number array straight into a primitive array: no tree, no boxed Floats.
     * Returns null for anything that is not a flat array of numbers.
     */
    public static float[] parseFeatureArray(String featureDataJson) {
        if (featureDataJson == null || featureDataJson.isEmpty()) return null;
        try (JsonParser parser = JSON.createParser(featureDataJson)) {
            if (parser.nextToken() != JsonToken.START_ARRAY) return null;
            float[] values = new float[1024];
            int count = 0;
            JsonToken token;
            while ((token = parser.nextToken()) != JsonToken.END_ARRAY) {
                if (token != JsonToken.VALUE_NUMBER_FLOAT && token != JsonToken.VALUE_NUMBER_INT) return null;
                if (count == values.length) values = Arrays.copyOf(values, count * 2);
                values[count++] = parser.getFloatValue();
            }
            if (parser.nextToken() != null) return null;
            return count == values.length ? values : Arrays.copyOf(values, count);
        } catch (Exception e) {
            return null;
        }
    }
}
