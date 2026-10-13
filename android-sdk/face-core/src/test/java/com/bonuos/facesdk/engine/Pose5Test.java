package com.bonuos.facesdk.engine;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class Pose5Test {
    // Frontal template (eyes, nose, mouth corners) with the nose at the neutral pitch height.
    private static float[] face(float noseShiftX, float rollDegrees) {
        float[] p = {-30, 0, 30, 0, noseShiftX, 0.4609f * 60, -25, 60, 25, 60};
        double r = Math.toRadians(rollDegrees), c = Math.cos(r), s = Math.sin(r);
        for (int k = 0; k < 5; k++) {
            float x = p[2 * k], y = p[2 * k + 1];
            p[2 * k] = (float) (x * c - y * s) + 200;
            p[2 * k + 1] = (float) (x * s + y * c) + 150;
        }
        return p;
    }

    @Test public void frontalFaceIsZeroInAnyRoll() {
        for (float roll : new float[]{0, 20, -35}) {
            Pose5 pose = Pose5.estimate(face(0, roll));
            assertEquals(0f, pose.yaw, 1e-3f);
            assertEquals(0f, pose.pitch, 1e-3f);
            assertEquals(roll, pose.roll, 1e-3f);
        }
    }

    @Test public void noseOffTheMidlineGivesYawIndependentOfRoll() {
        float yaw = Pose5.estimate(face(15, 0)).yaw;
        assertEquals(-72.8f * 15 / 60, yaw, 1e-3f);
        assertEquals(yaw, Pose5.estimate(face(15, 25)).yaw, 1e-3f);
        assertTrue(Pose5.estimate(face(-15, 0)).yaw > 0);
    }

    @Test public void degenerateLandmarksAreNaN() {
        assertTrue(Float.isNaN(Pose5.estimate(new float[10]).yaw));
    }
}
