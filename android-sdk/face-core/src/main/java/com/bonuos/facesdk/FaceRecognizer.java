package com.bonuos.facesdk;

import android.content.Context;
import android.graphics.Bitmap;
import android.util.Log;
import com.facesdk.wrapper.FaceSDKWrapper;
import java.util.List;

public class FaceRecognizer {
    private static final String TAG = "FaceRecognizer";
    private FaceSDKWrapper sdkWrapper;
    private FaceRepository repository;
    private List<FaceRepository.FaceUser> cachedUsers; // 内存缓存
    private float threshold = 0.65f; // 识别阈值（降低以提高召回率）

    public FaceRecognizer(Context context) {
        sdkWrapper = new FaceSDKWrapper();
        sdkWrapper.init(context);
        repository = new FaceRepository(context);
        // 启动时加载所有用户到内存
        cachedUsers = repository.getAllUsers();
        int validCount = 0;
        for (FaceRepository.FaceUser user : cachedUsers) {
            if (user.feature != null && user.feature.length > 0) {
                validCount++;
            }
        }
        Log.i(TAG, "Loaded " + cachedUsers.size() + " users into memory cache. Valid features: " + validCount);
        Log.i(TAG, "Loaded " + cachedUsers.size() + " users into memory cache. Valid features: " + validCount);
    }

    public synchronized void reloadUsers() {
        if (repository != null) {
            cachedUsers = repository.getAllUsers();
            int validCount = 0;
            for (FaceRepository.FaceUser user : cachedUsers) {
                if (user.feature != null && user.feature.length > 0) {
                    validCount++;
                }
            }
            Log.i(TAG, "Reloaded " + cachedUsers.size() + " users into memory cache. Valid features: " + validCount);
        }
    }

    public float[] extractFeature(Bitmap bitmap) {
        return sdkWrapper.extractFeature(bitmap);
    }

    public RecognitionResult recognize(Bitmap bitmap) {
        Log.d(TAG, "recognize() called");
        // 1. 提取特征
        float[] feature = sdkWrapper.extractFeature(bitmap);
        if (feature == null) {
            Log.e(TAG, "extractFeature returned null");
            return new RecognitionResult(false, "未检测到人脸或质量不合格");
        }

        // 2. 在内存缓存中搜索
        if (cachedUsers == null || cachedUsers.isEmpty()) {
            return new RecognitionResult(false, "本地人员库为空");
        }

        // 打印提取的特征前5位
        if (feature.length > 5) {
            Log.d(TAG, String.format("Extracted Feature[0-4]: %.4f, %.4f, %.4f, %.4f, %.4f",
                    feature[0], feature[1], feature[2], feature[3], feature[4]));
        }

        FaceRepository.FaceUser bestMatchUser = null;
        float maxSimilarity = 0f;

        for (FaceRepository.FaceUser user : cachedUsers) {
            if (user.feature == null) {
                Log.w(TAG, "User " + user.name + " has null feature");
                continue;
            }

            // 打印用户特征前5位（仅打印第一个用户以避免刷屏）
            if (user == cachedUsers.get(0) && user.feature.length > 5) {
                Log.d(TAG, String.format("User[%s] Feature[0-4]: %.4f, %.4f, %.4f, %.4f, %.4f",
                        user.name, user.feature[0], user.feature[1], user.feature[2], user.feature[3],
                        user.feature[4]));
            }

            float similarity = sdkWrapper.compare(feature, user.feature);
            Log.d(TAG, String.format("Comparing with user[%s]: similarity = %.4f", user.name, similarity));

            if (similarity > maxSimilarity) {
                maxSimilarity = similarity;
                bestMatchUser = user;
            }
        }

        // 3. 判断是否达到阈值
        if (maxSimilarity >= threshold && bestMatchUser != null) {
            return new RecognitionResult(true, bestMatchUser, maxSimilarity);
        } else {
            return new RecognitionResult(false, "未识别到已知人员 (相似度: " + maxSimilarity + ")");
        }
    }

    public void setThreshold(float threshold) {
        this.threshold = threshold;
    }

    public void release() {
        if (sdkWrapper != null) {
            sdkWrapper.release();
        }
        if (repository != null) {
            repository.close();
        }
    }

}
