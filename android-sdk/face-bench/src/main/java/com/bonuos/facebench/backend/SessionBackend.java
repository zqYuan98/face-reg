package com.bonuos.facebench.backend;

import android.content.Context;
import android.graphics.Bitmap;
import android.os.SystemClock;
import com.bonuos.facebench.clip.ClipFile;
import com.bonuos.facesdk.engine.CameraFrame;
import com.bonuos.facesdk.engine.FaceRect;
import com.bonuos.facesdk.engine.FaceSession;
import com.bonuos.facesdk.engine.Gallery;
import com.bonuos.facesdk.engine.NativeEngine;
import com.bonuos.facesdk.engine.NativeFeatureEngine;
import com.bonuos.facesdk.engine.ScrfdEngines;
import com.bonuos.facesdk.engine.SessionConfig;
import java.util.ArrayList;
import java.util.List;

/**
 * Replays a clip through the real {@link FaceSession} (tracking, cheap filters, liveness voting,
 * one feature per person), as the app runs it, instead of running every model on every frame.
 *
 * <p>Runs in real time, like the app: frames are released at their recorded timestamps, the
 * feature worker is a separate thread as in the app, and a frame is dropped when a newer one has
 * already arrived by the time the analysis thread is free (CameraX KEEP_ONLY_LATEST). The
 * stability requirement is converted to time ({@link #STABLE_MS}), so a 10 fps recording waits as
 * long as a 30 fps camera does. {@code detect_ms} is the analysis-thread cost of a processed
 * frame; dropped frames carry the note {@code dropped}; the frame on which the person is
 * recognised carries {@code recognized:<id>:<score>:<ms since clip start>}.
 */
public final class SessionBackend implements FaceBackend {
    private final String name, recognizer;
    private final ScrfdEngines frames;
    private final NativeEngine engine;
    /** The app's 5 stable frames at a 30 fps camera. */
    static final long STABLE_MS = 167;

    private final SessionConfig config = new SessionConfig();
    private SessionConfig clipConfig = config;
    private long frameIntervalMs = 33;
    private final List<Gallery.Entry<String>> enrolled = new ArrayList<>();
    private Gallery<String> gallery = Gallery.empty();
    private FaceSession<String> session;
    private List<FaceSession.FaceState<String>> faces = new ArrayList<>();
    private final List<FaceSession.RecognitionEvent<String>> events = new ArrayList<>();
    private long clipStartMs, wallStartMs;
    private boolean started;

    public SessionBackend(Context context, String name, String recognizerAsset) {
        this.name = name;
        this.recognizer = recognizerAsset;
        boolean r50 = !NativeEngine.INSIGHTFACE_MBF.equals(recognizerAsset);
        // Same settings as FaceRecognitionActivity for InsightFace (thresholds from LFW at FAR 1e-5).
        config.thresholdHigh = r50 ? 0.31f : 0.33f;
        config.thresholdLow = r50 ? 0.26f : 0.28f;
        config.minFaceSize = 64;
        frames = new ScrfdEngines(context.getAssets(), 320, 0.5f);
        try {
            engine = NativeEngine.insightFace(context.getAssets(), recognizerAsset, 4);
        } catch (RuntimeException error) {
            frames.close();
            throw error;
        }
    }

    @Override public String name() { return name; }

    @Override public String describe() {
        return "FaceSession replay with simulated KEEP_ONLY_LATEST drops: SCRFD-500M tracking (whole frame long side 320 every "
                + ScrfdEngines.FULL_SCAN_EVERY + " frames, region " + ScrfdEngines.REGION_LONG_SIDE + " otherwise), liveness vote "
                + config.livenessRequired + "/" + config.livenessWindow + " at " + config.livenessThreshold + ", recognizer "
                + recognizer + ", accept >= " + config.thresholdHigh;
    }

    @Override public void enrolled(String subjectId, Object reference) {
        enrolled.add(new Gallery.Entry<>(subjectId, (float[]) reference));
        gallery = Gallery.build(enrolled, 512);
    }

