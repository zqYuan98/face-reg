package com.bonuos.facesdk.engine;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.Callable;
import java.util.concurrent.Executor;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.RejectedExecutionException;

/**
 * Continuous-video recognition: one state machine per tracked face.
 *
 * <p>Per frame (analysis thread): detect, track and run the cheap filters. A face that passes is
 * handed to two workers in parallel, as in ArcSoft's demo: liveness (when the scorer is a
 * {@link LivenessEngine}) and feature extraction. Whichever finishes last decides at once, on its
 * own thread, under the session lock; nothing waits for the next frame. Once a track is
 * recognised it never runs a model again, which turns "every model on every frame" into "the
 * expensive models once per person".
 *
 * <p>Call {@link #onFrame} from one thread. {@link Listener#onFaces} runs on that thread; the other
 * callbacks may also run on a worker thread, with the session lock held, so keep them short.
 *
 * @param <T> gallery entry type, e.g. a user record; must implement equals/hashCode by identity
 */
public final class FaceSession<T> implements AutoCloseable {

    /** Upright-frame face detector. Called once per frame, before any scorer call for that frame. */
    public interface Detector {
        List<Detection> detect(CameraFrame frame);
    }

    /** Silent-liveness score for a box of the frame last passed to the detector; NaN on failure. */
    public interface LivenessScorer {
        float score(CameraFrame frame, FaceRect face);
    }

    /**
     * Copies what it needs from {@code frame} now (the buffer is reused after onFrame returns) and
     * returns the task that extracts a feature from that copy. The task runs on the worker thread
     * and returns null when the recognizer finds no usable face.
     *
     * @param face      the tracked face box; {@code crop} is that box enlarged by featureCropScale
     * @param keypoints the detector's five points for this frame (x0,y0..x4,y4), or null
     */
    public interface FeatureEngine {
        Callable<float[]> capture(CameraFrame frame, FaceRect face, float[] keypoints, FaceRect crop);
    }

    /**
     * Liveness that runs off the analysis thread, in parallel with feature extraction (ArcSoft's
     * FL thread). Copies what it needs from {@code frame} now; the task returns the real-face
     * probability, NaN on failure. A {@link LivenessScorer} that also implements this runs async.
     */
    public interface LivenessEngine {
        Callable<Float> captureLiveness(CameraFrame frame, FaceRect face);
    }

    public interface GallerySource<T> {
        /** Current snapshot; never null (use {@link Gallery#empty()}). */
        Gallery<T> gallery();
    }

    public interface Listener<T> {
        /** Every frame: all live tracks, for drawing. */
        void onFaces(List<FaceState<T>> faces, CameraFrame frame);

        /** A track was recognised. Not repeated for the same person within the identity cooldown. */
        void onRecognized(RecognitionEvent<T> event);

        /** A track used up its attempts without a match; it backs off and tries again later. */
        void onRecognitionFailed(long trackId, float bestScore, Reason reason);

        /**
         * Every gallery decision, for calibration logs: the probe's best score, how many frames were
         * fused into it, and the outcome (NONE = accepted, NEED_MORE_FRAMES, UNKNOWN_PERSON, ...).
         */
        default void onMatchScored(long trackId, float score, float secondScore, int fusedFrames, Liveness liveness, Reason outcome) { }
    }

    public enum Liveness { UNKNOWN, ALIVE, NOT_ALIVE }

    public enum Recognition { PENDING, SUCCEED, FAILED }

    /** Why a face is not progressing. NONE means it is being processed normally. */
    public enum Reason {
        NONE, FACE_TOO_SMALL, BEYOND_BOUNDARY, NOT_STABLE, MULTIPLE_FACES, NOT_LARGEST, LOST, FACE_ANGLE_TOO_LARGE,
        TOO_DARK, TOO_BRIGHT, BLURRY, NOT_ALIVE, NO_FEATURE, NEED_MORE_FRAMES, UNKNOWN_PERSON,
        GALLERY_EMPTY, FEATURE_ERROR
    }

    /** Immutable per-track snapshot for the UI. */
    public static final class FaceState<T> {
        public final long trackId;
        /** Smoothed box for drawing; models use the raw box. */
        public final FaceRect rect;
        public final Liveness liveness;
        public final float livenessScore;
        public final Recognition recognition;
        public final T user;
        public final float score;
        public final Reason reason;

        FaceState(long trackId, FaceRect rect, Liveness liveness, float livenessScore, Recognition recognition,
                  T user, float score, Reason reason) {
            this.trackId = trackId;
            this.rect = rect;
            this.liveness = liveness;
            this.livenessScore = livenessScore;
            this.recognition = recognition;
            this.user = user;
            this.score = score;
            this.reason = reason;
        }
    }

