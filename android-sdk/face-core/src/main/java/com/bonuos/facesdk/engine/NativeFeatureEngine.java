package com.bonuos.facesdk.engine;

import android.os.SystemClock;
import android.util.Log;
import java.util.concurrent.Callable;

/**
 * Captures an independent BGR crop on the analysis thread; extraction runs on the feature worker.
 * Compatible native stages can share this read-only crop without keeping the reusable camera
 * buffer alive. Each stage still owns its model, landmark outputs and pose decision.
 */
public final class NativeFeatureEngine implements FaceSession.FeatureEngine {
    private static final String TAG = "FaceEngine";
    static final float KEYPOINT_CROP_SCALE = 1.6f;

    /** Native extraction treats BGR and supplied keypoints as read-only. */
    interface Extractor {
        float[] extract(byte[] bgr, int width, int height, FaceRect face,
                        NativeEngine.LandmarkMode mode, float[] outLandmarks);
        float[] extractWithKeypoints(byte[] bgr, int width, int height, float[] keypoints);
    }

    interface CropConverter {
        boolean convert(byte[] nv21, int width, int height, int rotation,
                        int left, int top, int cropWidth, int cropHeight, byte[] output);
    }

    private static final CropConverter NATIVE_CONVERTER = (nv21, width, height, rotation, left, top, cw, ch, out) ->
            NativeEngine.cropBgr(nv21, width, height, rotation, left, top, cw, ch, out);
    private final Extractor extractor;
    private final CropConverter converter;
    private final NativeEngine.LandmarkMode mode;
    private final float maxYaw, maxPitch;

    public NativeFeatureEngine(NativeEngine engine, NativeEngine.LandmarkMode mode, float maxYaw, float maxPitch) {
        this(new Extractor() {
            @Override public float[] extract(byte[] bgr, int width, int height, FaceRect face,
                                            NativeEngine.LandmarkMode mode, float[] points) {
                return engine.extract(bgr, width, height, face, mode, points);
            }
            @Override public float[] extractWithKeypoints(byte[] bgr, int width, int height, float[] points) {
                return engine.extractWithKeypoints(bgr, width, height, points);
            }
        }, NATIVE_CONVERTER, mode, maxYaw, maxPitch);
    }

    // Narrow test seam: exercises capture ownership and scheduling without loading a native model.
    NativeFeatureEngine(Extractor extractor, CropConverter converter, NativeEngine.LandmarkMode mode,
                        float maxYaw, float maxPitch) {
        this.extractor = extractor;
        this.converter = converter;
        this.mode = mode;
        this.maxYaw = maxYaw;
        this.maxPitch = maxPitch;
    }

    /** Exclusive ownership of the copied pixels; never exposed to the camera or external callers. */
    private static final class PreparedCapture {
        final byte[] bgr;
        final int width, height;
        final FaceRect localFace;
        final float[] localKeypoints;

        PreparedCapture(byte[] bgr, int width, int height, FaceRect localFace, float[] localKeypoints) {
            this.bgr = bgr;
            this.width = width;
            this.height = height;
            this.localFace = localFace;
            this.localKeypoints = localKeypoints;
        }
    }

    @Override
    public Callable<float[]> capture(CameraFrame frame, FaceRect face, float[] keypoints, FaceRect crop) {
        return task(prepare(frame, face, keypoints, crop));
    }

    @Override
    public FaceSession.CapturedFeatures capturePair(CameraFrame frame, FaceRect face, float[] keypoints,
                                                    FaceRect crop, FaceSession.FeatureEngine verifier) {
        if (verifier == null) {
            PreparedCapture prepared = prepare(frame, face, keypoints, crop);
            return new FaceSession.CapturedFeatures(task(prepared), null, 1, prepared.bgr.length);
        }
        if (!(verifier instanceof NativeFeatureEngine)) {
            return FaceSession.FeatureEngine.super.capturePair(frame, face, keypoints, crop, verifier);
        }
        NativeFeatureEngine second = (NativeFeatureEngine) verifier;
        // The two stages must have exactly the same pixel conversion and crop contract. Different
        // models and pose limits are fine: those belong to the independent tasks, not the capture.
        if (converter != second.converter) {
            return FaceSession.FeatureEngine.super.capturePair(frame, face, keypoints, crop, verifier);
        }
        PreparedCapture prepared = prepare(frame, face, keypoints, crop);
        return new FaceSession.CapturedFeatures(task(prepared), second.task(prepared), 1, prepared.bgr.length);
    }

    private PreparedCapture prepare(CameraFrame frame, FaceRect face, float[] keypoints, FaceRect crop) {
        boolean useKeypoints = keypoints != null && keypoints.length >= 10;
        if (useKeypoints) crop = face.scaledClipped(KEYPOINT_CROP_SCALE, frame.uprightWidth(), frame.uprightHeight());
        int left = Math.max(0, (int) Math.floor(crop.left)), top = Math.max(0, (int) Math.floor(crop.top));
        int width = Math.min(frame.uprightWidth(), (int) Math.ceil(crop.right)) - left;
        int height = Math.min(frame.uprightHeight(), (int) Math.ceil(crop.bottom)) - top;
        long bytes = (long) width * height * 3;
        if (width <= 0 || height <= 0 || bytes > Integer.MAX_VALUE) throw new IllegalArgumentException("Invalid crop size");
        byte[] bgr = new byte[(int) bytes];
        if (!converter.convert(frame.nv21, frame.width, frame.height, frame.rotationDegrees,
                left, top, width, height, bgr)) throw new IllegalArgumentException("Crop outside frame");
        FaceRect local = new FaceRect(face.left - left, face.top - top, face.right - left, face.bottom - top);
        float[] localKps = useKeypoints ? new float[10] : null;
        if (useKeypoints) {
            for (int k = 0; k < 5; k++) {
                localKps[2 * k] = keypoints[2 * k] - left;
                localKps[2 * k + 1] = keypoints[2 * k + 1] - top;
            }
        }
        return new PreparedCapture(bgr, width, height, local, localKps);
    }

    private Callable<float[]> task(PreparedCapture prepared) {
        return () -> {
            boolean useKeypoints = prepared.localKeypoints != null;
            // Landmark outputs may be mutated by extraction; each model task keeps its own copy.
            float[] points = useKeypoints ? prepared.localKeypoints.clone() : new float[10];
            long started = SystemClock.elapsedRealtimeNanos();
            float[] feature = useKeypoints
                    ? extractor.extractWithKeypoints(prepared.bgr, prepared.width, prepared.height, points)
                    : extractor.extract(prepared.bgr, prepared.width, prepared.height, prepared.localFace, mode, points);
            double ms = (SystemClock.elapsedRealtimeNanos() - started) / 1e6;
            if (feature == null) {
                Log.d(TAG, String.format("extract %.1f ms crop %dx%d: no feature", ms, prepared.width, prepared.height));
                return null;
            }
            Pose5 pose = Pose5.estimate(points);
            boolean frontal = Math.abs(pose.yaw) <= maxYaw && Math.abs(pose.pitch) <= maxPitch;
            Log.d(TAG, String.format("extract %.1f ms crop %dx%d face %.0fpx yaw %.1f pitch %.1f%s%s", ms,
                    prepared.width, prepared.height, prepared.localFace.width(), pose.yaw, pose.pitch,
                    useKeypoints ? " (tracker keypoints)" : "", frontal ? "" : " -> rejected (pose)"));
            return frontal ? feature : null;
        };
    }
}
