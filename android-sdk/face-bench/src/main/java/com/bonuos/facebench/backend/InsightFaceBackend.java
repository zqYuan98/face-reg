package com.bonuos.facebench.backend;

import android.content.Context;
import android.graphics.Bitmap;
import com.bonuos.facebench.clip.ClipFile;
import com.bonuos.facesdk.engine.CameraFrame;
import com.bonuos.facesdk.engine.Detection;
import com.bonuos.facesdk.engine.FaceRect;
import com.bonuos.facesdk.engine.Gallery;
import com.bonuos.facesdk.engine.NativeEngine;
import com.bonuos.facesdk.engine.ScrfdEngines;
import com.bonuos.facesdk.engine.SessionConfig;
import java.util.List;

/**
 * The optimised device pipeline: SCRFD-500M detection on every frame (long side 480), silent
 * liveness on the same converted frame, and an InsightFace ArcFace feature from a crop around the
 * face (SCRFD re-run on the crop for precise points). Enrolment photos go through SCRFD at 640 and
 * the same recognizer. Research-licensed weights; evaluation only.
 */
public final class InsightFaceBackend implements FaceBackend {
    private final String name;
    private final String recognizer;
    private final ScrfdEngines frames;
    private final NativeEngine engine;
    private final float cropScale = new SessionConfig().featureCropScale;
    private final float livenessThreshold = new SessionConfig().livenessThreshold;

    public InsightFaceBackend(Context context, String name, String recognizerAsset) {
        this.name = name;
        this.recognizer = recognizerAsset;
        // Every model on every frame, with whole-frame detection each time (no tracking): the
        // worst case per frame. SessionBackend measures what the app actually runs.
        frames = new ScrfdEngines(context.getAssets(), 320, 0.5f, 1);
        try {
            engine = NativeEngine.insightFace(context.getAssets(), recognizerAsset, 4);
        } catch (RuntimeException error) {
            frames.close();
            throw error;
        }
    }

    @Override public String name() { return name; }

    @Override public String describe() {
        return "SCRFD-500M (whole frame, long side 320, every frame) + silent liveness on one frame; recognizer " + recognizer
                + " on a crop x" + cropScale + "; enrolment SCRFD 640; score cosine; liveness threshold " + livenessThreshold;
    }

    @Override public void beginClip(int sensorRotationDegrees) { frames.reset(); }

    @Override
    public FrameOutcome process(ClipFile.Frame frame, boolean runLiveness, boolean runFeature) {
        FrameOutcome outcome = new FrameOutcome();
        CameraFrame camera = new CameraFrame(frame.nv21, frame.width, frame.height, 0, frame.timestampNs / 1_000_000L);
        Timer timer = Timer.start();
        List<Detection> faces = frames.detect(camera);
        outcome.detectMs = timer.lapMs();
        outcome.faceCount = faces.size();
        Detection main = null;
        for (Detection d : faces) if (main == null || d.rect.area() > main.rect.area()) main = d;
        if (main == null) {
            outcome.livenessStatus = FrameOutcome.LIVE_UNKNOWN;
            if (runFeature) {
                outcome.featureAttempted = true;
                outcome.note = "no face";
            }
            return outcome;
        }
        FaceRect r = main.rect;
        outcome.box = new int[]{Math.round(r.left), Math.round(r.top), Math.round(r.right) - 1, Math.round(r.bottom) - 1};
        if (runLiveness) {
            timer.lapMs();
            float score = frames.score(camera, r);
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
            outcome.feature = engine.extract(bgr, width, height, local, NativeEngine.LandmarkMode.SUBPIXEL, null);
            outcome.featureMs = timer.lapMs();
            if (outcome.feature == null) outcome.note = "no usable face for recognition";
        }
        return outcome;
    }

    @Override public Object enroll(Bitmap photo) {
        Bitmap argb = photo.getConfig() == Bitmap.Config.ARGB_8888 ? photo : photo.copy(Bitmap.Config.ARGB_8888, false);
        return engine.extract(argb, NativeEngine.LandmarkMode.SUBPIXEL, null);
    }

    @Override public float compare(Object probe, Object reference) {
        return Gallery.cosine((float[]) probe, (float[]) reference);
    }

    @Override public void close() {
        engine.close();
        frames.close();
    }
}