    public static final class RecognitionEvent<T> {
        public final long trackId;
        public final T user;
        public final float score, secondScore, livenessScore;
        /** Frames averaged into the probe that matched. */
        public final int fusedFrames;
        public final long timestampMs;

        RecognitionEvent(long trackId, T user, float score, float secondScore, float livenessScore, int fusedFrames, long timestampMs) {
            this.trackId = trackId;
            this.user = user;
            this.score = score;
            this.secondScore = secondScore;
            this.livenessScore = livenessScore;
            this.fusedFrames = fusedFrames;
            this.timestampMs = timestampMs;
        }
    }

    private static final class TrackState<T> {
        final long id;
        Liveness liveness = Liveness.UNKNOWN;
        final ArrayDeque<Boolean> votes = new ArrayDeque<>();
        float livenessScore = Float.NaN;
        long livenessRetryAt;
        Recognition recognition = Recognition.PENDING;
        long retryAt;
        int tries;
        /** Consecutive FAILED rounds; doubles the back-off. */
        int failedRounds;
        boolean extracting;
        float[] fusedSum;
        int fusedCount;
        Gallery.Match<T> match;
        float bestSharpness;
        T user;
        float score = Float.NaN;
        Reason reason = Reason.NONE;

        boolean livenessPending;

        TrackState(long id) { this.id = id; }
    }

    private final SessionConfig config;
    private final Detector detector;
    private final LivenessScorer liveness;
    private final FeatureEngine features;
    private final GallerySource<T> gallery;
    private final Listener<T> listener;
    private final LivenessEngine livenessAsync;
    private final Executor worker, livenessWorker;
    private final ExecutorService ownedWorker, ownedLivenessWorker;
    /** Guards all track state: taken by onFrame after detection and by worker completions. */
    private final Object lock = new Object();
    private final FaceTracker tracker;
    private final LumaQuality quality = new LumaQuality();
    private final Map<Long, TrackState<T>> states = new HashMap<>();
    private final Map<T, Long> lastReported = new HashMap<>();
    private volatile boolean busy, livenessBusy;
    private volatile boolean closed;
    private boolean idle;
    private long lastAnalysedAt = Long.MIN_VALUE;
    /** Session time for worker completions: the last frame's timestamp plus wall time since it. */
    private long lastFrameMs, lastFrameNanos;

    /** Uses a private single-thread feature worker, shut down by {@link #close()}. */
    public FaceSession(SessionConfig config, Detector detector, LivenessScorer liveness, FeatureEngine features,
                       GallerySource<T> gallery, Listener<T> listener) {
        this(config, detector, liveness, features, gallery, listener, null);
    }

    /** {@code worker} runs both feature and async liveness tasks; null means private threads. */
    public FaceSession(SessionConfig config, Detector detector, LivenessScorer liveness, FeatureEngine features,
                       GallerySource<T> gallery, Listener<T> listener, Executor worker) {
        this(config, detector, liveness, features, gallery, listener, worker, worker);
    }

    /**
     * {@code worker} runs feature tasks and {@code livenessWorker} async liveness tasks; null means a
     * private single-thread executor (shut down by {@link #close()}); supplied ones are not shut down.
     * Liveness runs async only when {@code liveness} also implements {@link LivenessEngine}.
     */
    public FaceSession(SessionConfig config, Detector detector, LivenessScorer liveness, FeatureEngine features,
                       GallerySource<T> gallery, Listener<T> listener, Executor worker, Executor livenessWorker) {
        this.config = config.clone();
        this.config.validate();
        this.detector = detector;
        this.liveness = liveness;
        this.livenessAsync = liveness instanceof LivenessEngine ? (LivenessEngine) liveness : null;
        this.features = features;
        this.gallery = gallery;
        this.listener = listener;
        if (worker == null) {
            ownedWorker = Executors.newSingleThreadExecutor(r -> {
                Thread thread = new Thread(r, "face-feature");
                thread.setPriority(Thread.NORM_PRIORITY - 1);
                return thread;
            });
            this.worker = ownedWorker;
        } else {
            ownedWorker = null;
            this.worker = worker;
        }
        if (livenessAsync != null && livenessWorker == null) {
            ownedLivenessWorker = Executors.newSingleThreadExecutor(r -> new Thread(r, "face-liveness"));
            this.livenessWorker = ownedLivenessWorker;
        } else {
            ownedLivenessWorker = null;
            this.livenessWorker = livenessWorker;
        }
        tracker = new FaceTracker(this.config.trackMinIou, this.config.trackMaxMissed, this.config.stableFrames,
                this.config.boxSmoothing, this.config.firstTrackId);
    }

