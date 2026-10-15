package com.bonuos.facebench.arcsoft;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Rect;
import android.os.SystemClock;
import com.arcsoft.face.ErrorInfo;
import com.arcsoft.face.FaceEngine;
import com.arcsoft.face.FaceFeature;
import com.arcsoft.face.FaceInfo;
import com.arcsoft.face.FaceSimilar;
import com.arcsoft.face.LivenessInfo;
import com.arcsoft.face.enums.CompareModel;
import com.arcsoft.face.enums.DetectFaceOrientPriority;
import com.arcsoft.face.enums.DetectMode;
import com.arcsoft.face.util.ImageUtils;
import com.bonuos.facebench.backend.FaceBackend;
import com.bonuos.facebench.backend.FrameOutcome;
import com.bonuos.facebench.clip.ClipFile;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;

/**
 * ArcSoft run the way its own demo app runs it, in real time, so its time-to-recognition can be
 * measured instead of estimated. Public API only; benchmark use only.
 *
 * <p>Per frame (analysis thread): VIDEO-mode tracking, a size filter and a move filter (the
 * demo's FaceMoveFilter; the same time-based stability as our SessionBackend). For each stable,
 * undecided faceId a feature task and a liveness task go to two single-thread pools in parallel,
 * each with a queue as long as the face limit (full = dropped, counts as a failed try). The
 * feature thread extracts, then waits for the liveness verdict (the demo's waitLock); on ALIVE it
 * compares against every enrolled feature (LIFE_PHOTO) and succeeds above 0.8, otherwise retries.
 * Liveness is a single-frame verdict at the default 0.5, retried until ALIVE, as in the demo.
 *
 * <p>Frames are released at their recorded timestamps and dropped when a newer one has already
 * arrived (CameraX KEEP_ONLY_LATEST), exactly like SessionBackend. Notes use the same
 * {@code recognized:<id>:<score>:<ms since clip start>} format.
 */
public final class ArcSoftSessionBackend implements FaceBackend {
    private static final int MAX_FACES = 5;
    private static final float LIVENESS_THRESHOLD = 0.5f;
    private static final float SIMILAR_THRESHOLD = 0.8f;
    private static final int EXTRACT_RETRY_COUNT = 3;
    private static final int MIN_FACE = 64;
    private static final float MOVE_LIMIT_RATIO = 0.08f;
    private static final long STABLE_MS = 167;

    private enum Live { UNKNOWN, ANALYZING, ALIVE, NOT_ALIVE }
    private enum Rec { TO_RETRY, SEARCHING, SUCCEED, FAILED }

    private final class Track {
        final int id;
        final ArrayList<float[]> centers = new ArrayList<>();
        Live live = Live.UNKNOWN;
        Rec rec = Rec.TO_RETRY;
        int tries;
        float score = Float.NaN;
        String user;

        Track(int id) { this.id = id; }
    }

    private final Context context;
    private final CompareModel compareModel;
    private FaceEngine tracker;
    private final FaceEngine fr = new FaceEngine(), fl = new FaceEngine(), enroller = new FaceEngine();
    private ThreadPoolExecutor frPool, flPool;
    private final Map<String, FaceFeature> gallery = new LinkedHashMap<>();
    private final Map<Integer, Track> tracks = new HashMap<>();
    private final Object lock = new Object();
    private long frameIntervalMs = 33, clipStartMs, wallStartMs;
    private int stableFrames = 5;
    private boolean started;
    private volatile boolean clipOpen;
    private volatile String recognizedNote;

