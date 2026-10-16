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
 * callbacks may also run on a worker thread, outside session/dataset locks; keep them short.
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

        /** Compatibility fallback; optimized engines may share only immutable capture storage. */
        default CapturedFeatures capturePair(CameraFrame frame, FaceRect face, float[] keypoints,
                                             FaceRect crop, FeatureEngine verifier) {
            return new CapturedFeatures(capture(frame, face, keypoints, crop),
                    verifier == null ? null : verifier.capture(frame, face, keypoints, crop));
        }
    }

    public static final class CapturedFeatures {
        public final Callable<float[]> primary, verification;
        public final int captureCount;
        /** Prepared bytes, or -1 when the compatibility engine cannot report them. */
        public final long preparedBytes;
        public CapturedFeatures(Callable<float[]> primary, Callable<float[]> verification) {
            this(primary, verification, verification == null ? 1 : 2, -1);
        }
        public CapturedFeatures(Callable<float[]> primary, Callable<float[]> verification, int captureCount, long preparedBytes) {
            this.primary = primary;
            this.verification = verification;
            this.captureCount = captureCount;
            this.preparedBytes = preparedBytes;
        }
    }

    /**
     * Liveness that runs off the analysis thread, in parallel with feature extraction (ArcSoft's
     * FL thread). Copies what it needs from {@code frame} now; the task returns the real-face
     * probability, NaN on failure. A {@link LivenessScorer} that also implements this runs async.
     */
    public interface LivenessEngine {
        Callable<Float> captureLiveness(CameraFrame frame, FaceRect face);
    }

    /**
     * Optional second stage. The first-stage score decides alone when it is clearly high
     * ({@code accept}) or clearly low ({@code reject}); in between, the slower, more accurate
     * model is run on the same capture and decides against its own gallery at {@code threshold}.
     * With MobileFaceNet first and IResNet-50 second this keeps IResNet-50 accuracy while most
     * probes pay only MobileFaceNet's cost (LFW all pairs: 99.56% vs 99.57% TAR at FAR 1e-5,
     * 87% of genuine pairs decided by the first stage).
     */
    public static final class Verifier<T> {
        final FeatureEngine features;
        final GallerySource<T> gallery;
        final float accept, reject, threshold;

        public Verifier(FeatureEngine features, GallerySource<T> gallery, float accept, float reject, float threshold) {
            if (!(reject < accept)) throw new IllegalArgumentException("reject must be below accept");
            this.features = features;
            this.gallery = gallery;
            this.accept = accept;
            this.reject = reject;
            this.threshold = threshold;
        }
    }

    public interface GallerySource<T> {
        /** Current snapshot; never null (use {@link Gallery#empty()}). */
        Gallery<T> gallery();
        /**
         * Override for coordinated publications. Must be a lock-free immutable snapshot read,
         * never enter a dataset writer/listener or run a model. Legacy/lambda sources use identity.
         */
        default GallerySnapshot<T> snapshot() { return new GallerySnapshot<>(gallery(), null, 0, true); }
    }

    public static final class GallerySnapshot<T> {
        public final Gallery<T> gallery;
        public final DatasetEpoch epoch;
        public final long generation;
        public final boolean ready;
        public GallerySnapshot(Gallery<T> gallery, DatasetEpoch epoch, long generation, boolean ready) {
            this.gallery = gallery;
            this.epoch = epoch;
            this.generation = generation;
            this.ready = ready;
        }
        boolean current() { return ready && (epoch == null || epoch.isCurrent(generation)); }
        boolean same(GallerySnapshot<?> other) {
            return other != null && gallery == other.gallery && epoch == other.epoch
                    && generation == other.generation && ready == other.ready;
        }
    }

    public interface Listener<T> {
        /** Every frame: all live tracks, for drawing. */
        void onFaces(List<FaceState<T>> faces, CameraFrame frame);

        /** A track was recognised. Not repeated for the same person within the identity cooldown. */
        void onRecognized(RecognitionEvent<T> event);

        /** A track used up its attempts without a match; it backs off and tries again later. */
        void onRecognitionFailed(long trackId, float bestScore, Reason reason);

        /** One applied, finite liveness vote, including UNKNOWN while the vote window fills. */
        default void onLiveness(long trackId, float score, Liveness verdict) { }

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
        /** Authorization provenance; asynchronous consumers must revalidate the dataset epoch. */
        public final long generation, attemptId, frameId, datasetGeneration;

        RecognitionEvent(long trackId, T user, float score, float secondScore, float livenessScore, int fusedFrames, long timestampMs, TaskToken<?> token) {
            generation = token.generation;
            attemptId = token.attemptId;
            frameId = token.frameId;
            datasetGeneration = token.primary.generation;
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
        long attemptId = 1;
        boolean visible, eligible;
        TaskToken<T> token;
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
        /** Cascade: second-stage task captured with the first one, run only when needed. */
        Callable<float[]> verifyTask;
        boolean verifying;

        TrackState(long id) { this.id = id; }
    }

    /** Immutable identities and the exact pair of searched gallery snapshots. */
    private static final class TaskToken<T> {
        final long generation, trackId, attemptId, frameId;
        final GallerySnapshot<T> primary, verification;
        TaskToken(long generation, long trackId, long attemptId, long frameId,
                  GallerySnapshot<T> primary, GallerySnapshot<T> verification) {
            this.generation = generation; this.trackId = trackId; this.attemptId = attemptId;
            this.frameId = frameId; this.primary = primary; this.verification = verification;
        }
    }

    private final SessionTasks tasks = new SessionTasks();
    private long generation = 1, frameId;
    private volatile boolean inputStopped, drainFailed;
    private boolean analysing, hasAnalysed, closeCleaned;
    private final ArrayDeque<Runnable> notifications = new ArrayDeque<>();
    private GallerySnapshot<T> observedPrimary, observedVerification;
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
    private Verifier<T> verifier;
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

    /** Adds the cascade's second stage; call before the first frame. */
    public FaceSession<T> withVerifier(Verifier<T> verifier) {
        synchronized (lock) {
            if (hasAnalysed || inputStopped || closed) throw new IllegalStateException("Session already started");
            this.verifier = verifier;
            return this;
        }
    }

    /** Next track id; persist it and pass it back as {@link SessionConfig#firstTrackId} after a restart. */
    public long nextTrackId() { synchronized (lock) { return tracker.nextId(); } }

    /** A hint only: does not consume admission or touch the camera buffer. */
    public boolean shouldProcessFrame(long timestampMs) {
        synchronized (lock) { return admits(timestampMs); }
    }

    private boolean admits(long timestampMs) {
        if (closed || inputStopped || analysing) return false;
        if (!hasAnalysed || timestampMs < lastAnalysedAt) return true;
        if (timestampMs == lastAnalysedAt) return false; // never count a duplicate observation
        long elapsed = timestampMs - lastAnalysedAt;
        return !idle || elapsed < 0 || elapsed >= config.idleFrameIntervalMs;
    }

    public void onFrame(CameraFrame frame) {
        final long acceptedGeneration;
        long now = frame.timestampMs;
        synchronized (lock) {
            if (!admits(now)) return;
            // A camera clock rollback starts new observations, not an endless throttle.
            if (hasAnalysed && now < lastAnalysedAt) {
                resetLocked();
                lastReported.clear(); // cooldowns in the previous clock domain cannot be compared
            }
            refreshDataset();
            if (!tasks.reserveFrame()) return;
            acceptedGeneration = generation;
            analysing = true;
            hasAnalysed = true;
            frameId++;
            lastAnalysedAt = now;
            lastFrameMs = now;
            lastFrameNanos = System.nanoTime();
        }
        tasks.enter();
        try {
            List<Detection> detections = detector.detect(frame);
            List<FaceState<T>> faces;
            synchronized (lock) {
                if (closed || generation != acceptedGeneration) return;
                refreshDataset();
                faces = updateStates(tracker.update(detections), frame, now);
            }
            flushNotifications();
            listener.onFaces(faces, frame);
        } finally {
            synchronized (lock) { analysing = false; }
            try { flushNotifications(); }
            finally { tasks.leave(); tasks.complete(); }
        }
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
                if (state.visible) invalidate(state);
                state.visible = state.eligible = false;
                state.reason = Reason.LOST;
            } else {
                state.visible = true;
                Reason gate = gate(track, frame, visible, largest);
                state.reason = gate;
                state.eligible = gate == Reason.NONE;
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
        quality.measure(frame, track.rect);
        state.bestSharpness = Math.max(state.bestSharpness, quality.sharpness);
        Reason qualityReason = quality.brightness < config.minBrightness ? Reason.TOO_DARK
                : quality.brightness > config.maxBrightness ? Reason.TOO_BRIGHT
                : quality.sharpness < Math.max(config.minSharpness, config.relativeSharpness * state.bestSharpness) ? Reason.BLURRY
                : Reason.NONE;
        state.eligible = qualityReason == Reason.NONE;
        updateLiveness(state, track, frame, now);
        if (state.liveness == Liveness.NOT_ALIVE) state.reason = Reason.NOT_ALIVE;
        else if (qualityReason != Reason.NONE) state.reason = qualityReason;

        if (state.match == null && !state.extracting && !busy && now >= state.retryAt
                && state.liveness != Liveness.NOT_ALIVE && qualityReason == Reason.NONE) {
            if (!datasetReady() || observedPrimary.gallery.size() == 0) state.reason = Reason.GALLERY_EMPTY;
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
        final TaskToken<T> token = token(state);
        state.livenessPending = true;
        livenessBusy = true;
        execute(livenessWorker, () -> {
            float score = Float.NaN;
            try { if (canRun(token)) score = task.call(); }
            catch (Exception error) { /* no vote */ }
            finally {
                synchronized (lock) {
                    livenessBusy = false;
                    refreshDataset();
                    TrackState<T> current = valid(token);
                    if (current != null) {
                        current.livenessPending = false;
                        long t = now();
                        addVote(current, score, t);
                        decide(current, t);
                    }
                }
            }
        }, () -> { livenessBusy = false; state.livenessPending = false; });
    }

    /**
     * Records one liveness vote. Decides as soon as the outcome is certain: ALIVE once
     * {@code livenessRequired} votes in the window say so, NOT_ALIVE once enough say otherwise
     * that ALIVE can no longer be reached. The window need not fill first.
     */
    private void addVote(TrackState<T> state, float score, long now) {
        if (!Float.isFinite(score) || state.liveness == Liveness.ALIVE) return; // engine error: no vote
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
        Liveness verdict = state.liveness;
        notifications.add(() -> listener.onLiveness(state.id, score, verdict));
    }

    private void submit(TrackState<T> state, Track track, CameraFrame frame) {
        FaceRect crop = config.featureCropScale > 0
                ? track.rect.scaledClipped(config.featureCropScale, frame.uprightWidth(), frame.uprightHeight())
                : new FaceRect(0, 0, frame.uprightWidth(), frame.uprightHeight());
        final Callable<float[]> task;
        final TaskToken<T> token = token(state);
        try {
            if (config.shareFeatureCapture && verifier != null) {
                CapturedFeatures captured = features.capturePair(frame, track.rect, track.keypoints, crop, verifier.features);
                task = captured.primary;
                state.verifyTask = captured.verification;
            } else {
                task = features.capture(frame, track.rect, track.keypoints, crop);
                state.verifyTask = verifier == null ? null : verifier.features.capture(frame, track.rect, track.keypoints, crop);
            }
            if (task == null) throw new IllegalStateException("Null capture");
        } catch (RuntimeException error) {
            state.verifyTask = null;
            state.reason = Reason.FEATURE_ERROR;
            return;
        }
        state.token = token;
        busy = true;
        state.extracting = true;
        execute(worker, () -> {
            float[] feature = null;
            boolean failed = false;
            try { if (canRun(token)) feature = task.call(); }
            catch (Exception error) { failed = true; }
            finally {
                synchronized (lock) {
                    busy = false;
                    refreshDataset();
                    if (valid(token) != null) applyFeature(token, feature, failed, now());
                    resumeDecisions();
                }
            }
        }, () -> {
            busy = false;
            state.extracting = false;
            state.verifyTask = null;
            state.reason = Reason.FEATURE_ERROR;
        });
    }

    /** Count chains before submission; completion callbacks are part of the count. */
    private void execute(Executor executor, Runnable action, Runnable rejected) {
        tasks.reserve();
        java.util.concurrent.atomic.AtomicBoolean started = new java.util.concurrent.atomic.AtomicBoolean();
        try {
            executor.execute(() -> {
                started.set(true);
                tasks.enter();
                try { action.run(); }
                finally {
                    try { flushNotifications(); }
                    finally { tasks.leave(); tasks.complete(); }
                }
            });
        } catch (RejectedExecutionException error) {
            if (started.get()) throw error;
            tasks.complete();
            rejected.run();
        }
    }

    /** A feature came back (worker thread, lock held): fuse, search, decide if liveness is in. */
    private void applyFeature(TaskToken<T> token, float[] feature, boolean failed, long now) {
        TrackState<T> state = valid(token);
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
        Gallery.Match<T> match = token.primary.gallery.search(state.fusedSum);
        if (match == null) {
            attemptFailed(state, now, Reason.FEATURE_ERROR, Float.NaN); // dimension mismatch
            return;
        }
        state.match = match;
        if (state.liveness != Liveness.ALIVE)
            matchScored(state.id, match.score, match.secondScore, state.fusedCount, state.liveness, Reason.NOT_ALIVE);
        decide(state, now);
    }

    /** Runs when both a match and liveness are available, whichever arrived last. */
    private void decide(TrackState<T> state, long now) {
        Gallery.Match<T> m = state.match;
        if (m == null || valid(state.token) != state || !state.eligible || state.liveness != Liveness.ALIVE) return;
        if (verifier != null && busy && m.score >= verifier.reject
                && !(m.score >= verifier.accept && m.margin() >= config.topMargin)) return;
        state.match = null;
        if (verifier != null && m.best != null && !state.verifying) {
            if (m.score >= verifier.accept && m.margin() >= config.topMargin) {
                succeed(state, m, now);
            } else if (m.score < verifier.reject || state.verifyTask == null) {
                matchScored(state.id, m.score, m.secondScore, state.fusedCount, state.liveness, Reason.UNKNOWN_PERSON);
                attemptFailed(state, now, Reason.UNKNOWN_PERSON, m.score);
            } else {
                matchScored(state.id, m.score, m.secondScore, state.fusedCount, state.liveness, Reason.NEED_MORE_FRAMES);
                verify(state);
            }
            return;
        }
        Reason outcome = m.best == null ? Reason.GALLERY_EMPTY
                : m.score >= config.thresholdHigh && m.margin() >= config.topMargin ? Reason.NONE
                : m.score >= config.thresholdLow && state.fusedCount < config.fusionFrames ? Reason.NEED_MORE_FRAMES
                : Reason.UNKNOWN_PERSON;
        matchScored(state.id, m.score, m.secondScore, state.fusedCount, state.liveness, outcome);
        if (m.best == null) {
            attemptFailed(state, now, Reason.GALLERY_EMPTY, Float.NaN);
        } else if (m.score >= config.thresholdHigh && m.margin() >= config.topMargin) {
            succeed(state, m, now);
        } else if (m.score >= config.thresholdLow && state.fusedCount < config.fusionFrames) {
            state.score = m.score;
            state.reason = Reason.NEED_MORE_FRAMES; // borderline: average in the next good frame
        } else {
            attemptFailed(state, now, Reason.UNKNOWN_PERSON, m.score);
        }
    }

    private void succeed(TrackState<T> state, Gallery.Match<T> m, long now) {
        TaskToken<T> token = state.token;
        if (valid(token) != state || !state.eligible || state.liveness != Liveness.ALIVE) return;
        final boolean[] report = {false};
        Runnable commit = () -> {
            // Authorization linearization point, under the shared publication monitor.
            if (valid(token) != state || !state.eligible || state.liveness != Liveness.ALIVE) return;
            state.recognition = Recognition.SUCCEED;
            state.user = m.best;
            state.score = m.score;
            state.reason = Reason.NONE;
            state.verifyTask = null;
            Long last = lastReported.get(m.best);
            if (last == null || now - last >= config.identityCooldownMs) {
                lastReported.put(m.best, now);
                report[0] = true;
            }
        };
        tasks.authorize(() -> {
            if (token.primary.epoch == null) commit.run();
            else token.primary.epoch.commit(token.primary.generation, commit);
        });
        // No user code under the dataset barrier. An asynchronous access consumer must
        // revalidate the event epoch at its own consumption/door-operation point.
        if (report[0]) {
            RecognitionEvent<T> event = new RecognitionEvent<>(state.id, m.best, m.score, m.secondScore,
                    state.livenessScore, state.fusedCount, now, token);
            notifications.add(() -> {
                synchronized (lock) {
                    TrackState<T> current = states.get(token.trackId);
                    if (closed || token.generation != generation || current == null || !current.visible
                            || current.attemptId != token.attemptId
                            || !snapshotsCurrent(token.primary, token.verification)) return;
                }
                listener.onRecognized(event);
            });
        }
    }

    /** Runs only the captured second-stage task, never adds an observation at EOF. */
    private void verify(TrackState<T> state) {
        final Callable<float[]> task = state.verifyTask;
        final TaskToken<T> token = state.token;
        if (valid(token) != state || task == null || busy) return;
        state.verifyTask = null;
        state.verifying = true;
        state.extracting = true;
        busy = true;
        execute(worker, () -> {
            float[] feature = null;
            try { if (canRun(token)) feature = task.call(); }
            catch (Exception error) { /* no feature */ }
            finally {
                synchronized (lock) {
                    busy = false;
                    refreshDataset();
                    TrackState<T> current = valid(token);
                    if (current != null) {
                        current.extracting = false;
                        current.verifying = false;
                        long t = now();
                        float[] unit = feature == null ? null : unit(feature);
                        Gallery.Match<T> m = unit == null ? null : token.verification.gallery.search(unit);
                        if (m == null || m.best == null) {
                            attemptFailed(current, t, Reason.NO_FEATURE, Float.NaN);
                        } else if (m.score >= verifier.threshold && m.margin() >= config.topMargin) {
                            matchScored(current.id, m.score, m.secondScore, 1, current.liveness, Reason.NONE);
                            succeed(current, m, t);
                        } else {
                            matchScored(current.id, m.score, m.secondScore, 1, current.liveness, Reason.UNKNOWN_PERSON);
                            attemptFailed(current, t, Reason.UNKNOWN_PERSON, m.score);
                        }
                    }
                    resumeDecisions();
                }
            }
        }, () -> {
            busy = false;
            state.extracting = false;
            state.verifying = false;
            state.reason = Reason.FEATURE_ERROR;
        });
    }

    private void resumeDecisions() {
        for (TrackState<T> state : new ArrayList<>(states.values())) decide(state, now());
    }

    private TaskToken<T> token(TrackState<T> state) {
        return new TaskToken<>(generation, state.id, state.attemptId, frameId, observedPrimary, observedVerification);
    }

    private boolean canRun(TaskToken<T> token) {
        synchronized (lock) { refreshDataset(); return valid(token) != null; }
    }

    private TrackState<T> valid(TaskToken<T> token) {
        if (closed || token == null || token.generation != generation || !snapshotsCurrent(token.primary, token.verification)) return null;
        TrackState<T> state = states.get(token.trackId);
        return state != null && state.visible && state.attemptId == token.attemptId
                && state.recognition == Recognition.PENDING ? state : null;
    }

    private boolean snapshotsCurrent(GallerySnapshot<T> primary, GallerySnapshot<T> verification) {
        if (primary == null || !primary.current() || !primary.same(gallery.snapshot())) return false;
        if (verifier == null) return verification == null;
        return verification != null && verification.current() && verification.same(verifier.gallery.snapshot())
                && primary.epoch == verification.epoch && primary.generation == verification.generation;
    }

    private boolean datasetReady() { return snapshotsCurrent(observedPrimary, observedVerification); }

    /** Never enters coordinator/listeners: only immutable snapshots and the short epoch monitor. */
    private void refreshDataset() {
        GallerySnapshot<T> primary = gallery.snapshot();
        GallerySnapshot<T> verification = verifier == null ? null : verifier.gallery.snapshot();
        if (observedPrimary == null || !observedPrimary.same(primary)
                || (observedVerification == null ? verification != null : !observedVerification.same(verification))
                || !snapshotsCurrent(primary, verification)) {
            for (TrackState<T> state : states.values()) invalidate(state);
            lastReported.clear();
            observedPrimary = primary;
            observedVerification = verification;
            idle = false;
        }
    }

    private void invalidate(TrackState<T> state) {
        state.attemptId++;
        state.token = null;
        state.match = null;
        state.verifyTask = null;
        state.extracting = state.verifying = state.livenessPending = false;
        state.fusedSum = null;
        state.fusedCount = state.tries = state.failedRounds = 0;
        state.recognition = Recognition.PENDING;
        state.user = null;
        state.score = Float.NaN;
        state.liveness = Liveness.UNKNOWN;
        state.livenessScore = Float.NaN;
        state.votes.clear();
        state.retryAt = state.livenessRetryAt = 0;
        state.bestSharpness = 0;
    }

    private void attemptFailed(TrackState<T> state, long now, Reason reason, float score) {
        if (closed || states.get(state.id) != state) return;
        state.attemptId++;
        state.livenessPending = false;
        state.tries++;
        state.verifyTask = null;
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
        float bestScore = state.score;
        notifications.add(() -> listener.onRecognitionFailed(state.id, bestScore, reason));
    }

    private void matchScored(long trackId, float score, float secondScore, int fusedFrames, Liveness liveness, Reason outcome) {
        notifications.add(() -> listener.onMatchScored(trackId, score, secondScore, fusedFrames, liveness, outcome));
    }

    private void flushNotifications() {
        // A direct executor is supported. Its caller will flush after releasing the outer lock.
        if (Thread.holdsLock(lock)) return;
        while (true) {
            Runnable callback;
            synchronized (lock) { callback = notifications.poll(); }
            if (callback == null) return;
            try { callback.run(); }
            catch (RuntimeException | Error failure) {
                // A throwing observer makes the run incomplete; never claim a successful drain.
                tasks.revoke();
                drainFailed = true;
                closed = true;
                synchronized (lock) { notifications.clear(); }
                throw failure;
            }
        }
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

    /** Forgets observations; worker slots stay occupied until their actual completion. */
    public void reset() { synchronized (lock) { resetLocked(); } }

    private void resetLocked() {
        generation++;
        tracker.reset();
        states.clear();
        notifications.clear();
        idle = false;
        hasAnalysed = false;
        lastAnalysedAt = Long.MIN_VALUE;
        // Never clear busy here: uninterruptible native work still owns its engine slot.
    }

    /**
     * Stop input and wait for accepted frames, submitted work, cascade descendants and callbacks.
     * Call off the UI/worker threads. False (timeout/interruption/self-wait) permanently revokes
     * this session; it says nothing about whether an external engine is safe to release.
     * True does not imply recognition: a missing liveness vote remains unresolved.
     */
    public boolean finish(long timeoutMs) {
        if (timeoutMs < 0) throw new IllegalArgumentException("negative timeout");
        inputStopped = true;
        tasks.stopInput();
        boolean complete = tasks.await(timeoutMs);
        if (!complete) {
            tasks.revoke();
            drainFailed = true;
            closed = true; // volatile revocation; do not block behind a native/capture/session lock
        }
        return complete && !drainFailed;
    }

    @Override
    public void close() {
        inputStopped = true;
        tasks.revoke();
        synchronized (lock) {
            if (!closeCleaned) { closed = true; resetLocked(); closeCleaned = true; }
            inputStopped = true;
        }
        // Queued wrappers must run their cancellation/finally path for reliable drain accounting.
        // The session owns neither injected engines nor injected executors.
        if (ownedWorker != null) ownedWorker.shutdown();
        if (ownedLivenessWorker != null) ownedLivenessWorker.shutdown();
    }
}
