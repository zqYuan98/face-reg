package com.bonuos.facebench.backend;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.ImageFormat;
import android.graphics.Rect;
import android.graphics.YuvImage;
import com.bonuos.facebench.clip.ClipFile;
import com.bonuos.facesdk.image.Nv21;
import com.facesdk.wrapper.FaceSDKWrapper;
import com.mv.engine.FaceBox;
import com.mv.engine.FaceDetector;
import com.mv.engine.Live;
import java.io.ByteArrayOutputStream;
import java.util.List;

/**
 * The current SDK: minivision silent liveness plus the prebuilt recognition library.
 *
 * <p>{@code asIs=true} reproduces the shipped app: the frame stays in sensor orientation, goes
 * through a quality-100 JPEG round trip into a Bitmap, and liveness re-encodes that Bitmap to NV21.
 * {@code asIs=false} is the stage-0 fix: upright NV21 straight from the camera.
 */
public final class LegacyBackend implements FaceBackend {
    /** Threshold the shipped app uses; not calibrated. */
    public static final float DEFAULT_LIVENESS_THRESHOLD = 0.7f;

    private final boolean asIs;
    private final FaceDetector detector = new FaceDetector();
    private final Live live = new Live();
    private final FaceSDKWrapper recognizer = new FaceSDKWrapper();
    private int sensorRotation;
    private int[] pixels;
    private Bitmap bitmap;

    public LegacyBackend(Context context, boolean asIs) {
        this.asIs = asIs;
        if (detector.loadModel(context.getAssets()) != 0) throw new IllegalStateException("Liveness face detector failed to load");
        if (live.loadModel(context.getAssets()) != 0) throw new IllegalStateException("Liveness model failed to load");
        if (!recognizer.init(context)) throw new IllegalStateException("Recognition library failed to initialise");
    }

    @Override public String name() { return asIs ? "legacy_asis" : "legacy_fixed"; }

    @Override public String describe() {
        return (asIs ? "current app input path (JPEG round trip, sensor orientation)" : "upright NV21 input, no JPEG")
                + "; liveness=minivision MiniFASNet x2 (ncnn, 2 threads); recognition=prebuilt libface_sdk_jni (1024-d)"
                + "; default liveness threshold " + DEFAULT_LIVENESS_THRESHOLD;
    }

    @Override public void beginClip(int sensorRotationDegrees) { sensorRotation = sensorRotationDegrees; }

    @Override
    public FrameOutcome process(ClipFile.Frame frame, boolean runLiveness, boolean runFeature) {
        return asIs ? processAsIs(frame, runLiveness, runFeature) : processFixed(frame, runLiveness, runFeature);
    }

    private FrameOutcome processFixed(ClipFile.Frame frame, boolean runLiveness, boolean runFeature) {
        FrameOutcome outcome = new FrameOutcome();
        Timer timer = Timer.start();
        List<FaceBox> boxes = detector.detect(frame.nv21, frame.width, frame.height, 1);
        outcome.detectMs = timer.lapMs();
        FaceBox box = record(outcome, boxes);
        if (box != null && runLiveness) {
            timer.lapMs();
            float score = live.detect(frame.nv21, frame.width, frame.height, 1, box);
            outcome.livenessMs = timer.lapMs();
            setScore(outcome, score);
        }
        if (runFeature) {
            outcome.featureAttempted = true;
            timer.lapMs();
            Bitmap upright = uprightBitmap(frame);
            outcome.preprocessMs = timer.lapMs();
            outcome.feature = recognizer.extractFeature(upright);
            outcome.featureMs = timer.lapMs();
            if (outcome.feature == null) outcome.note = "no usable face for recognition";
        }
        return outcome;
    }

