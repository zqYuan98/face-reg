package com.bonuos.facesdk.engine;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotEquals;
import static org.junit.Assert.assertTrue;

import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import org.junit.Test;

public class FaceTrackerTest {
    private static Detection box(float x, float y, float size) {
        return new Detection(new FaceRect(x, y, x + size, y + size), 1f);
    }

    @Test public void idIsStableWhileTheFaceMoves() {
        FaceTracker tracker = new FaceTracker(0.3f, 3, 5, 0.5f, 100);
        long id = tracker.update(Collections.singletonList(box(0, 0, 100))).get(0).id;
        for (int i = 1; i < 10; i++) {
            List<Track> tracks = tracker.update(Collections.singletonList(box(i * 5, 0, 100)));
            assertEquals(1, tracks.size());
            assertEquals(id, tracks.get(0).id);
        }
        assertEquals(100, id);
    }

    @Test public void shortDropOutKeepsTheIdLongOneDoesNot() {
        FaceTracker tracker = new FaceTracker(0.3f, 3, 5, 0.5f, 1);
        long id = tracker.update(Collections.singletonList(box(0, 0, 100))).get(0).id;
        for (int i = 0; i < 3; i++) {
            List<Track> lost = tracker.update(Collections.<Detection>emptyList());
            assertEquals(1, lost.size());
            assertFalse(lost.get(0).visible());
        }
        assertEquals(id, tracker.update(Collections.singletonList(box(0, 0, 100))).get(0).id);
        for (int i = 0; i < 4; i++) tracker.update(Collections.<Detection>emptyList());
        assertNotEquals(id, tracker.update(Collections.singletonList(box(0, 0, 100))).get(0).id);
    }

    @Test public void twoFacesKeepTheirOwnIdsWhenDetectionOrderSwaps() {
        FaceTracker tracker = new FaceTracker(0.3f, 3, 5, 0.5f, 1);
        List<Track> first = tracker.update(Arrays.asList(box(0, 0, 100), box(300, 0, 100)));
        List<Track> second = tracker.update(Arrays.asList(box(305, 0, 100), box(5, 0, 100)));
        assertEquals(first.get(0).id, second.get(0).id);
        assertEquals(5f, second.get(0).rect().left, 0f);
        assertEquals(305f, second.get(1).rect().left, 0f);
    }

    @Test public void stabilityNeedsAFullStillHistory() {
        FaceTracker tracker = new FaceTracker(0.3f, 3, 5, 0.5f, 1);
        Track track = null;
        for (int i = 0; i < 4; i++) track = tracker.update(Collections.singletonList(box(0, 0, 100))).get(0);
        assertEquals(Float.POSITIVE_INFINITY, track.maxRecentMove(), 0f);
        track = tracker.update(Collections.singletonList(box(0, 0, 100))).get(0);
        assertEquals(0f, track.maxRecentMove(), 0f);
        track = tracker.update(Collections.singletonList(box(20, 0, 100))).get(0);
        assertEquals(20f, track.maxRecentMove(), 1e-4f);
        assertTrue(track.smoothedRect().left > 0 && track.smoothedRect().left < 20);
    }
}