    public ArcSoftSessionBackend(Context context, boolean idCardModel) {
        this.context = context.getApplicationContext();
        this.compareModel = idCardModel ? CompareModel.ID_CARD : CompareModel.LIFE_PHOTO;
        check(fr.init(this.context, DetectMode.ASF_DETECT_MODE_IMAGE, DetectFaceOrientPriority.ASF_OP_0_ONLY,
                MAX_FACES, FaceEngine.ASF_FACE_RECOGNITION), "fr engine init");
        check(fl.init(this.context, DetectMode.ASF_DETECT_MODE_IMAGE, DetectFaceOrientPriority.ASF_OP_0_ONLY,
                MAX_FACES, FaceEngine.ASF_LIVENESS), "fl engine init");
        check(fl.setLivenessParam(ArcSoftBackend.livenessParam(LIVENESS_THRESHOLD)), "liveness threshold");
        check(enroller.init(this.context, DetectMode.ASF_DETECT_MODE_IMAGE, DetectFaceOrientPriority.ASF_OP_0_ONLY,
                MAX_FACES, FaceEngine.ASF_FACE_DETECT | FaceEngine.ASF_FACE_RECOGNITION), "enrollment engine init");
    }

    private static void check(int code, String what) {
        if (code != ErrorInfo.MOK) throw new IllegalStateException(what + " failed: " + code);
    }

    private static ThreadPoolExecutor pool(String name) {
        return new ThreadPoolExecutor(1, 1, 0, TimeUnit.MILLISECONDS, new ArrayBlockingQueue<>(MAX_FACES),
                r -> new Thread(r, name), new ThreadPoolExecutor.AbortPolicy());
    }

    @Override public String name() { return "session_arcsoft"; }

    @Override public String describe() {
        return "ArcSoft demo flow replayed in real time: VIDEO tracking, move filter " + stableFrames
                + " frames, parallel FR/FL single-thread pools (queue " + MAX_FACES + "), FR waits for ALIVE, "
                + "liveness " + LIVENESS_THRESHOLD + " single frame, compare " + compareModel + " > " + SIMILAR_THRESHOLD;
    }

    @Override public void clipTiming(int frameCount, long durationMs) {
        frameIntervalMs = frameCount > 1 && durationMs > 0 ? Math.max(1, durationMs / (frameCount - 1)) : 33;
        stableFrames = (int) Math.max(2, Math.round((double) STABLE_MS / frameIntervalMs) + 1);
    }

    @Override public void beginClip(int sensorRotationDegrees) {
        endClip();
        tracker = new FaceEngine(); // fresh VIDEO engine: faceIds never carry across clips
        check(tracker.init(context, DetectMode.ASF_DETECT_MODE_VIDEO, DetectFaceOrientPriority.ASF_OP_0_ONLY,
                MAX_FACES, FaceEngine.ASF_FACE_DETECT), "tracking engine init");
        frPool = pool("arc-fr");
        flPool = pool("arc-fl");
        synchronized (lock) { tracks.clear(); }
        started = false;
        recognizedNote = null;
        clipOpen = true;
    }