    @Override public void clipTiming(int frameCount, long durationMs) {
        frameIntervalMs = frameCount > 1 && durationMs > 0 ? Math.max(1, durationMs / (frameCount - 1)) : 33;
        clipConfig = config.clone();
        clipConfig.stableFrames = (int) Math.max(2, Math.round((double) STABLE_MS / frameIntervalMs) + 1);
    }

    @Override public void beginClip(int sensorRotationDegrees) {
        if (session != null) session.close();
        frames.reset();
        events.clear();
        faces = new ArrayList<>();
        started = false;
        final Gallery<String> clipGallery = gallery;
        session = new FaceSession<>(clipConfig, frames, frames,
                new NativeFeatureEngine(engine, NativeEngine.LandmarkMode.SUBPIXEL, config.maxYaw, config.maxPitch),
                () -> clipGallery, new FaceSession.Listener<String>() {
                    @Override public void onFaces(List<FaceSession.FaceState<String>> states, CameraFrame frame) { faces = states; }
                    @Override public void onRecognized(FaceSession.RecognitionEvent<String> event) { events.add(event); }
                    @Override public void onRecognitionFailed(long trackId, float bestScore, FaceSession.Reason reason) { }
                }, null); // private worker thread, as in the app
    }

    @Override
    public FrameOutcome process(ClipFile.Frame frame, boolean runLiveness, boolean runFeature) {
        FrameOutcome outcome = new FrameOutcome();
        long timestampMs = frame.timestampNs / 1_000_000L;
        if (!started) {
            started = true;
            clipStartMs = timestampMs;
            wallStartMs = SystemClock.elapsedRealtime();
        }
        // Release the frame at its recorded time; if the next frame has already arrived, this one
        // would have been replaced in CameraX's single-slot queue.
        long due = wallStartMs + (timestampMs - clipStartMs);
        long now = SystemClock.elapsedRealtime();
        if (now < due) {
            SystemClock.sleep(due - now);
        } else if (now >= due + frameIntervalMs) {
            outcome.note = "dropped";
            return outcome;
        }
        int eventsBefore = events.size();
        long t0 = SystemClock.elapsedRealtimeNanos();
        session.onFrame(new CameraFrame(frame.nv21, frame.width, frame.height, 0, timestampMs));
        double ms = (SystemClock.elapsedRealtimeNanos() - t0) / 1e6;
        outcome.detectMs = ms;
        FaceSession.FaceState<String> main = null;
        for (FaceSession.FaceState<String> face : faces) {
            if (face.reason == FaceSession.Reason.LOST) continue;
            outcome.faceCount++;
            if (main == null || face.rect.area() > main.rect.area()) main = face;
        }
        if (main == null) {
            outcome.livenessStatus = FrameOutcome.LIVE_UNKNOWN;
        } else {
            FaceRect r = main.rect;
            outcome.box = new int[]{Math.round(r.left), Math.round(r.top), Math.round(r.right) - 1, Math.round(r.bottom) - 1};
            outcome.livenessScore = main.livenessScore;
            outcome.livenessStatus = main.liveness == FaceSession.Liveness.ALIVE ? FrameOutcome.LIVE_ALIVE
                    : main.liveness == FaceSession.Liveness.NOT_ALIVE ? FrameOutcome.LIVE_NOT_ALIVE : FrameOutcome.LIVE_UNKNOWN;
            outcome.note = "state:" + main.recognition + ":" + main.reason;
        }
        if (events.size() > eventsBefore) {
            FaceSession.RecognitionEvent<String> e = events.get(events.size() - 1);
            outcome.note = String.format(java.util.Locale.ROOT, "recognized:%s:%.4f:%d", e.user, e.score, SystemClock.elapsedRealtime() - wallStartMs);
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
        if (session != null) session.close();
        engine.close();
        frames.close();
    }
}
