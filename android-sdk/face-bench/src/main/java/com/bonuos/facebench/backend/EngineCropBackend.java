package com.bonuos.facebench.backend;

import android.content.Context;
import android.graphics.Bitmap;
import com.bonuos.facebench.clip.ClipFile;
import com.bonuos.facesdk.engine.CameraFrame;
import com.bonuos.facesdk.engine.Detection;
import com.bonuos.facesdk.engine.FaceRect;
import com.bonuos.facesdk.engine.MvEngines;
import com.bonuos.facesdk.engine.SessionConfig;
import com.bonuos.facesdk.image.Nv21;
import com.facesdk.wrapper.FaceSDKWrapper;
import java.util.List;

/**
 * Same models as {@code legacy_fixed}, fed the way the new {@link com.bonuos.facesdk.engine.FaceSession}
 * feeds them: one native conversion shared by detection and liveness, and the recognizer gets a
 * crop around the detected face instead of the whole frame. Per-frame replay only measures the
 * per-stage effects; "one extraction per person" needs the live mode.
 */
public final class EngineCropBackend implements FaceBackend {
    private final MvEngines engines;
    private final FaceSDKWrapper recognizer = new FaceSDKWrapper();
    private final float cropScale = new SessionConfig().featureCropScale;
    private final float livenessThreshold = new SessionConfig().livenessThreshold;

    public EngineCropBackend(Context context) {
        engines = new MvEngines(context);
        if (!recognizer.init(context)) {
            engines.close();
            throw new IllegalStateException("Recognition library failed to initialise");
        }
    }

    @Override public String name() { return BackendRegistry.ENGINE_CROP; }

    @Override public String describe() {
        return "upright NV21, single native BGR conversion shared by detection and liveness"
                + "; recognition on a face crop x" + cropScale + " (whole frame when no face)"
                + "; same models as legacy_fixed; liveness threshold " + livenessThreshold;
    }

    @Override public void beginClip(int sensorRotationDegrees) { }

    @Override
    public FrameOutcome process(ClipFile.Frame frame, boolean runLiveness, boolean runFeature) {
        FrameOutcome outcome = new FrameOutcome();
        CameraFrame camera = new CameraFrame(frame.nv21, frame.width, frame.height, 0, frame.timestampNs / 1_000_000L);
        Timer timer = Timer.start();
        List<Detection> faces = engines.detect(camera);
        outcome.detectMs = timer.lapMs();
        outcome.faceCount = faces.size();
        Detection main = faces.isEmpty() ? null : faces.get(0); // largest first
        if (main == null) {
            outcome.livenessStatus = FrameOutcome.LIVE_UNKNOWN;
        } else {
            FaceRect r = main.rect;
            outcome.box = new int[]{Math.round(r.left), Math.round(r.top), Math.round(r.right) - 1, Math.round(r.bottom) - 1};
            if (runLiveness) {
                timer.lapMs();
                float score = engines.score(camera, r);
                outcome.livenessMs = timer.lapMs();
                outcome.livenessScore = score;
                outcome.livenessStatus = Float.isNaN(score) ? FrameOutcome.LIVE_ERROR
                        : score > livenessThreshold ? FrameOutcome.LIVE_ALIVE : FrameOutcome.LIVE_NOT_ALIVE;
            }
        }
        if (runFeature) {
            outcome.featureAttempted = true;
            timer.lapMs();
            FaceRect crop = main == null ? new FaceRect(0, 0, frame.width, frame.height)
                    : main.rect.scaledClipped(cropScale, frame.width, frame.height);
            int left = (int) Math.floor(crop.left), top = (int) Math.floor(crop.top);
            int width = Math.min(frame.width, (int) Math.ceil(crop.right)) - left;
            int height = Math.min(frame.height, (int) Math.ceil(crop.bottom)) - top;
            int[] pixels = new int[width * height];
            Nv21.cropToArgb(frame.nv21, frame.width, frame.height, 0, left, top, width, height, pixels);
            Bitmap bitmap = Bitmap.createBitmap(pixels, width, height, Bitmap.Config.ARGB_8888);
            outcome.preprocessMs = timer.lapMs();
            outcome.feature = recognizer.extractFeature(bitmap);
            outcome.featureMs = timer.lapMs();
            bitmap.recycle();
            if (outcome.feature == null) outcome.note = "no usable face for recognition";
            else if (main == null) outcome.note = "feature from whole frame";
        }
        return outcome;
    }

    @Override public Object enroll(Bitmap photo) { return recognizer.extractFeature(photo); }

    @Override public float compare(Object probe, Object reference) {
        return recognizer.compare((float[]) probe, (float[]) reference);
    }

    @Override public void close() {
        recognizer.release();
        engines.close();
    }
}
