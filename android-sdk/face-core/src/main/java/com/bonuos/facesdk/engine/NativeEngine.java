package com.bonuos.facesdk.engine;

import android.content.res.AssetManager;
import android.graphics.Bitmap;

/**
 * Our own build of the recognizer_v1_1024 pipeline (libfaceengine.so, source in
 * src/main/cpp/engine): same ONNX models and the same pre/post-processing as the enrolment
 * service, without the rotation classifier, FSANet and per-call detection the prebuilt library ran.
 * Methods are synchronized; one extraction runs at a time.
 */
public final class NativeEngine implements AutoCloseable {
    static {
        System.loadLibrary("faceengine");
    }

    public enum LandmarkMode {
        /** Bit-compatible with the enrolment service: landmarks truncated to whole 112-crop units. */
        TRUNCATED,
        /** Fractional landmark positions kept. */
        SUBPIXEL,
        /** Sub-pixel, averaged over five slightly jittered boxes (~4 ms extra). */
        TTA
    }

    public static final String RECOGNIZER_FP32 = "face_recognizer.onnx";
    /**
     * Same weights, static int8 (convolutions only, first layer kept float), built by
     * tools/engine-eval/quantize.py. ~44% faster on a Dimensity 1200. LFW all-pairs, device-side
     * TTA against server fp32 enrolment: TAR@FAR=1e-5 88.7% vs 88.3% for the unchanged pipeline.
     */
    public static final String RECOGNIZER_INT8 = "face_recognizer_int8.onnx";

    /** InsightFace SCRFD-500M detector and MobileFaceNet / IResNet-50 recognizers (research licence). */
    public static final String INSIGHTFACE_DETECTOR = "insightface/det_500m.onnx";
    public static final String INSIGHTFACE_MBF = "insightface/w600k_mbf.onnx";
    public static final String INSIGHTFACE_R50 = "insightface/w600k_r50.onnx";
    /** w600k_r50 static int8 (tools/engine-eval/quantize_arcface.py): 160 ms -> 46 ms on a Dimensity 1200. */
    public static final String INSIGHTFACE_R50_INT8 = "insightface/w600k_r50_int8.onnx";

    private long handle;

    /**
     * recognizer_v1_1024 pipeline.
     *
     * @param recognizerAsset asset name of the recognizer weights
     * @param threads         intra-op threads for the recognizer (4 suits a 4+4 big.LITTLE phone)
     * @param withDetector    also load FaceBoxes, needed only for {@link #extract(Bitmap, LandmarkMode, float[])}
     */
    public NativeEngine(AssetManager assets, String recognizerAsset, int threads, boolean withDetector) {
        this(assets, 0, recognizerAsset, withDetector ? "faceboxesv2-640x640.onnx" : null, threads);
    }

    private NativeEngine(AssetManager assets, int kind, String recognizerAsset, String detectorAsset, int threads) {
        handle = nativeCreate(assets, kind, recognizerAsset, detectorAsset, threads);
        if (handle == 0) throw new IllegalStateException("Native face engine failed to load " + recognizerAsset);
    }

    /**
     * InsightFace pipeline: SCRFD places the five points (on a crop around a tracked face, or on
     * the whole still image), ArcFace template alignment, 512-d feature. Landmark modes are ignored.
     */
    public static NativeEngine insightFace(AssetManager assets, String recognizerAsset, int threads) {
        return new NativeEngine(assets, 1, recognizerAsset, INSIGHTFACE_DETECTOR, threads);
    }

    /**
     * Feature for a face whose box is already known, e.g. from the tracker.
     *
     * @param bgr          packed BGR pixels, {@code width * height * 3} bytes
     * @param face         face box in {@code bgr} coordinates
     * @param outLandmarks optional, receives the 5 points (x0,y0..x4,y4) in {@code bgr} coordinates
     * @return L2-normalised 1024-d feature, or null when extraction failed
     */
    public synchronized float[] extract(byte[] bgr, int width, int height, FaceRect face, LandmarkMode mode, float[] outLandmarks) {
        if (handle == 0) return null;
        return nativeExtractBgr(handle, bgr, width, height, face.left, face.top, face.right, face.bottom,
                mode.ordinal(), outLandmarks);
    }

    /**
     * InsightFace pipeline only: align on five points already known (crop coordinates, e.g. from
     * the tracker's SCRFD pass) and extract, skipping detection. Null on failure or for v1.
     */
    public synchronized float[] extractWithKeypoints(byte[] bgr, int width, int height, float[] keypoints) {
        if (handle == 0 || keypoints == null || keypoints.length < 10) return null;
        return nativeExtractKeypoints(handle, bgr, width, height, keypoints);
    }

    /** Still image (enrolment photo): FaceBoxes picks the highest-scoring face, like the server. */
    public synchronized float[] extract(Bitmap bitmap, LandmarkMode mode, float[] outLandmarks) {
        if (handle == 0 || bitmap == null || bitmap.getConfig() != Bitmap.Config.ARGB_8888) return null;
        return nativeExtractBitmap(handle, bitmap, mode.ordinal(), outLandmarks);
    }

    /**
     * Full-range BT.601 conversion of one upright-frame rectangle of a sensor-orientation NV21
     * frame into packed BGR. Same pixels as Nv21.cropToArgb, in native code.
     */
    public static boolean cropBgr(byte[] nv21, int width, int height, int rotationDegrees,
                                  int left, int top, int cropWidth, int cropHeight, byte[] out) {
        return nativeCropBgr(nv21, width, height, rotationDegrees, left, top, cropWidth, cropHeight, out);
    }

    @Override
    public synchronized void close() {
        if (handle != 0) {
            nativeRelease(handle);
            handle = 0;
        }
    }

    private static native long nativeCreate(AssetManager assets, int kind, String recognizerAsset, String detectorAsset, int threads);
    private static native void nativeRelease(long handle);
    private static native boolean nativeCropBgr(byte[] nv21, int width, int height, int rotation,
                                                int left, int top, int cropWidth, int cropHeight, byte[] out);
    private static native float[] nativeExtractBgr(long handle, byte[] bgr, int width, int height,
                                                   float x1, float y1, float x2, float y2, int mode, float[] outLandmarks);
    private static native float[] nativeExtractKeypoints(long handle, byte[] bgr, int width, int height, float[] keypoints);
    private static native float[] nativeExtractBitmap(long handle, Bitmap bitmap, int mode, float[] outLandmarks);
}
