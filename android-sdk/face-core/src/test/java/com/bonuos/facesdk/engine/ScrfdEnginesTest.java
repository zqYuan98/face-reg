package com.bonuos.facesdk.engine;

import static org.junit.Assert.*;
import java.util.*;
import java.util.concurrent.Callable;
import org.junit.Test;

/** The real Java adapter and tracker, with only the native model calls replaced. */
public class ScrfdEnginesTest {
    private static final float[] FACE = {60,50,160,150,.9f,90,80,130,80,110,100,95,125,125,125};
    private static final AnalysisProfile PROFILE = AnalysisProfile.forVariant("baseline");

    private static final class Analyzer implements ScrfdEngines.Analyzer {
        boolean accepts = true, closed;
        int full, roi, copies, threads = 2, longSide;
        float threshold, regionWidth;
        float[] fullResult = FACE, regionResult = FACE;
        RuntimeException copyFailure;
        public boolean setNv21(byte[] nv21,int width,int height,int rotation) {
            copies++;
            if (copyFailure != null) throw copyFailure;
            return accepts;
        }
        public float[] detect(int side,float score) {
            full++; longSide = side; threshold = score;
            return fullResult;
        }
        public float[] detectRegion(float x,float y,float w,float h,int side,float score) {
            roi++; longSide = side; threshold = score; regionWidth = w;
            return regionResult;
        }
        public float liveness(int left,int top,int right,int bottom) { return .95f; }
        public int threads() { return threads; }
        public void close() { closed = true; }
    }

    private static final class Live implements ScrfdEngines.LivenessWorker {
        boolean closed;
        byte[] captured;
        public float score(byte[] data,int width,int height,int rotation,int left,int top,int right,int bottom) {
            captured = data;
            return .95f;
        }
        public void close() { closed = true; }
    }

    private static CameraFrame frame(long timestamp) {
        byte[] nv21 = new byte[320 * 240 * 3 / 2];
        Arrays.fill(nv21,(byte)100);
        return new CameraFrame(nv21,320,240,0,timestamp);
    }

    private static void noCurrentFrame(ScrfdEngines engines, CameraFrame frame) {
        FaceRect box = new FaceRect(60,50,160,150);
        try { engines.score(frame,box); fail("failed/reset observation cannot be scored"); }
        catch (IllegalStateException expected) { }
        try { engines.captureLiveness(frame,box); fail("failed/reset observation cannot be captured"); }
        catch (IllegalStateException expected) { }
    }

    @Test public void emptyNativeArrayIsSuccessfulButNullIsAnError() {
        Analyzer nativeCalls = new Analyzer();
        try (ScrfdEngines engines = new ScrfdEngines(nativeCalls,new Live(),PROFILE)) {
            CameraFrame frame = frame(1);
            nativeCalls.fullResult = new float[0];
            assertTrue(engines.detect(frame).isEmpty());
            nativeCalls.fullResult = null;
            try { engines.detect(frame); fail("native null is not an empty scene"); }
            catch (IllegalStateException expected) { assertEquals("Native detector failed",expected.getMessage()); }
            noCurrentFrame(engines,frame);
            nativeCalls.fullResult = FACE;
            assertEquals(1,engines.detect(frame).size());
            assertEquals(3,nativeCalls.full); assertEquals(0,nativeCalls.roi);
        }
        assertTrue(nativeCalls.closed);
    }

    @Test public void rejectedInputRevokesCurrentFrameAndForcesNextFull() {
        Analyzer nativeCalls = new Analyzer();
        try (ScrfdEngines engines = new ScrfdEngines(nativeCalls,new Live(),PROFILE)) {
            CameraFrame old = frame(1), next = frame(2);
            engines.detect(old); nativeCalls.accepts = false;
            try { engines.detect(next); fail("input rejection must fail"); }
            catch (IllegalStateException expected) { assertEquals("Native analyzer rejected NV21 frame",expected.getMessage()); }
            noCurrentFrame(engines,old); noCurrentFrame(engines,next);
            assertEquals(1,nativeCalls.full); assertEquals(0,nativeCalls.roi);
            nativeCalls.accepts = true; engines.detect(next);
            assertEquals(2,nativeCalls.full); assertEquals(0,nativeCalls.roi);
        }
    }