    /** Next track id; persist it and pass it back as {@link SessionConfig#firstTrackId} after a restart. */
    public long nextTrackId() { return tracker.nextId(); }

    public void onFrame(CameraFrame frame) {
        if (closed) return;
        long now = frame.timestampMs;
        synchronized (lock) {
            // Idle: nothing to recognise, so a few frames per second are enough to notice a new face.
            if (idle && now - lastAnalysedAt < config.idleFrameIntervalMs) return;
            lastAnalysedAt = now;
            lastFrameMs = now;
            lastFrameNanos = System.nanoTime();
        }
        // Detection and tracking belong to the analysis thread alone; no lock needed.
        List<Track> tracks = tracker.update(detector.detect(frame));
        List<FaceState<T>> faces;
        synchronized (lock) {
            faces = updateStates(tracks, frame, now);
        }
        listener.onFaces(faces, frame);
    }

    private List<FaceState<T>> updateStates(List<Track> tracks, CameraFrame frame, long now) {

        Set<Long> live = new HashSet<>();
        int visible = 0;
        Track largest = null;
        for (Track track : tracks) {
            live.add(track.id);
            if (!track.visible()) continue;
            visible++;
            if (largest == null || track.rect.area() > largest.rect.area()) largest = track;
        }
        states.keySet().retainAll(live);

        List<FaceState<T>> snapshot = new ArrayList<>(tracks.size());
        for (Track track : tracks) {
            TrackState<T> state = states.get(track.id);
            if (state == null) {
                state = new TrackState<>(track.id);
                states.put(track.id, state);
            }
            if (!track.visible()) {
                state.reason = Reason.LOST;
            } else {
                Reason gate = gate(track, frame, visible, largest);
                state.reason = gate;
                if (gate == Reason.NONE) process(state, track, frame, now);
            }
            snapshot.add(new FaceState<>(track.id, track.smoothedRect(), state.liveness, state.livenessScore,
                    state.recognition, state.user, state.score, state.reason));
        }
        expireCooldowns(now);
        idle = !busy && !livenessBusy;
        for (FaceState<T> face : snapshot) {
            TrackState<T> state = states.get(face.trackId);
            if (state.recognition == Recognition.PENDING || (state.recognition == Recognition.FAILED && now >= state.retryAt)) {
                idle = false;
                break;
            }
        }
        return Collections.unmodifiableList(snapshot);
    }

    /** Session time now, for completions that arrive between frames. Caller holds the lock. */
    private long now() {
        return lastFrameMs + (System.nanoTime() - lastFrameNanos) / 1_000_000L;
    }

    /** Filters ordered by cost; everything here is O(1) per face. */
    private Reason gate(Track track, CameraFrame frame, int visible, Track largest) {
        FaceRect r = track.rect;
        if (Math.min(r.width(), r.height()) < config.minFaceSize) return Reason.FACE_TOO_SMALL;
        float tx = r.width() * config.boundaryTolerance, ty = r.height() * config.boundaryTolerance;
        if (r.left < -tx || r.top < -ty || r.right > frame.uprightWidth() + tx || r.bottom > frame.uprightHeight() + ty)
            return Reason.BEYOND_BOUNDARY;
        if (config.strictSingleFace && visible > 1) return Reason.MULTIPLE_FACES;
        if (config.keepMaxFace && track != largest) return Reason.NOT_LARGEST;
        if (track.maxRecentMove() > config.moveLimitRatio * r.width()) return Reason.NOT_STABLE;
        if (track.keypoints != null) {
            Pose5 pose = Pose5.estimate(track.keypoints);
            if (!(Math.abs(pose.yaw) <= config.maxYaw && Math.abs(pose.pitch) <= config.maxPitch)) return Reason.FACE_ANGLE_TOO_LARGE;
        }
        return Reason.NONE;
    }

    private void process(TrackState<T> state, Track track, CameraFrame frame, long now) {
        if (state.recognition == Recognition.SUCCEED) return; // decided; never run a model again
        if (state.recognition == Recognition.FAILED) {
            if (now < state.retryAt) {
                state.reason = Reason.UNKNOWN_PERSON;
                return;
            }
            state.recognition = Recognition.PENDING;
        }
        updateLiveness(state, track, frame, now);

        quality.measure(frame, track.rect);
        state.bestSharpness = Math.max(state.bestSharpness, quality.sharpness);
        Reason qualityReason = quality.brightness < config.minBrightness ? Reason.TOO_DARK
                : quality.brightness > config.maxBrightness ? Reason.TOO_BRIGHT
                : quality.sharpness < Math.max(config.minSharpness, config.relativeSharpness * state.bestSharpness) ? Reason.BLURRY
                : Reason.NONE;
        if (state.liveness == Liveness.NOT_ALIVE) state.reason = Reason.NOT_ALIVE;
        else if (qualityReason != Reason.NONE) state.reason = qualityReason;

        if (state.match == null && !state.extracting && !busy && now >= state.retryAt
                && state.liveness != Liveness.NOT_ALIVE && qualityReason == Reason.NONE) {
            if (gallery.gallery().size() == 0) state.reason = Reason.GALLERY_EMPTY;
            else submit(state, track, frame);
        }
        decide(state, now);
    }

