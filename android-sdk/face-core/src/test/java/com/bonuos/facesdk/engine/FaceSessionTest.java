package com.bonuos.facesdk.engine;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.Random;
import java.util.concurrent.Executor;
import org.junit.Before;
import org.junit.Test;

/** State machine tests with scripted engines and an injected clock (frame timestamps). */
public class FaceSessionTest {
    private static final int W = 320, H = 240;
    private static final float[] ALICE = {1, 0, 0, 0}, BOB = {0, 1, 0, 0};

    private final byte[] nv21 = new byte[W * H * 3 / 2];
    private List<Detection> faces = new ArrayList<>();
    private float livenessScore = 0.95f;
    private float[] nextFeature = ALICE;
    private final ArrayDeque<float[]> scriptedFeatures = new ArrayDeque<>();
    private int livenessCalls, extractions;
    private final List<FaceSession.RecognitionEvent<String>> recognized = new ArrayList<>();
    private final List<FaceSession.Reason> failures = new ArrayList<>();
    private List<FaceSession.FaceState<String>> lastFaces;
    private Gallery<String> gallery;
    private long now;
    private SessionConfig config;

    @Before public void setUp() {
        // Textured, mid-grey face region so brightness and sharpness gates pass.
        new Random(5).nextBytes(nv21);
        for (int i = 0; i < W * H; i++) nv21[i] = (byte) (64 + (nv21[i] & 0x7f));
        Arrays.fill(nv21, W * H, nv21.length, (byte) 128);
        gallery = Gallery.build(Arrays.asList(new Gallery.Entry<>("alice", ALICE), new Gallery.Entry<>("bob", BOB)), 4);
        config = new SessionConfig();
        config.minFaceSize = 40;
        config.identityCooldownMs = 5000;
        face(100, 60, 100);
    }

    private void face(float x, float y, float size) {
        faces = Collections.singletonList(new Detection(new FaceRect(x, y, x + size, y + size), 1f));
    }

    private FaceSession<String> session(Executor executor) {
        FaceSession.Detector detector = frame -> faces;
        FaceSession.LivenessScorer liveness = (frame, rect) -> { livenessCalls++; return livenessScore; };
        FaceSession.FeatureEngine features = (frame, face, keypoints, crop) -> {
            float[] feature = scriptedFeatures.isEmpty() ? nextFeature : scriptedFeatures.poll();
            return () -> { extractions++; return feature == null ? null : feature.clone(); };
        };
        return new FaceSession<>(config, detector, liveness, features, () -> gallery, new FaceSession.Listener<String>() {
            @Override public void onFaces(List<FaceSession.FaceState<String>> states, CameraFrame frame) { lastFaces = states; }
            @Override public void onRecognized(FaceSession.RecognitionEvent<String> event) { recognized.add(event); }
            @Override public void onRecognitionFailed(long trackId, float bestScore, FaceSession.Reason reason) { failures.add(reason); }
        }, executor);
    }

    private void frames(FaceSession<String> session, int count) {
        for (int i = 0; i < count; i++) {
            now += 33;
            session.onFrame(new CameraFrame(nv21, W, H, 0, now));
        }
    }

    @Test public void livePersonIsRecognisedOnceAndModelsStop() {
        FaceSession<String> session = session(Runnable::run);
        frames(session, 4);
        assertEquals("no model before the face is stable", 0, livenessCalls + extractions);
        frames(session, 60);
        assertEquals(1, recognized.size());
        assertEquals("alice", recognized.get(0).user);
        assertEquals(1, extractions);
        assertEquals("two passing votes decide; liveness then stops", 2, livenessCalls);
        assertEquals(FaceSession.Recognition.SUCCEED, lastFaces.get(0).recognition);
    }

    @Test public void spoofIsNeverRecognised() {
        livenessScore = 0.1f;
        FaceSession<String> session = session(Runnable::run);
        frames(session, 120);
        assertTrue(recognized.isEmpty());
        assertTrue("at most the one feature started in parallel with liveness", extractions <= 1);
        assertEquals(FaceSession.Liveness.NOT_ALIVE, lastFaces.get(0).liveness);
    }