    private void endClip() {
        clipOpen = false;
        synchronized (lock) { lock.notifyAll(); }
        for (ThreadPoolExecutor p : new ThreadPoolExecutor[]{frPool, flPool}) {
            if (p == null) continue;
            p.shutdownNow();
            try {
                p.awaitTermination(5, TimeUnit.SECONDS);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        }
        frPool = flPool = null;
        if (tracker != null) {
            tracker.unInit();
            tracker = null;
        }
    }

    @Override
    public FrameOutcome process(ClipFile.Frame frame, boolean runLiveness, boolean runFeature) {
        FrameOutcome outcome = new FrameOutcome();
        long ts = frame.timestampNs / 1_000_000L;
        if (!started) {
            started = true;
            clipStartMs = ts;
            wallStartMs = SystemClock.elapsedRealtime();
        }
        long due = wallStartMs + (ts - clipStartMs), now = SystemClock.elapsedRealtime();
        if (now < due) SystemClock.sleep(due - now);
        else if (now >= due + frameIntervalMs) {
            outcome.note = "dropped";
            return outcome;
        }
        long t0 = SystemClock.elapsedRealtimeNanos();
        List<FaceInfo> faces = new ArrayList<>();
        int code = tracker.detectFaces(frame.nv21, frame.width, frame.height, FaceEngine.CP_PAF_NV21, faces);
        FaceInfo main = null;
        synchronized (lock) {
            List<Integer> seen = new ArrayList<>();
            if (code == ErrorInfo.MOK) {
                for (FaceInfo face : faces) {
                    seen.add(face.getFaceId());
                    Track t = tracks.get(face.getFaceId());
                    if (t == null) tracks.put(face.getFaceId(), t = new Track(face.getFaceId()));
                    Rect r = face.getRect();
                    t.centers.add(new float[]{r.exactCenterX(), r.exactCenterY(), r.width()});
                    while (t.centers.size() > stableFrames) t.centers.remove(0);
                    if (main == null || r.width() * r.height() > main.getRect().width() * main.getRect().height()) main = face;
                    if (t.rec == Rec.SUCCEED || !passes(t, r)) continue;
                    if (t.rec == Rec.TO_RETRY || t.rec == Rec.FAILED) submitFr(t, frame, face);
                    if (t.live == Live.UNKNOWN || t.live == Live.NOT_ALIVE) submitFl(t, frame, face);
                }
            }
            for (Iterator<Integer> it = tracks.keySet().iterator(); it.hasNext(); ) if (!seen.contains(it.next())) it.remove();
            outcome.faceCount = faces.size();
            if (main != null) {
                Rect r = main.getRect();
                outcome.box = new int[]{r.left, r.top, r.right, r.bottom};
                Track t = tracks.get(main.getFaceId());
                outcome.livenessStatus = t.live == Live.ALIVE ? FrameOutcome.LIVE_ALIVE
                        : t.live == Live.NOT_ALIVE ? FrameOutcome.LIVE_NOT_ALIVE : FrameOutcome.LIVE_UNKNOWN;
                outcome.note = "state:" + t.rec + ":" + t.live;
            } else {
                outcome.livenessStatus = FrameOutcome.LIVE_UNKNOWN;
            }
        }
        outcome.detectMs = (SystemClock.elapsedRealtimeNanos() - t0) / 1e6;
        String recognized = recognizedNote;
        if (recognized != null) {
            outcome.note = recognized;
            recognizedNote = null;
        }
        return outcome;
    }

    /** Size and move filters (caller holds the lock). */
    private boolean passes(Track t, Rect r) {
        if (Math.min(r.width(), r.height()) < MIN_FACE || t.centers.size() < stableFrames) return false;
        for (int i = 1; i < t.centers.size(); i++) {
            float[] a = t.centers.get(i - 1), b = t.centers.get(i);
            if (Math.hypot(b[0] - a[0], b[1] - a[1]) > MOVE_LIMIT_RATIO * b[2]) return false;
        }
        return true;
    }

    private void submitFr(Track t, ClipFile.Frame frame, FaceInfo face) {
        final byte[] nv21 = frame.nv21.clone();
        final FaceInfo info = new FaceInfo(face);
        final int w = frame.width, h = frame.height;
        t.rec = Rec.SEARCHING;
        try {
            frPool.execute(() -> recognize(t, nv21, w, h, info));
        } catch (RejectedExecutionException busy) {
            failFr(t); // ERROR_BUSY path of the demo
        }
    }

    private void submitFl(Track t, ClipFile.Frame frame, FaceInfo face) {
        final byte[] nv21 = frame.nv21.clone();
        final FaceInfo info = new FaceInfo(face);
        final int w = frame.width, h = frame.height;
        t.live = Live.ANALYZING;
        try {
            flPool.execute(() -> liveness(t, nv21, w, h, info));
        } catch (RejectedExecutionException busy) {
            t.live = Live.UNKNOWN;
        }
    }

    private void liveness(Track t, byte[] nv21, int w, int h, FaceInfo info) {
        Live verdict = Live.UNKNOWN;
        if (fl.process(nv21, w, h, FaceEngine.CP_PAF_NV21, Collections.singletonList(info), FaceEngine.ASF_LIVENESS) == ErrorInfo.MOK) {
            List<LivenessInfo> result = new ArrayList<>();
            if (fl.getLiveness(result) == ErrorInfo.MOK && !result.isEmpty()) {
                int v = result.get(0).getLiveness();
                verdict = v == LivenessInfo.ALIVE ? Live.ALIVE : v == LivenessInfo.NOT_ALIVE ? Live.NOT_ALIVE : Live.UNKNOWN;
            }
        }
        synchronized (lock) {
            t.live = verdict; // NOT_ALIVE / UNKNOWN are resubmitted on the next frame (retry interval 0)
            lock.notifyAll();
        }
    }

    private void recognize(Track t, byte[] nv21, int w, int h, FaceInfo info) {
        FaceFeature feature = new FaceFeature();
        if (ArcSoftBackend.extract(fr, nv21, w, h, FaceEngine.CP_PAF_NV21, info, false, feature) != ErrorInfo.MOK) {
            synchronized (lock) { failFr(t); }
            return;
        }
        synchronized (lock) { // the demo's waitLock: identity only after liveness says ALIVE
            while (clipOpen && t.live != Live.ALIVE && tracks.get(t.id) == t) {
                try {
                    lock.wait(20);
                } catch (InterruptedException e) {
                    return;
                }
            }
            if (t.live != Live.ALIVE) return;
        }
        String best = null;
        float bestScore = -1f;
        for (Map.Entry<String, FaceFeature> e : gallery.entrySet()) {
            FaceSimilar similar = new FaceSimilar();
            if (fr.compareFaceFeature(feature, e.getValue(), compareModel, similar) == ErrorInfo.MOK && similar.getScore() > bestScore) {
                bestScore = similar.getScore();
                best = e.getKey();
            }
        }
        synchronized (lock) {
            t.score = bestScore;
            if (best != null && bestScore > SIMILAR_THRESHOLD) {
                t.rec = Rec.SUCCEED;
                t.user = best;
                recognizedNote = String.format(java.util.Locale.ROOT, "recognized:%s:%.4f:%d", best, bestScore,
                        SystemClock.elapsedRealtime() - wallStartMs);
            } else {
                failFr(t);
            }
        }
    }

    private void failFr(Track t) {
        t.tries++;
        t.rec = t.tries > EXTRACT_RETRY_COUNT ? Rec.FAILED : Rec.TO_RETRY;
    }

    @Override public Object enroll(Bitmap photo) {
        Bitmap aligned = ImageUtils.alignBitmapForBgr24(photo);
        if (aligned == null) return null;
        byte[] bgr = ImageUtils.bitmapToBgr24(aligned);
        List<FaceInfo> faces = new ArrayList<>();
        if (enroller.detectFaces(bgr, aligned.getWidth(), aligned.getHeight(), FaceEngine.CP_PAF_BGR24, faces) != ErrorInfo.MOK
                || faces.isEmpty()) return null;
        FaceInfo largest = faces.get(0);
        for (FaceInfo f : faces)
            if (f.getRect().width() * f.getRect().height() > largest.getRect().width() * largest.getRect().height()) largest = f;
        FaceFeature feature = new FaceFeature();
        return ArcSoftBackend.extract(enroller, bgr, aligned.getWidth(), aligned.getHeight(), FaceEngine.CP_PAF_BGR24,
                largest, true, feature) == ErrorInfo.MOK ? feature : null;
    }

    @Override public void enrolled(String subjectId, Object reference) {
        gallery.put(subjectId, (FaceFeature) reference);
    }

    @Override public float compare(Object probe, Object reference) {
        FaceSimilar similar = new FaceSimilar();
        return fr.compareFaceFeature((FaceFeature) probe, (FaceFeature) reference, compareModel, similar) == ErrorInfo.MOK
                ? similar.getScore() : Float.NaN;
    }

    @Override public void close() {
        endClip();
        fr.unInit();
        fl.unInit();
        enroller.unInit();
    }
}
