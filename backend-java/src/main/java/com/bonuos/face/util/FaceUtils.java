package com.bonuos.face.util;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.util.List;

public class FaceUtils {

    private static final ObjectMapper objectMapper = new ObjectMapper();

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

        float dotProduct = 0.0f;
        float normA = 0.0f;
        float normB = 0.0f;

        for (int i = 0; i < feature1.size(); i++) {
            dotProduct += feature1.get(i) * feature2.get(i);
            normA += Math.pow(feature1.get(i), 2);
            normB += Math.pow(feature2.get(i), 2);
        }

        if (normA == 0 || normB == 0) {
            return 0.0f;
        }

        return (float) (dotProduct / (Math.sqrt(normA) * Math.sqrt(normB)));
    }

    /**
     * 解析特征数据字符串
     *
     * @param featureDataJson JSON字符串 [0.1, 0.2, ...]
     * @return 特征列表
     */
    public static List<Float> parseFeatureData(String featureDataJson) {
        if (featureDataJson == null || featureDataJson.isEmpty()) {
            return null;
        }
        try {
            return objectMapper.readValue(featureDataJson, new TypeReference<List<Float>>() {
            });
        } catch (Exception e) {
            return null;
        }
    }
}