    @Test public void copyExceptionAlsoResetsTrackerAndPreservesTheOriginalError() {
        Analyzer nativeCalls = new Analyzer();
        try (ScrfdEngines engines = new ScrfdEngines(nativeCalls,new Live(),PROFILE)) {
            CameraFrame frame = frame(1); engines.detect(frame);
            nativeCalls.copyFailure = new IllegalStateException("copy failed");
            try { engines.detect(frame); fail("must fail"); }
            catch (IllegalStateException error) { assertSame(nativeCalls.copyFailure,error); }
            noCurrentFrame(engines,frame);
            nativeCalls.copyFailure = null; engines.detect(frame);
            assertEquals(2,nativeCalls.full); assertEquals(0,nativeCalls.roi);
        }
    }

    @Test public void regionFailureIsNotAMissAndNextObservationIsFull() {
        Analyzer nativeCalls = new Analyzer();
        try (ScrfdEngines engines = new ScrfdEngines(nativeCalls,new Live(),PROFILE)) {
            CameraFrame frame = frame(1); engines.detect(frame);
            nativeCalls.regionResult = null;
            try { engines.detect(frame); fail("ROI failure must fail the observation"); }
            catch (IllegalStateException expected) { }
            noCurrentFrame(engines,frame);
            engines.detect(frame);
            assertEquals(2,nativeCalls.full); assertEquals(1,nativeCalls.roi);
        }
    }

    @Test public void malformedNativeOutputNeverPublishesPartialDetections() {
        List<float[]> invalid = new ArrayList<>();
        invalid.add(Arrays.copyOf(FACE,16));
        float[] nan = FACE.clone(); nan[5] = Float.NaN; invalid.add(nan);
        float[] infinite = FACE.clone(); infinite[0] = Float.POSITIVE_INFINITY; invalid.add(infinite);
        float[] inverted = FACE.clone(); inverted[2] = inverted[0]; invalid.add(inverted);
        float[] score = FACE.clone(); score[4] = 1.1f; invalid.add(score);
        float[] partial = Arrays.copyOf(FACE,30); partial[15 + 4] = Float.NaN; invalid.add(partial);
        for (float[] packed : invalid) {
            Analyzer nativeCalls = new Analyzer(); nativeCalls.fullResult = packed;
            try (ScrfdEngines engines = new ScrfdEngines(nativeCalls,new Live(),PROFILE)) {
                CameraFrame frame = frame(1);
                try { engines.detect(frame); fail("malformed output must fail"); }
                catch (IllegalStateException expected) { }
                noCurrentFrame(engines,frame);
                nativeCalls.fullResult = FACE;
                assertEquals(1,engines.detect(frame).size()); assertEquals(2,nativeCalls.full);
            }
        }
    }

    @Test public void profileDrivesActualCallsAndSettingsDescribeTheInstance() {
        Analyzer nativeCalls = new Analyzer();
        try (ScrfdEngines engines = new ScrfdEngines(nativeCalls,new Live(),PROFILE)) {
            engines.detect(frame(1));
            assertEquals(PROFILE.detectorLongSide,nativeCalls.longSide);
            assertEquals(PROFILE.detectorThreshold,nativeCalls.threshold,0f);
            engines.detect(frame(2));
            assertEquals(PROFILE.regionLongSide,nativeCalls.longSide);
            assertEquals(100f * PROFILE.regionScale,nativeCalls.regionWidth,0f);
            for (int i = 2; i <= PROFILE.fullScanEvery; i++) engines.detect(frame(i + 1));
            assertEquals(2,nativeCalls.full); assertEquals(PROFILE.fullScanEvery - 1,nativeCalls.roi);
            Map<String,Object> effective = engines.effectiveSettings();
            assertEquals(PROFILE.detectorLongSide,effective.get("longSide"));
            assertEquals(PROFILE.detectorThreshold,effective.get("threshold"));
            assertEquals(PROFILE.regionLongSide,effective.get("regionLongSide"));
            assertEquals(PROFILE.regionScale,effective.get("regionScale"));
            assertEquals(PROFILE.regionMinIou,effective.get("regionMinIou"));
            assertEquals(PROFILE.fullScanEvery,effective.get("fullScanEvery"));
            assertEquals(nativeCalls.threads(),effective.get("detectorThreads"));
            assertFalse("not a measurement claim",effective.containsKey("timingLevel"));
            try { effective.put("longSide",1); fail("settings must be immutable"); }
            catch (UnsupportedOperationException expected) { }
        }
    }

