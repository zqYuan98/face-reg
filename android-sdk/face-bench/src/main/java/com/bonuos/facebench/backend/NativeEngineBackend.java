package com.bonuos.facebench.backend;

import android.content.Context;
import android.graphics.Bitmap;
import com.bonuos.facebench.clip.ClipFile;
import com.bonuos.facesdk.engine.CameraFrame;
import com.bonuos.facesdk.engine.Detection;
import com.bonuos.facesdk.engine.FaceRect;
import com.bonuos.facesdk.engine.Gallery;
import com.bonuos.facesdk.engine.MvEngines;
import com.bonuos.facesdk.engine.NativeEngine;
import com.bonuos.facesdk.engine.SessionConfig;
import java.util.List;

/**
 * Our own recognizer_v1_1024 engine (libfaceengine.so): tracker box -> averaged landmarks ->
 * Seeta alignment -> recognizer, no rotation classifier, detection or FSANet per extraction.
 * Enrolment photos use the server's exact steps (FaceBoxes, truncated landmarks), so scores are
 * device-probe versus server-style enrolment, as in the field.
 */
public final class NativeEngineBackend implements FaceBackend {
    private final MvEngines engines;
    private final NativeEngine engine;
    private final boolean int8;
    private final float cropScale = new SessionConfig().featureCropScale;
    private final float livenessThreshold = new SessionConfig().livenessThreshold;

    public NativeEngineBackend(Context context, boolean int8) {
        this.int8 = int8;
        engines = new MvEngines(context);
        try {
            engine = new NativeEngine(context.getAssets(), int8 ? NativeEngine.RECOGNIZER_INT8 : NativeEngine.RECOGNIZER_FP32, 4, true);
        } catch (RuntimeException error) {
            engines.close();
            throw error;
        }
    }

    @Override public String name() { return int8 ? BackendRegistry.ENGINE_NATIVE_INT8 : BackendRegistry.ENGINE_NATIVE; }

    @Override public String describe() {
        return "libfaceengine recognizer_v1_1024 (" + (int8 ? "int8" : "fp32") + " recognizer, 4 threads)"
                + "; probe: tracker box, 5-box averaged landmarks, crop x" + cropScale
                + "; enrolment: FaceBoxes + truncated landmarks (server steps); score: cosine"
                + "; liveness as engine_crop, threshold " + livenessThreshold;
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
        Detection main = faces.isEmpty() ? null : faces.get(0);
        if (main == null) {
            outcome.livenessStatus = FrameOutcome.LIVE_UNKNOWN;
            if (runFeature) {
                outcome.featureAttempted = true;
                outcome.note = "no face from tracker detector";
            }
            return outcome;
        }
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
        if (runFeature) {
            outcome.featureAttempted = true;
            timer.lapMs();
            FaceRect crop = r.scaledClipped(cropScale, frame.width, frame.height);
            int left = (int) Math.floor(crop.left), top = (int) Math.floor(crop.top);
            int width = Math.min(frame.width, (int) Math.ceil(crop.right)) - left;
            int height = Math.min(frame.height, (int) Math.ceil(crop.bottom)) - top;
            byte[] bgr = new byte[width * height * 3];
            NativeEngine.cropBgr(frame.nv21, frame.width, frame.height, 0, left, top, width, height, bgr);
            outcome.preprocessMs = timer.lapMs();
            FaceRect local = new FaceRect(r.left - left, r.top - top, r.right - left, r.bottom - top);
            outcome.feature = engine.extract(bgr, width, height, local, NativeEngine.LandmarkMode.TTA, null);
            outcome.featureMs = timer.lapMs();
            if (outcome.feature == null) outcome.note = "no usable face for recognition";
        }
        return outcome;
    }

    @Override public Object enroll(Bitmap photo) {
        Bitmap argb = photo.getConfig() == Bitmap.Config.ARGB_8888 ? photo : photo.copy(Bitmap.Config.ARGB_8888, false);
        return engine.extract(argb, NativeEngine.LandmarkMode.TRUNCATED, null);
    }

    @Override public float compare(Object probe, Object reference) {
        return Gallery.cosine((float[]) probe, (float[]) reference);
    }

    @Override public void close() {
        engine.close();
        engines.close();
    }
}