    private void updateLiveness(TrackState<T> state, Track track, CameraFrame frame, long now) {
        if (!config.livenessEnabled) {
            state.liveness = Liveness.ALIVE;
            return;
        }
        if (state.liveness == Liveness.ALIVE || now < state.livenessRetryAt || state.livenessPending) return;
        if (livenessAsync == null) {
            addVote(state, liveness.score(frame, track.rect), now);
            return;
        }
        if (livenessBusy) return; // one evaluation in flight: the next one uses a fresher frame
        final Callable<Float> task;
        try {
            task = livenessAsync.captureLiveness(frame, track.rect);
        } catch (RuntimeException error) {
            return;
        }
        final long id = state.id;
        state.livenessPending = true;
        livenessBusy = true;
        try {
            livenessWorker.execute(() -> {
                float score = Float.NaN;
                try {
                    if (!closed) score = task.call();
                } catch (Exception error) {
                    // no vote
                }
                synchronized (lock) {
                    livenessBusy = false;
                    TrackState<T> current = states.get(id);
                    if (current == null || closed) return; // the face left meanwhile
                    current.livenessPending = false;
                    long t = now();
                    addVote(current, score, t);
                    decide(current, t);
                }
            });
        } catch (RejectedExecutionException error) {
            livenessBusy = false;
            state.livenessPending = false;
        }
    }

    /**
     * Records one liveness vote. Decides as soon as the outcome is certain: ALIVE once
     * {@code livenessRequired} votes in the window say so, NOT_ALIVE once enough say otherwise
     * that ALIVE can no longer be reached. The window need not fill first.
     */
    private void addVote(TrackState<T> state, float score, long now) {
        if (Float.isNaN(score) || state.liveness == Liveness.ALIVE) return; // engine error: no vote
        state.livenessScore = score;
        state.votes.addLast(score > config.livenessThreshold);
        while (state.votes.size() > config.livenessWindow) state.votes.removeFirst();
        int alive = 0;
        for (Boolean vote : state.votes) if (vote) alive++;
        if (alive >= config.livenessRequired) {
            state.liveness = Liveness.ALIVE;
        } else if (state.votes.size() - alive > config.livenessWindow - config.livenessRequired) {
            state.liveness = Liveness.NOT_ALIVE;
            state.livenessRetryAt = now + config.livenessFailedRetryMs;
            state.votes.clear();
        }
    }

    private void submit(TrackState<T> state, Track track, CameraFrame frame) {
        FaceRect crop = config.featureCropScale > 0
                ? track.rect.scaledClipped(config.featureCropScale, frame.uprightWidth(), frame.uprightHeight())
                : new FaceRect(0, 0, frame.uprightWidth(), frame.uprightHeight());
        final Callable<float[]> task;
        try {
            task = features.capture(frame, track.rect, track.keypoints, crop);
        } catch (RuntimeException error) {
            state.reason = Reason.FEATURE_ERROR;
            return;
        }
        final long id = state.id;
        busy = true;
        state.extracting = true;
        try {
            worker.execute(() -> {
                float[] feature = null;
                boolean failed = false;
                try {
                    if (!closed) feature = task.call();
                } catch (Exception error) {
                    failed = true;
                } finally {
                    synchronized (lock) {
                        busy = false;
                        if (!closed) applyFeature(id, feature, failed, now());
                    }
                }
            });
        } catch (RejectedExecutionException error) {
            busy = false;
            state.extracting = false;
        }
    }

