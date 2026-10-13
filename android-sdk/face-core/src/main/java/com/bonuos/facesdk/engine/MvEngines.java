package com.bonuos.facesdk.engine;

import android.content.Context;
import com.mv.engine.FaceBox;
import com.mv.engine.FaceDetector;
import com.mv.engine.Live;
import com.mv.engine.UprightFrame;
import java.util.ArrayList;
import java.util.List;

/**
 * The bundled minivision detector and silent-liveness models behind the session interfaces.
 * Each frame is converted to upright BGR once in native code and shared by detection and both
 * liveness models (previously: a Java rotation plus two separate native conversions per frame).
 * Use from the analysis thread only.
 */
public final class MvEngines implements FaceSession.Detector, FaceSession.LivenessScorer, AutoCloseable {
    private final FaceDetector detector = new FaceDetector();
    private final Live live = new Live();
    private final UprightFrame upright = new UprightFrame();
    private CameraFrame current;

    public MvEngines(Context context) {
        try {
            if (detector.loadModel(context.getAssets()) != 0) throw new IllegalStateException("Face detector model failed to load");
            if (live.loadModel(context.getAssets()) != 0) throw new IllegalStateException("Liveness model failed to load");
        } catch (RuntimeException error) {
            close();
            throw error;
        }
    }

    @Override
    public List<Detection> detect(CameraFrame frame) {
        upright.setNv21(frame.nv21, frame.width, frame.height, frame.rotationDegrees);
        current = frame;
        List<FaceBox> boxes = detector.detect(upright);
        List<Detection> out = new ArrayList<>(boxes.size());
        // Native boxes are inclusive; FaceRect's right/bottom are exclusive.
        for (FaceBox box : boxes)
            out.add(new Detection(new FaceRect(box.getLeft(), box.getTop(), box.getRight() + 1, box.getBottom() + 1), box.getConfidence()));
        return out;
    }

    @Override
    public float score(CameraFrame frame, FaceRect face) {
        if (frame != current) throw new IllegalStateException("score() must follow detect() on the same frame");
        FaceBox box = new FaceBox(Math.round(face.left), Math.round(face.top),
                Math.round(face.right) - 1, Math.round(face.bottom) - 1, 0f);
        return live.detect(upright, box);
    }

    @Override
    public void close() {
        live.destroy();
        detector.destroy();
        upright.close();
    }
}