    @Test public void injectedEngineCannotMisreportProfileThreads() {
        Analyzer nativeCalls = new Analyzer(); nativeCalls.threads = 1;
        try { new ScrfdEngines(nativeCalls,new Live(),PROFILE); fail("mismatched instance must fail"); }
        catch (IllegalArgumentException expected) { }
    }

    @Test public void capturedLivenessOwnsItsPixelsAndResetStopsNewCaptures() throws Exception {
        Analyzer nativeCalls = new Analyzer(); Live live = new Live();
        try (ScrfdEngines engines = new ScrfdEngines(nativeCalls,live,PROFILE)) {
            CameraFrame frame = frame(1); engines.detect(frame);
            Callable<Float> task = engines.captureLiveness(frame,new FaceRect(60,50,160,150));
            frame.nv21[0] = 0; engines.reset();
            noCurrentFrame(engines,frame);
            assertEquals(.95f,task.call(),0f);
            assertNotSame(frame.nv21,live.captured); assertEquals(100,live.captured[0]);
        }
        assertTrue(live.closed);
    }

    @Test public void nativeNullReachesSessionRevocationAndOldWorkerCannotAuthorize() {
        Analyzer nativeCalls = new Analyzer();
        try (ScrfdEngines engines = new ScrfdEngines(nativeCalls,new Live(),PROFILE)) {
            SessionConfig config = new SessionConfig();
            config.stableFrames = 2; config.minFaceSize = 20; config.relativeSharpness = 0;
            config.livenessEnabled = false; config.idleFrameIntervalMs = 0;
            config.maxYaw = config.maxPitch = 180;
            List<Runnable> tasks = new ArrayList<>();
            List<FaceSession.RecognitionEvent<String>> events = new ArrayList<>();
            Gallery<String> gallery = Gallery.build(Collections.singletonList(new Gallery.Entry<>("alice",new float[]{1,0})),2);
            FaceSession.Listener<String> listener = new FaceSession.Listener<String>() {
                public void onFaces(List<FaceSession.FaceState<String>> faces,CameraFrame frame) { }
                public void onRecognized(FaceSession.RecognitionEvent<String> event) { events.add(event); }
                public void onRecognitionFailed(long id,float score,FaceSession.Reason reason) { }
            };
            try (FaceSession<String> session = new FaceSession<>(config,engines,engines,
                    (f,r,k,c) -> () -> new float[]{1,0}, () -> gallery,listener,tasks::add)) {
                session.onFrame(frame(1)); session.onFrame(frame(2));
                assertEquals(1,tasks.size());
                nativeCalls.regionResult = null;
                try { session.onFrame(frame(3)); fail("production adapter must expose failure to session"); }
                catch (IllegalStateException expected) { }
                session.onFrame(frame(4));
                assertEquals("old task retains its busy slot",1,tasks.size());
                tasks.remove(0).run(); assertTrue(events.isEmpty());
                nativeCalls.regionResult = FACE;
                session.onFrame(frame(5)); session.onFrame(frame(6));
                assertEquals(1,tasks.size()); tasks.remove(0).run();
                assertEquals(1,events.size()); assertTrue(session.finish(0));
            }
        }
    }
}
