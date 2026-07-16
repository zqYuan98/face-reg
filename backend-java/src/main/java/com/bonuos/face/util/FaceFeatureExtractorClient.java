package com.bonuos.face.util;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.io.ByteArrayResource;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Component;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;

@Component
public class FaceFeatureExtractorClient {

    @Value("${face.extractor.api.url:http://localhost:8000}")
    private String apiUrl;

    private final RestTemplate restTemplate;
    private final ObjectMapper objectMapper;

    public FaceFeatureExtractorClient() {
        this.restTemplate = new RestTemplate();
        this.objectMapper = new ObjectMapper();
    }

    /**
     * 调用 Python API 提取人脸特征
     *
     * @param imageFile 图片文件
     * @return 特征向量 (512维 float 数组)
     * @throws IOException 如果调用失败或解析错误
     */
    public List<Float> extractFeature(java.io.File imageFile) throws IOException {
        String extractUrl = apiUrl + "/api/extract_feature";

        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.MULTIPART_FORM_DATA);

        MultiValueMap<String, Object> body = new LinkedMultiValueMap<>();
        body.add("image", new org.springframework.core.io.FileSystemResource(imageFile));

        HttpEntity<MultiValueMap<String, Object>> requestEntity = new HttpEntity<>(body, headers);

        return sendRequest(extractUrl, requestEntity);
    }

    /**
     * 调用 Python API 提取人脸特征
     *
     * @param imageFile 图片文件 (MultipartFile)
     * @return 特征向量 (512维 float 数组)
     * @throws IOException 如果调用失败或解析错误
     */
    public List<Float> extractFeature(MultipartFile imageFile) throws IOException {
        String extractUrl = apiUrl + "/api/extract_feature";

        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.MULTIPART_FORM_DATA);

        MultiValueMap<String, Object> body = new LinkedMultiValueMap<>();
        body.add("image", new ByteArrayResource(imageFile.getBytes()) {
            @Override
            public String getFilename() {
                return imageFile.getOriginalFilename();
            }
        });

        HttpEntity<MultiValueMap<String, Object>> requestEntity = new HttpEntity<>(body, headers);

        return sendRequest(extractUrl, requestEntity);
    }

    private List<Float> sendRequest(String url, HttpEntity<MultiValueMap<String, Object>> requestEntity)
            throws IOException {
        try {
            ResponseEntity<String> response = restTemplate.postForEntity(url, requestEntity, String.class);
            if (response.getStatusCode().is2xxSuccessful() && response.getBody() != null) {
                JsonNode root = objectMapper.readTree(response.getBody());
                if (root.path("success").asBoolean()) {
                    JsonNode featureNode = root.path("feature");
                    List<Float> features = new ArrayList<>();
                    if (featureNode.isArray()) {
                        for (JsonNode node : featureNode) {
                            features.add((float) node.asDouble());
                        }
                    }
                    return features;
                } else {
                    throw new IOException("Feature extraction failed: " + root.path("message").asText());
                }
            } else {
                throw new IOException("API call failed with status: " + response.getStatusCode());
            }
        } catch (IOException e) {
            throw e;
        } catch (Exception e) {
            throw new IOException("Error calling Face Feature Extractor API", e);
        }
    }
}
