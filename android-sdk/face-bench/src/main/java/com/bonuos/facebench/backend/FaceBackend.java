package com.bonuos.facebench.backend;

import android.graphics.Bitmap;
import com.bonuos.facebench.clip.ClipFile;

/**
 * One engine under test. Every backend receives the same upright NV21 frames. Implementations
 * are used from a single thread and must time only their own work.
 */
public interface FaceBackend extends AutoCloseable {
    /** Stable identifier written to every CSV row, e.g. "arcsoft". */
    String name();

    /** Engine version and configuration, written to run.json. */
    String describe();

    /** Called before the first frame of each clip; resets any tracking state. */
    void beginClip(int sensorRotationDegrees) throws Exception;

    FrameOutcome process(ClipFile.Frame frame, boolean runLiveness, boolean runFeature) throws Exception;

    /** Extracts an enrollment feature from a registration photo; returns null when no usable face. */
    Object enroll(Bitmap photo) throws Exception;

    /** Similarity on this backend's own scale. Only compared against scores from the same backend. */
    float compare(Object probe, Object reference) throws Exception;

    @Override void close();
}
