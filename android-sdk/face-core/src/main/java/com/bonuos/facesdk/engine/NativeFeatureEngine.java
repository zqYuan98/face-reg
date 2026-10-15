package com.bonuos.facesdk.engine;

import android.os.SystemClock;
import android.util.Log;
import java.util.concurrent.Callable;

/**
 * {@link NativeEngine} behind {@link FaceSession.FeatureEngine}. The analysis thread converts
 * only the crop around the tracked face (native, full-range); the worker thread runs landmarks on
 * the tracker's box, aligns and extracts. A non-frontal result is dropped before the gallery
 * search, using the pose estimated from the same five points.
 */
public final class NativeFeatureEngine implements FaceSession.FeatureEngine {
    private static final String TAG = "FaceEngine";
    private final NativeEngine engine;
    private final NativeEngine.LandmarkMode mode;
    private final float maxYaw, maxPitch;

    /** @param maxYaw,maxPitch degrees; frames beyond either return no feature */
    public NativeFeatureEngine(NativeEngine engine, NativeEngine.LandmarkMode mode, float maxYaw, float maxPitch) {
        this.engine = engine;
        this.mode = mode;
        this.maxYaw = maxYaw;
        this.maxPitch = maxPitch;
    }

    /** Crop around the tracked face when keypoints are known: covers the 112 ArcFace template with margin. */
    static final float KEYPOINT_CROP_SCALE = 1.6f;

    @Override
    public Callable<float[]> capture(CameraFrame frame, FaceRect face, float[] keypoints, FaceRect crop) {
        final boolean useKeypoints = keypoints != null && keypoints.length >= 10;
        if (useKeypoints) crop = face.scaledClipped(KEYPOINT_CROP_SCALE, frame.uprightWidth(), frame.uprightHeight());
        final int left = Math.max(0, (int) Math.floor(crop.left)), top = Math.max(0, (int) Math.floor(crop.top));
        final int width = Math.min(frame.uprightWidth(), (int) Math.ceil(crop.right)) - left;
        final int height = Math.min(frame.uprightHeight(), (int) Math.ceil(crop.bottom)) - top;
        if (width <= 0 || height <= 0) throw new IllegalArgumentException("Empty crop");
        final byte[] bgr = new byte[width * height * 3];
        if (!NativeEngine.cropBgr(frame.nv21, frame.width, frame.height, frame.rotationDegrees, left, top, width, height, bgr))
            throw new IllegalArgumentException("Crop outside frame");
        final FaceRect local = new FaceRect(face.left - left, face.top - top, face.right - left, face.bottom - top);
        final float[] localKps = new float[10];
        if (useKeypoints) {
            for (int k = 0; k < 5; k++) {
                localKps[2 * k] = keypoints[2 * k] - left;
                localKps[2 * k + 1] = keypoints[2 * k + 1] - top;
            }
        }
        return () -> {
            float[] points = useKeypoints ? localKps : new float[10];
            long started = SystemClock.elapsedRealtimeNanos();
            float[] feature = useKeypoints
                    ? engine.extractWithKeypoints(bgr, width, height, localKps)
                    : engine.extract(bgr, width, height, local, mode, points);
            double ms = (SystemClock.elapsedRealtimeNanos() - started) / 1e6;
            if (feature == null) {
                Log.d(TAG, String.format("extract %.1f ms crop %dx%d: no feature", ms, width, height));
                return null;
            }
            Pose5 pose = Pose5.estimate(points);
            boolean frontal = Math.abs(pose.yaw) <= maxYaw && Math.abs(pose.pitch) <= maxPitch;
            Log.d(TAG, String.format("extract %.1f ms crop %dx%d face %.0fpx yaw %.1f pitch %.1f%s%s", ms, width, height,
                    local.width(), pose.yaw, pose.pitch, useKeypoints ? " (tracker keypoints)" : "",
                    frontal ? "" : " -> rejected (pose)"));
            return frontal ? feature : null;
        };
    }
}