    /** A feature came back (worker thread, lock held): fuse, search, decide if liveness is in. */
    private void applyFeature(long trackId, float[] feature, boolean failed, long now) {
        TrackState<T> state = states.get(trackId);
        if (state == null) return; // the face left before its feature came back
        state.extracting = false;
        if (feature == null) {
            attemptFailed(state, now, failed ? Reason.FEATURE_ERROR : Reason.NO_FEATURE, Float.NaN);
            return;
        }
        float[] unit = unit(feature);
        if (unit == null) {
            attemptFailed(state, now, Reason.FEATURE_ERROR, Float.NaN);
            return;
        }
        if (state.fusedSum == null || state.fusedSum.length != unit.length) {
            state.fusedSum = new float[unit.length];
            state.fusedCount = 0;
        }
        for (int i = 0; i < unit.length; i++) state.fusedSum[i] += unit[i];
        state.fusedCount++;
        Gallery.Match<T> match = gallery.gallery().search(state.fusedSum);
        if (match == null) {
            attemptFailed(state, now, Reason.FEATURE_ERROR, Float.NaN); // dimension mismatch
            return;
        }
        state.match = match;
        if (state.liveness != Liveness.ALIVE)
            listener.onMatchScored(state.id, match.score, match.secondScore, state.fusedCount, state.liveness, Reason.NOT_ALIVE);
        decide(state, now);
    }

    /** Runs when both a match and liveness are available, whichever arrived last. */
    private void decide(TrackState<T> state, long now) {
        Gallery.Match<T> m = state.match;
        if (m == null || state.recognition != Recognition.PENDING || state.liveness != Liveness.ALIVE) return;
        state.match = null;
        Reason outcome = m.best == null ? Reason.GALLERY_EMPTY
                : m.score >= config.thresholdHigh && m.margin() >= config.topMargin ? Reason.NONE
                : m.score >= config.thresholdLow && state.fusedCount < config.fusionFrames ? Reason.NEED_MORE_FRAMES
                : Reason.UNKNOWN_PERSON;
        listener.onMatchScored(state.id, m.score, m.secondScore, state.fusedCount, state.liveness, outcome);
        if (m.best == null) {
            attemptFailed(state, now, Reason.GALLERY_EMPTY, Float.NaN);
        } else if (m.score >= config.thresholdHigh && m.margin() >= config.topMargin) {
            state.recognition = Recognition.SUCCEED;
            state.user = m.best;
            state.score = m.score;
            state.reason = Reason.NONE;
            Long last = lastReported.get(m.best);
            if (last == null || now - last >= config.identityCooldownMs) {
                lastReported.put(m.best, now);
                listener.onRecognized(new RecognitionEvent<>(state.id, m.best, m.score, m.secondScore,
                        state.livenessScore, state.fusedCount, now));
            }
        } else if (m.score >= config.thresholdLow && state.fusedCount < config.fusionFrames) {
            state.score = m.score;
            state.reason = Reason.NEED_MORE_FRAMES; // borderline: average in the next good frame
        } else {
            attemptFailed(state, now, Reason.UNKNOWN_PERSON, m.score);
        }
    }

    private void attemptFailed(TrackState<T> state, long now, Reason reason, float score) {
        state.tries++;
        state.fusedSum = null;
        state.fusedCount = 0;
        state.match = null;
        state.reason = reason;
        if (!Float.isNaN(score)) state.score = score;
        if (state.tries < config.extractRetryCount) return;
        state.tries = 0;
        state.recognition = Recognition.FAILED;
        long backoff = config.recognizeFailedRetryMs << Math.min(state.failedRounds, 16);
        state.retryAt = now + Math.min(backoff, config.recognizeFailedRetryMaxMs);
        state.failedRounds++;
        // A face that keeps failing gets its liveness re-checked too, as ArcSoft's demo does.
        state.liveness = Liveness.UNKNOWN;
        state.votes.clear();
        listener.onRecognitionFailed(state.id, state.score, reason);
    }

    private void expireCooldowns(long now) {
        if (lastReported.size() < 64) return;
        for (Iterator<Long> it = lastReported.values().iterator(); it.hasNext(); ) {
            if (now - it.next() >= config.identityCooldownMs) it.remove();
        }
    }

    private static float[] unit(float[] v) {
        double sum = 0;
        for (float x : v) {
            if (!Float.isFinite(x)) return null;
            sum += (double) x * x;
        }
        if (!(sum > 0)) return null;
        float inv = (float) (1.0 / Math.sqrt(sum));
        float[] out = new float[v.length];
        for (int i = 0; i < v.length; i++) out[i] = v[i] * inv;
        return out;
    }

    /** Forgets all tracks, e.g. after switching cameras. Pending results are discarded. */
    public void reset() {
        tracker.reset();
        synchronized (lock) {
            states.clear();
        }
    }

    @Override
    public void close() {
        closed = true;
        if (ownedWorker != null) ownedWorker.shutdownNow();
        if (ownedLivenessWorker != null) ownedLivenessWorker.shutdownNow();
        synchronized (lock) {
            states.clear();
        }
    }
}
