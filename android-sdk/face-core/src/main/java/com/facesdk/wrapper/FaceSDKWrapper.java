package com.facesdk.wrapper;

import android.content.Context;
import android.content.res.AssetManager;
import android.graphics.Bitmap;
import android.util.Log;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;

public class FaceSDKWrapper {

    private static final String TAG = "FaceSDKWrapper";

    static {
        try {
            System.loadLibrary("c++_shared");
            System.loadLibrary("onnxruntime");
            System.loadLibrary("opencv_java4");
            System.loadLibrary("face_sdk_jni");
        } catch (UnsatisfiedLinkError e) {
            Log.e(TAG, "!!! 致命错误: 无法加载一个或多个 Native 库 !!!", e);
            throw new RuntimeException("Failed to load native libraries", e);
        }
    }

    private native long nativeInit(String modelDir);

    private native float[] nativeExtractFeature(Bitmap bitmap);

    private native float nativeCompare(float[] feat1, float[] feat2);

    private native void nativeRelease();

    private long nativeHandle = 0;

    public boolean init(Context context) {
        if (nativeHandle != 0) {
            Log.w(TAG, "SDK already initialized.");
            return true;
        }

        String modelPath = copyModelsFromAssets(context);
        if (modelPath == null) {
            Log.e(TAG, "Failed to copy models from assets.");
            return false;
        }

        try {
            this.nativeHandle = nativeInit(modelPath);
            if (this.nativeHandle == 0) {
                Log.e(TAG, "JNI nativeInit() returned 0 (Init failed).");
                return false;
            }
        } catch (Exception e) {
            Log.e(TAG, "Exception during nativeInit()", e);
            return false;
        }

        Log.i(TAG, "SDK Initialized successfully. Handle: " + this.nativeHandle);
        return true;
    }

    public float[] extractFeature(Bitmap bitmap) {
        if (nativeHandle == 0) {
            Log.e(TAG, "SDK not initialized. Call init() first.");
            return null;
        }
        if (bitmap == null) {
            Log.e(TAG, "Input bitmap is null.");
            return null;
        }

        try {
            return nativeExtractFeature(bitmap);
        } catch (Exception e) {
            Log.e(TAG, "Exception during nativeExtractFeature()", e);
            return null;
        }
    }

    public float compare(float[] feat1, float[] feat2) {
        if (nativeHandle == 0) {
            Log.e(TAG, "SDK not initialized.");
            return -2.0f;
        }
        if (feat1 == null || feat2 == null) {
            Log.e(TAG, "Feature vectors are null.");
            return -2.0f;
        }
        if (feat1.length != feat2.length) {
            Log.e(TAG, "Feature vector dimensions mismatch: " + feat1.length + " vs " + feat2.length);
            return -2.0f;
        }
        // 支持 512 或 1024 维特征
        if (feat1.length != 512 && feat1.length != 1024) {
            Log.e(TAG, "Invalid feature dimension: " + feat1.length + " (expected 512 or 1024)");
            return -2.0f;
        }

        try {
            return nativeCompare(feat1, feat2);
        } catch (Exception e) {
            Log.e(TAG, "Exception during nativeCompare()", e);
            return -2.0f;
        }
    }

    public void release() {
        if (nativeHandle != 0) {
            nativeRelease();
            Log.i(TAG, "SDK Released. Handle: " + nativeHandle);
            nativeHandle = 0;
        }
    }

    private String copyModelsFromAssets(Context context) {
        final String[] modelFiles = {
                "faceboxesv2-640x640.onnx",
                "face_landmarker_pts5_net1.onnx",
                "face_landmarker_pts5_net2.onnx",
                "face_recognizer.onnx",
                "model_gray_mobilenetv2_rotcls.onnx",
                "fsanet-var.onnx",
                "fsanet-conv.onnx"
        };

        File modelDir = new File(context.getFilesDir(), "models");
        if (!modelDir.exists()) {
            if (!modelDir.mkdirs()) {
                Log.e(TAG, "Failed to create directory: " + modelDir.getAbsolutePath());
                return null;
            }
        }

        AssetManager assetManager = context.getAssets();

        for (String filename : modelFiles) {
            File outFile = new File(modelDir, filename);

            if (outFile.exists()) {
                Log.i(TAG, "Model exists, skipping: " + filename);
                continue;
            }

            Log.i(TAG, "Copying model: " + filename);
            try (InputStream is = assetManager.open(filename);
                    OutputStream os = new FileOutputStream(outFile)) {

                byte[] buffer = new byte[1024 * 4];
                int bytesRead;
                while ((bytesRead = is.read(buffer)) != -1) {
                    os.write(buffer, 0, bytesRead);
                }
            } catch (IOException e) {
                Log.e(TAG, "Failed to copy model: " + filename, e);
                return null;
            }
        }

        Log.i(TAG, "All models copied successfully to: " + modelDir.getAbsolutePath());
        return modelDir.getAbsolutePath();
    }
}