    @Test public void borderlineFramesAreFusedIntoOneConfidentProbe() {
        // Each frame alone scores ~0.62 against alice (between low 0.60 and high 0.65); noise cancels in the mean.
        scriptedFeatures.add(new float[]{0.62f, 0, 0.785f, 0});
        scriptedFeatures.add(new float[]{0.62f, 0, -0.785f, 0});
        FaceSession<String> session = session(Runnable::run);
        frames(session, 30);
        assertEquals(1, recognized.size());
        assertEquals("alice", recognized.get(0).user);
        assertEquals(2, recognized.get(0).fusedFrames);
        assertTrue(recognized.get(0).score > 0.99f);
    }

    @Test public void unknownPersonBacksOffAfterRetries() {
        nextFeature = new float[]{0, 0, 1, 0};
        FaceSession<String> session = session(Runnable::run);
        frames(session, 10);
        assertEquals(Collections.singletonList(FaceSession.Reason.UNKNOWN_PERSON), failures);
        assertEquals(config.extractRetryCount, extractions);
        frames(session, 20); // inside the 1 s back-off
        assertEquals(config.extractRetryCount, extractions);
        frames(session, 40);
        assertTrue(extractions > config.extractRetryCount);
        assertTrue(recognized.isEmpty());
    }

    @Test public void samePersonOnANewTrackIsNotReportedTwiceWithinCooldown() {
        FaceSession<String> session = session(Runnable::run);
        frames(session, 20);
        faces = Collections.emptyList();
        frames(session, 10); // track dropped
        face(100, 60, 100);
        frames(session, 20);
        assertEquals(FaceSession.Recognition.SUCCEED, lastFaces.get(0).recognition);
        assertEquals(1, recognized.size());
        faces = Collections.emptyList();
        frames(session, 200); // > identity cooldown
        face(100, 60, 100);
        frames(session, 20);
        assertEquals(2, recognized.size());
    }

    @Test public void cheapFiltersRunBeforeAnyModel() {
        face(100, 60, 30); // smaller than minFaceSize
        FaceSession<String> session = session(Runnable::run);
        frames(session, 30);
        assertEquals(FaceSession.Reason.FACE_TOO_SMALL, lastFaces.get(0).reason);
        face(250, 60, 100); // sticks 30 px out of a 320 px frame
        frames(session, 30);
        assertEquals(FaceSession.Reason.BEYOND_BOUNDARY, lastFaces.get(0).reason);
        assertEquals(0, livenessCalls + extractions);
    }

    @Test public void featureForAFaceThatLeftIsDiscarded() {
        List<Runnable> queued = new ArrayList<>();
        FaceSession<String> session = session(queued::add);
        frames(session, 6);
        assertEquals(1, queued.size());
        faces = Collections.emptyList();
        frames(session, 5);
        queued.remove(0).run();
        frames(session, 2);
        assertTrue(recognized.isEmpty());
        assertTrue(lastFaces.isEmpty());
    }

    @Test public void onlyOneFeatureIsInFlightAtATime() {
        List<Runnable> queued = new ArrayList<>();
        faces = Arrays.asList(new Detection(new FaceRect(10, 10, 110, 110), 1f), new Detection(new FaceRect(200, 10, 300, 110), 1f));
        FaceSession<String> session = session(queued::add);
        frames(session, 20);
        assertEquals(1, queued.size());
        queued.remove(0).run();
        frames(session, 1);
        assertEquals(1, queued.size());
    }

    @Test public void emptyGalleryDoesNotRunTheRecognizer() {
        gallery = Gallery.empty();
        FaceSession<String> session = session(Runnable::run);
        frames(session, 30);
        assertEquals(0, extractions);
        assertEquals(FaceSession.Reason.GALLERY_EMPTY, lastFaces.get(0).reason);
    }

    @Test public void lumaQualityRanksSharpAboveFlat() {
        float[] flat = new float[LumaQuality.GRID * LumaQuality.GRID];
        Arrays.fill(flat, 100f);
        float[] textured = flat.clone();
        for (int i = 0; i < textured.length; i += 2) textured[i] = 140f;
        assertEquals(0f, LumaQuality.laplacianVariance(flat, LumaQuality.GRID, LumaQuality.GRID), 0f);
        assertTrue(LumaQuality.laplacianVariance(textured, LumaQuality.GRID, LumaQuality.GRID) > 100f);
    }

    private static float[] keypoints(float x, float y, float size, float noseShift) {
        float cx = x + size / 2, eyeY = y + size * 0.4f, mouthY = y + size * 0.75f;
        return new float[]{cx - size * 0.2f, eyeY, cx + size * 0.2f, eyeY, cx + noseShift * size, eyeY + 0.4609f * (mouthY - eyeY),
                cx - size * 0.15f, mouthY, cx + size * 0.15f, mouthY};
    }

