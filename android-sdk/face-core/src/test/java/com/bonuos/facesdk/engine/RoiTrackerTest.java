package com.bonuos.facesdk.engine;

import static org.junit.Assert.*;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import org.junit.Test;

public class RoiTrackerTest {
    /** A scripted scene: the faces currently in frame; region() returns those inside the region. */
    private static final class Scene implements RoiTracker.Source {
        List<Detection> faces = new ArrayList<>();
        int full, region, failRegionAt;
        boolean nullFull, nullRegion;
        RuntimeException fullFailure;
        final RuntimeException regionFailure = new IllegalStateException("region failed");
        final List<FaceRect> regions = new ArrayList<>();

        @Override public List<Detection> full() {
            full++;
            if (fullFailure != null) throw fullFailure;
            return nullFull ? null : new ArrayList<>(faces);
        }

        @Override public List<Detection> region(FaceRect r) {
            region++;
            if (region == failRegionAt) throw regionFailure;
            if (nullRegion) return null;
            regions.add(r);
            List<Detection> out = new ArrayList<>();
            for (Detection d : faces)
                if (d.rect.left >= r.left && d.rect.right <= r.right && d.rect.top >= r.top && d.rect.bottom <= r.bottom) out.add(d);
            return out;
        }
    }

    private static Detection face(float x, float y, float size) {
        return new Detection(new FaceRect(x, y, x + size, y + size), 0.9f);
    }

    @Test public void scansWholeFrameEveryNthFrameAndFollowsFacesInBetween() {
        Scene scene = new Scene();
        scene.faces.add(face(100, 100, 100));
        RoiTracker tracker = new RoiTracker(6, 2f, 0.2f);
        for (int i = 0; i < 12; i++) {
            scene.faces.set(0, face(100 + 3 * i, 100, 100)); // slow drift stays inside the region
            assertEquals(1, tracker.next(scene).size());
        }
        assertEquals(2, scene.full);
        assertEquals(10, scene.region);
        FaceRect r = scene.regions.get(0);
        assertEquals(200f, r.width(), 1e-3f); // 2x the face side, centred on the last box
        assertEquals(150f, r.centerX(), 1e-3f);
    }

    @Test public void lostFaceForcesAWholeFrameScanNextFrame() {
        Scene scene = new Scene();
        scene.faces.add(face(100, 100, 100));
        RoiTracker tracker = new RoiTracker(10, 2f, 0.2f);
        tracker.next(scene);                          // full
        scene.faces.set(0, face(600, 100, 100));      // jumped out of the region
        assertTrue(tracker.next(scene).isEmpty());    // region misses it this frame
        assertEquals(1, tracker.next(scene).size());  // whole-frame scan finds it again
        assertEquals(2, scene.full);
    }

    @Test public void newFaceIsFoundAtTheNextScheduledScan() {
        Scene scene = new Scene();
        scene.faces.add(face(100, 100, 100));
        RoiTracker tracker = new RoiTracker(3, 2f, 0.2f);
        tracker.next(scene);
        scene.faces.add(face(500, 100, 100));
        assertEquals(1, tracker.next(scene).size());
        assertEquals(1, tracker.next(scene).size());
        assertEquals(2, tracker.next(scene).size()); // third frame: scheduled whole-frame scan
    }

    @Test public void failedScheduledFullDiscardsOldRegionsAndRetriesFull() {
        Scene scene = new Scene(); scene.faces.add(face(100,100,100));
        RoiTracker tracker = new RoiTracker(2,2f,.2f);
        tracker.next(scene); tracker.next(scene);
        RuntimeException failure = new IllegalStateException("full failed");
        scene.fullFailure = failure;
        try { tracker.next(scene); fail("must propagate"); }
        catch (RuntimeException error) { assertSame(failure,error); }
        scene.fullFailure = null;
        scene.faces.set(0,face(600,100,100));
        assertEquals(600f,tracker.next(scene).get(0).rect.left,0f);
        assertEquals(3,scene.full); assertEquals(1,scene.region);
        assertEquals(3,tracker.fullScans()); assertEquals(1,tracker.regionScans());
    }

    @Test public void laterRegionFailureRejectsTheWholeObservationAndForcesFull() {
        Scene scene = new Scene();
        scene.faces.add(face(100,100,100)); scene.faces.add(face(500,100,100));
        RoiTracker tracker = new RoiTracker(10,2f,.2f);
        tracker.next(scene); scene.failRegionAt = 2;
        try { tracker.next(scene); fail("must not return the first successful ROI as a partial frame"); }
        catch (RuntimeException error) { assertSame(scene.regionFailure,error); }
        scene.faces.clear(); scene.faces.add(face(900,100,100));
        assertEquals(900f,tracker.next(scene).get(0).rect.left,0f);
        assertEquals(2,scene.full); assertEquals(2,scene.region);
    }

    @Test public void nullFullAndRegionResultsAreErrorsNotEmptyScenes() {
        for (boolean full : new boolean[]{true,false}) {
            Scene scene = new Scene(); scene.faces.add(face(100,100,100));
            RoiTracker tracker = new RoiTracker(10,2f,.2f);
            if (full) scene.nullFull = true;
            else { tracker.next(scene); scene.nullRegion = true; }
            try { tracker.next(scene); fail("null must fail"); }
            catch (IllegalStateException expected) { assertEquals("Detector returned null",expected.getMessage()); }
            scene.nullFull = scene.nullRegion = false;
            assertEquals(1,tracker.next(scene).size()); assertEquals(2,scene.full);
        }
    }

    @Test public void sourceErrorAlsoInvalidatesOldRegions() {
        Scene scene = new Scene(); scene.faces.add(face(100,100,100));
        RoiTracker tracker = new RoiTracker(10,2f,.2f); tracker.next(scene);
        AssertionError failure = new AssertionError("native failure");
        try {
            tracker.next(new RoiTracker.Source() {
                public List<Detection> full() { throw new AssertionError("expected ROI"); }
                public List<Detection> region(FaceRect r) { throw failure; }
            });
            fail("must propagate Error");
        } catch (AssertionError error) { assertSame(failure,error); }
        tracker.next(scene); assertEquals(2,scene.full); assertEquals(0,scene.region);
    }

    @Test public void emptySceneScansEveryFrameAndResetRescans() {
        Scene scene = new Scene();
        RoiTracker tracker = new RoiTracker(6, 2f, 0.2f);
        for (int i = 0; i < 3; i++) assertTrue(tracker.next(scene).isEmpty());
        assertEquals(3, scene.full);
        scene.faces = new ArrayList<>(Collections.singletonList(face(100, 100, 100)));
        tracker.next(scene);
        tracker.reset();
        tracker.next(scene);
        assertEquals(0, scene.region);
    }
}
