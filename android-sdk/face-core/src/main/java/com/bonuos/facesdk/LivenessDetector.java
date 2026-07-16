package com.bonuos.facesdk;

import android.content.Context;
import android.graphics.Bitmap;
import android.util.Log;

/**
 * 活体检测类（集成 Silent-Face-Anti-Spoofing）
 */
public class LivenessDetector {
    private static final String TAG = "LivenessDetector";
    private boolean isInitialized = false;

    public interface LivenessCallback {
        void onResult(boolean isLive, float score);
    }

    private com.mv.engine.Live liveEngine;
    private com.mv.engine.FaceDetector faceDetector;

    public void init(Context context) {
        try {
            Log.i(TAG, "初始化活体检测引擎...");
            liveEngine = new com.mv.engine.Live();
            faceDetector = new com.mv.engine.FaceDetector();

            int ret = liveEngine.loadModel(context.getAssets());
            if (ret == 0) {
                Log.i(TAG, "Live 模型加载成功");
            } else {
                Log.e(TAG, "Live 模型加载失败: " + ret);
            }

            ret = faceDetector.loadModel(context.getAssets());
            if (ret == 0) {
                Log.i(TAG, "FaceDetector 模型加载成功");
            } else {
                Log.e(TAG, "FaceDetector 模型加载失败: " + ret);
            }

            isInitialized = true;
        } catch (Exception e) {
            Log.e(TAG, "活体检测初始化失败", e);
        }
    }

    /**
     * 执行活体检测
     * 
     * @param bitmap   待检测的图片
     * @param callback 检测结果回调
     */
    /**
     * 执行活体检测 (同步方法，建议在后台线程调用)
     * 
     * @param bitmap 待检测的图片
     * @return 活体检测结果
     */
    public LivenessResult detect(Bitmap bitmap) {
        if (!isInitialized || bitmap == null) {
            return new LivenessResult(0.0f, null);
        }

        try {
            // 1. 检测人脸框
            java.util.List<com.mv.engine.FaceBox> boxes = faceDetector.detect(bitmap);
            if (boxes == null || boxes.isEmpty()) {
                Log.w(TAG, "未检测到人脸");
                return new LivenessResult(0.0f, null);
            }

            com.mv.engine.FaceBox box = boxes.get(0);

            // 2. 转换 Bitmap 为 NV21 (YUV420SP)
            byte[] yuv = getNV21(bitmap.getWidth(), bitmap.getHeight(), bitmap);

            // 3. 活体检测
            float score = liveEngine.detect(
                    yuv,
                    bitmap.getWidth(),
                    bitmap.getHeight(),
                    0, // orientation
                    box);
            
            return new LivenessResult(score, box);

        } catch (Exception e) {
            Log.e(TAG, "活体检测出错", e);
            return new LivenessResult(0.0f, null);
        }
    }

    public void release() {
        if (liveEngine != null) {
            liveEngine.destroy();
            liveEngine = null;
        }
        if (faceDetector != null) {
            faceDetector.destroy();
            faceDetector = null;
        }
        isInitialized = false;
        Log.i(TAG, "活体检测资源已释放");
    }

    // Bitmap 转 NV21 辅助方法
    private byte[] getNV21(int inputWidth, int inputHeight, Bitmap scaled) {
        int[] argb = new int[inputWidth * inputHeight];
        scaled.getPixels(argb, 0, inputWidth, 0, 0, inputWidth, inputHeight);
        byte[] yuv = new byte[inputWidth * inputHeight * 3 / 2];
        encodeYUV420SP(yuv, argb, inputWidth, inputHeight);
        return yuv;
    }

    private void encodeYUV420SP(byte[] yuv420sp, int[] argb, int width, int height) {
        final int frameSize = width * height;
        int yIndex = 0;
        int uvIndex = frameSize;
        int a, R, G, B, Y, U, V;
        int index = 0;
        for (int j = 0; j < height; j++) {
            for (int i = 0; i < width; i++) {
                a = (argb[index] & 0xff000000) >> 24; // a is not used obviously
                R = (argb[index] & 0xff0000) >> 16;
                G = (argb[index] & 0xff00) >> 8;
                B = (argb[index] & 0xff) >> 0;

                // well known RGB to YUV algorithm
                Y = ((66 * R + 129 * G + 25 * B + 128) >> 8) + 16;
                U = ((-38 * R - 74 * G + 112 * B + 128) >> 8) + 128;
                V = ((112 * R - 94 * G - 18 * B + 128) >> 8) + 128;

                // NV21 has a plane of Y and interleaved planes of VU each sampled by a factor
                // of 2
                // meaning for every 4 Y pixels there are 1 V and 1 U. Note the sampling is
                // every other
                // pixel AND every other scanline.
                yuv420sp[yIndex++] = (byte) ((Y < 0) ? 0 : ((Y > 255) ? 255 : Y));
                if (j % 2 == 0 && index % 2 == 0) {
                    yuv420sp[uvIndex++] = (byte) ((V < 0) ? 0 : ((V > 255) ? 255 : V));
                    yuv420sp[uvIndex++] = (byte) ((U < 0) ? 0 : ((U > 255) ? 255 : U));
                }
                index++;
            }
        }
    }
}
