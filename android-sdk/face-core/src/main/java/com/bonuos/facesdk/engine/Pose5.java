package com.bonuos.facesdk.engine;

/**
 * Head pose from the five landmarks, replacing the two FSANet models the prebuilt library ran on
 * every call. Roll is the eye-line angle; yaw and pitch come from where the nose sits relative to
 * the eye and mouth midpoints, mapped to degrees by a linear fit against FSANet. Good enough for
 * a frontal-face gate, not for measuring angles.
 */
public final class Pose5 {
    // Least-squares fit to FSANet (the models this replaces) on 1649 LFW + HardHat faces, 2026-10-13:
    // yaw corr 0.82 / MAE 4.3 deg, pitch corr 0.73 / MAE 3.8 deg; a 30/25 deg frontal gate agrees
    // with FSANet's on 97.3% of faces. Signs follow FSANet.
    static final float YAW_PER_RATIO = -72.8f, PITCH_PER_RATIO = -53.7f, PITCH_NEUTRAL = 0.4609f;

    public final float yaw, pitch, roll;

    private Pose5(float yaw, float pitch, float roll) {
        this.yaw = yaw;
        this.pitch = pitch;
        this.roll = roll;
    }

    /** @param p left eye, right eye, nose, left mouth, right mouth as x0,y0..x4,y4 */
    public static Pose5 estimate(float[] p) {
        double ex = p[2] - p[0], ey = p[3] - p[1];
        double iod = Math.hypot(ex, ey);
        if (!(iod > 0)) return new Pose5(Float.NaN, Float.NaN, Float.NaN);
        double roll = Math.atan2(ey, ex);
        double cos = Math.cos(-roll), sin = Math.sin(-roll);
        double ox = (p[0] + p[2]) / 2, oy = (p[1] + p[3]) / 2; // eye midpoint, rotation origin
        double[] r = new double[10];
        for (int k = 0; k < 5; k++) {
            double dx = p[2 * k] - ox, dy = p[2 * k + 1] - oy;
            r[2 * k] = dx * cos - dy * sin;
            r[2 * k + 1] = dx * sin + dy * cos;
        }
        // De-rolled frame: eye midpoint at the origin, eyes on the x axis.
        double mouthX = (r[6] + r[8]) / 2, mouthY = (r[7] + r[9]) / 2;
        if (!(mouthY > 0)) return new Pose5(Float.NaN, Float.NaN, (float) Math.toDegrees(roll));
        double midlineX = mouthX * (r[5] / mouthY); // midline x at the nose height
        double yawRatio = (r[4] - midlineX) / iod;
        double pitchRatio = r[5] / mouthY;
        return new Pose5((float) (yawRatio * YAW_PER_RATIO), (float) ((pitchRatio - PITCH_NEUTRAL) * PITCH_PER_RATIO),
                (float) Math.toDegrees(roll));
    }
}