    @Test public void detectorKeypointsGatePoseBeforeAnyModel() {
        faces = Collections.singletonList(new Detection(new FaceRect(100, 60, 200, 160), 1f, keypoints(100, 60, 100, 0.25f)));
        FaceSession<String> session = session(Runnable::run);
        frames(session, 30);
        assertEquals(FaceSession.Reason.FACE_ANGLE_TOO_LARGE, lastFaces.get(0).reason);
        assertEquals(0, livenessCalls + extractions);
        faces = Collections.singletonList(new Detection(new FaceRect(100, 60, 200, 160), 1f, keypoints(100, 60, 100, 0f)));
        frames(session, 20);
        assertEquals(1, recognized.size());
    }

    @Test public void idleSessionAnalysesFewerFrames() {
        int[] detections = {0};
        FaceSession<String> session = new FaceSession<>(config, frame -> { detections[0]++; return faces; },
                (frame, rect) -> livenessScore, (frame, face, keypoints, crop) -> () -> ALICE.clone(), () -> gallery,
                new FaceSession.Listener<String>() {
                    @Override public void onFaces(List<FaceSession.FaceState<String>> states, CameraFrame frame) { lastFaces = states; }
                    @Override public void onRecognized(FaceSession.RecognitionEvent<String> event) { recognized.add(event); }
                    @Override public void onRecognitionFailed(long trackId, float bestScore, FaceSession.Reason reason) { }
                }, Runnable::run);
        frames(session, 30); // recognised by frame ~7, then idle
        int busyAndIdle = detections[0];
        assertEquals(1, recognized.size());
        assertTrue("idle frames are skipped: " + busyAndIdle, busyAndIdle < 20);
        faces = Collections.emptyList();
        frames(session, 30);
        assertTrue(detections[0] - busyAndIdle <= 11);
    }

    @Test public void strangerBackOffGrows() {
        nextFeature = new float[]{0, 0, 1, 0};
        FaceSession<String> session = session(Runnable::run);
        for (int i = 0; i < 400 && failures.size() < 3; i++) frames(session, 1);
        assertEquals("rounds back off 1 s, then 2 s", 3, failures.size());
        int before = extractions;
        frames(session, 3 * 30); // the next back-off is 4 s
        assertEquals(before, extractions);
        frames(session, 2 * 30);
        assertTrue(extractions > before);
    }

    /** Liveness that runs on its own worker, like ScrfdEngines. */
    private final class AsyncLiveness implements FaceSession.LivenessScorer, FaceSession.LivenessEngine {
        @Override public float score(CameraFrame frame, FaceRect face) { throw new AssertionError("must run async"); }
        @Override public java.util.concurrent.Callable<Float> captureLiveness(CameraFrame frame, FaceRect face) {
            return () -> { livenessCalls++; return livenessScore; };
        }
    }

    @Test public void livenessAndFeatureRunInParallelAndTheLaterOneDecidesAtOnce() {
        List<Runnable> featureQueue = new ArrayList<>(), livenessQueue = new ArrayList<>();
        FaceSession<String> session = new FaceSession<>(config, frame -> faces, new AsyncLiveness(),
                (frame, face, keypoints, crop) -> () -> { extractions++; return ALICE.clone(); }, () -> gallery,
                new FaceSession.Listener<String>() {
                    @Override public void onFaces(List<FaceSession.FaceState<String>> states, CameraFrame frame) { lastFaces = states; }
                    @Override public void onRecognized(FaceSession.RecognitionEvent<String> event) { recognized.add(event); }
                    @Override public void onRecognitionFailed(long trackId, float bestScore, FaceSession.Reason reason) { }
                }, featureQueue::add, livenessQueue::add);
        frames(session, 6); // stable: one feature task and one liveness task are submitted together
        assertEquals(1, featureQueue.size());
        assertEquals(1, livenessQueue.size());
        featureQueue.remove(0).run();            // feature first: waits for liveness
        assertTrue(recognized.isEmpty());
        livenessQueue.remove(0).run();           // vote 1
        frames(session, 1);
        livenessQueue.remove(0).run();           // vote 2: ALIVE -> decides inside this call
        assertEquals(1, recognized.size());
        assertEquals(1, extractions);
        assertEquals(2, livenessCalls);
    }
}