    private FrameOutcome processAsIs(ClipFile.Frame frame, boolean runLiveness, boolean runFeature) {
        FrameOutcome outcome = new FrameOutcome();
        int back = (360 - sensorRotation) % 360;
        byte[] sensorFrame = Nv21.rotate(frame.nv21, frame.width, frame.height, back);
        int width = back % 180 == 0 ? frame.width : frame.height;
        int height = back % 180 == 0 ? frame.height : frame.width;

        Timer timer = Timer.start();
        Bitmap jpegBitmap = jpegRoundTrip(sensorFrame, width, height);
        byte[] reEncoded = runLiveness ? bitmapToNv21(jpegBitmap) : null;
        outcome.preprocessMs = timer.lapMs();
        if (jpegBitmap == null) {
            outcome.note = "JPEG decode failed";
            return outcome;
        }
        List<FaceBox> boxes = detector.detect(jpegBitmap);
        outcome.detectMs = timer.lapMs();
        FaceBox box = record(outcome, boxes);
        if (box != null) outcome.note = "box in sensor orientation";
        if (box != null && runLiveness) {
            timer.lapMs();
            float score = live.detect(reEncoded, width, height, 0, box);
            outcome.livenessMs = timer.lapMs();
            setScore(outcome, score);
        }
        if (runFeature) {
            outcome.featureAttempted = true;
            timer.lapMs();
            outcome.feature = recognizer.extractFeature(jpegBitmap);
            outcome.featureMs = timer.lapMs();
            if (outcome.feature == null) outcome.note = "no usable face for recognition";
        }
        return outcome;
    }

    private static FaceBox record(FrameOutcome outcome, List<FaceBox> boxes) {
        outcome.faceCount = boxes == null ? 0 : boxes.size();
        if (outcome.faceCount == 0) {
            outcome.livenessStatus = FrameOutcome.LIVE_UNKNOWN;
            return null;
        }
        FaceBox box = boxes.get(0); // detector sorts by area, largest first
        outcome.box = new int[]{box.getLeft(), box.getTop(), box.getRight(), box.getBottom()};
        return box;
    }

    private static void setScore(FrameOutcome outcome, float score) {
        outcome.livenessScore = score;
        outcome.livenessStatus = score > DEFAULT_LIVENESS_THRESHOLD ? FrameOutcome.LIVE_ALIVE : FrameOutcome.LIVE_NOT_ALIVE;
    }

    private Bitmap uprightBitmap(ClipFile.Frame frame) {
        int count = frame.width * frame.height;
        if (pixels == null || pixels.length != count || bitmap.getWidth() != frame.width) {
            pixels = new int[count];
            bitmap = Bitmap.createBitmap(frame.width, frame.height, Bitmap.Config.ARGB_8888);
        }
        Nv21.toArgb(frame.nv21, frame.width, frame.height, pixels);
        bitmap.setPixels(pixels, 0, frame.width, 0, 0, frame.width, frame.height);
        return bitmap;
    }

    /** Exactly what FaceRecognitionActivity.imageProxyToBitmap did before the fix. */
    private static Bitmap jpegRoundTrip(byte[] nv21, int width, int height) {
        YuvImage yuv = new YuvImage(nv21, ImageFormat.NV21, width, height, null);
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        yuv.compressToJpeg(new Rect(0, 0, width, height), 100, out);
        byte[] jpeg = out.toByteArray();
        return BitmapFactory.decodeByteArray(jpeg, 0, jpeg.length);
    }

    /** Exactly what LivenessDetector.getNV21 does: integer BT.601 limited-range re-encode. */
    private static byte[] bitmapToNv21(Bitmap source) {
        if (source == null) return null;
        int width = source.getWidth(), height = source.getHeight();
        int[] argb = new int[width * height];
        source.getPixels(argb, 0, width, 0, 0, width, height);
        byte[] yuv = new byte[width * height * 3 / 2];
        int yIndex = 0, uvIndex = width * height, index = 0;
        for (int j = 0; j < height; j++) {
            for (int i = 0; i < width; i++) {
                int r = (argb[index] & 0xff0000) >> 16, g = (argb[index] & 0xff00) >> 8, b = argb[index] & 0xff;
                int y = ((66 * r + 129 * g + 25 * b + 128) >> 8) + 16;
                int u = ((-38 * r - 74 * g + 112 * b + 128) >> 8) + 128;
                int v = ((112 * r - 94 * g - 18 * b + 128) >> 8) + 128;
                yuv[yIndex++] = (byte) Math.max(0, Math.min(255, y));
                if (j % 2 == 0 && index % 2 == 0) {
                    yuv[uvIndex++] = (byte) Math.max(0, Math.min(255, v));
                    yuv[uvIndex++] = (byte) Math.max(0, Math.min(255, u));
                }
                index++;
            }
        }
        return yuv;
    }

    @Override
    public Object enroll(Bitmap photo) {
        return recognizer.extractFeature(photo);
    }

    @Override
    public float compare(Object probe, Object reference) {
        return recognizer.compare((float[]) probe, (float[]) reference);
    }

    @Override
    public void close() {
        recognizer.release();
        live.destroy();
        detector.destroy();
    }
}
