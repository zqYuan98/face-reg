package com.bonuos.facesdk.engine;

import static org.junit.Assert.*;

import com.bonuos.facesdk.image.Nv21;
import java.util.Arrays;
import java.util.Random;
import java.util.concurrent.Callable;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.annotation.Config;

@RunWith(RobolectricTestRunner.class)
@Config(manifest = Config.NONE, sdk = 28)
public class NativeFeatureEngineCaptureTest {
    private static final class Converter implements NativeFeatureEngine.CropConverter {
        int calls;
        @Override public boolean convert(byte[] nv21, int width, int height, int rotation, int left, int top,
                                         int cropWidth, int cropHeight, byte[] out) {
            calls++;
            int[] pixels = new int[cropWidth * cropHeight];
            Nv21.cropToArgb(nv21, width, height, rotation, left, top, cropWidth, cropHeight, pixels);
            for (int i = 0; i < pixels.length; i++) {
                out[3 * i] = (byte) pixels[i];
                out[3 * i + 1] = (byte) (pixels[i] >> 8);
                out[3 * i + 2] = (byte) (pixels[i] >> 16);
            }
            return true;
        }
    }

    private static final class Model implements NativeFeatureEngine.Extractor {
        int calls;
        byte[] pixels;
        float[] points;
        NativeEngine.LandmarkMode mode;
        final float[] result;
        boolean mutateKeypoints;

        Model(float... result) { this.result = result; }

        @Override public float[] extract(byte[] bgr, int width, int height, FaceRect face,
                                         NativeEngine.LandmarkMode mode, float[] outLandmarks) {
            this.mode = mode;
            float[] landmarks = keypoints(5f);
            System.arraycopy(landmarks, 0, outLandmarks, 0, 10);
            return extractWithKeypoints(bgr, width, height, outLandmarks);
        }

        @Override public float[] extractWithKeypoints(byte[] bgr, int width, int height, float[] keypoints) {
            calls++;
            pixels = bgr;
            points = keypoints.clone();
            if (mutateKeypoints) keypoints[0] += 1;
            return result.clone();
        }
    }

    private static float[] keypoints(float dx) {
        return new float[]{50, 45, 90, 45, 70 + dx, 45 + 0.4609f * 40, 55, 85, 85, 85};
    }

    private static CameraFrame frame(int rotation) {
        byte[] nv21 = new byte[Nv21.size(200, 160)];
        new Random(39).nextBytes(nv21);
        return new CameraFrame(nv21, 200, 160, rotation, 10);
    }

    private static NativeFeatureEngine engine(Model model, Converter converter) {
        return new NativeFeatureEngine(model, converter, NativeEngine.LandmarkMode.SUBPIXEL, 30, 25);
    }

    @Test public void compatiblePairConvertsOnceAndSecondModelRemainsLazy() throws Exception {
        Converter converter = new Converter();
        Model fast = new Model(1, 0), slow = new Model(0, 1);
        NativeFeatureEngine first = engine(fast, converter), second = engine(slow, converter);
        FaceRect face = new FaceRect(40, 20, 100, 110);
        FaceSession.CapturedFeatures tasks = first.capturePair(frame(0), face, keypoints(0), face, second);
        assertEquals(1, converter.calls);
        assertEquals(1, tasks.captureCount);
        assertTrue(tasks.preparedBytes > 0);
        assertEquals(0, fast.calls + slow.calls);
        assertArrayEquals(new float[]{1, 0}, tasks.primary.call(), 0);
        assertEquals(0, slow.calls);
        assertArrayEquals(new float[]{0, 1}, tasks.verification.call(), 0);
        assertSame("models share the privately owned BGR capture", fast.pixels, slow.pixels);
        assertArrayEquals(fast.points, slow.points, 0);
    }

    @Test public void sharedCaptureMatchesIndependentCapturesForAllRotations() throws Exception {
        FaceRect face = new FaceRect(35.5f, 17.5f, 103.5f, 117.5f);
        for (int rotation : new int[]{0, 90, 180, 270}) {
            CameraFrame frame = frame(rotation);
            Converter converter = new Converter();
            Model a = new Model(1), b = new Model(1), expectedA = new Model(1), expectedB = new Model(1);
            NativeFeatureEngine first = engine(a, converter), second = engine(b, converter);
            FaceSession.CapturedFeatures pair = first.capturePair(frame, face, keypoints(0), face, second);
            Callable<float[]> independentA = engine(expectedA, converter).capture(frame, face, keypoints(0), face);
            Callable<float[]> independentB = engine(expectedB, converter).capture(frame, face, keypoints(0), face);
            pair.primary.call(); pair.verification.call(); independentA.call(); independentB.call();
            assertEquals(3, converter.calls);
            assertArrayEquals(expectedA.pixels, a.pixels);
            assertArrayEquals(expectedB.pixels, b.pixels);
            assertArrayEquals(expectedA.points, a.points, 0);
            assertArrayEquals(expectedB.points, b.points, 0);
        }
    }

    @Test public void cameraAndKeypointReuseCannotChangeCapturedTasks() throws Exception {
        CameraFrame input = frame(270);
        float[] points = keypoints(0);
        Converter converter = new Converter();
        Model a = new Model(1), b = new Model(1);
        NativeFeatureEngine first = engine(a, converter), second = engine(b, converter);
        FaceRect face = new FaceRect(40, 20, 100, 110);
        FaceSession.CapturedFeatures pair = first.capturePair(input, face, points, face, second);
        Model expected = new Model(1);
        engine(expected, converter).capture(input, face, points, face).call();
        Arrays.fill(input.nv21, (byte) 0);
        Arrays.fill(points, Float.NaN);
        pair.primary.call(); pair.verification.call();
        assertArrayEquals(expected.pixels, a.pixels);
        assertArrayEquals(expected.pixels, b.pixels);
        assertArrayEquals(expected.points, b.points, 0);
    }

    @Test public void landmarkOutputAndPosePolicyAreIndependentPerStage() throws Exception {
        Converter converter = new Converter();
        Model first = new Model(1), second = new Model(1);
        first.mutateKeypoints = true;
        NativeFeatureEngine fast = engine(first, converter);
        NativeFeatureEngine strict = new NativeFeatureEngine(second, converter, NativeEngine.LandmarkMode.TTA, 1, 25);
        FaceRect face = new FaceRect(40, 20, 100, 110);
        FaceSession.CapturedFeatures pair = fast.capturePair(frame(0), face, keypoints(5), face, strict);
        assertNotNull(pair.primary.call());
        assertNull("strict verifier must still use its own pose limits", pair.verification.call());
        assertArrayEquals(first.points, second.points, 0);
        assertEquals(1, converter.calls);
    }

    @Test public void noKeypointsStillSharesPixelsButKeepsModelOutputSeparate() throws Exception {
        Converter converter = new Converter();
        Model a = new Model(1), b = new Model(2);
        NativeFeatureEngine first = new NativeFeatureEngine(a, converter, NativeEngine.LandmarkMode.TRUNCATED, 30, 25);
        NativeFeatureEngine second = new NativeFeatureEngine(b, converter, NativeEngine.LandmarkMode.TTA, 30, 25);
        FaceRect face = new FaceRect(40, 20, 100, 110);
        FaceSession.CapturedFeatures pair = first.capturePair(frame(0), face, null, face, second);
        pair.primary.call(); pair.verification.call();
        assertSame(a.pixels, b.pixels);
        assertEquals(NativeEngine.LandmarkMode.TRUNCATED, a.mode);
        assertEquals(NativeEngine.LandmarkMode.TTA, b.mode);
        assertEquals(1, converter.calls);
    }

    @Test public void differentConvertersAndNonNativeVerifiersFallBack() throws Exception {
        Converter one = new Converter(), two = new Converter();
        FaceRect face = new FaceRect(40, 20, 100, 110);
        NativeFeatureEngine first = engine(new Model(1), one);
        first.capturePair(frame(0), face, keypoints(0), face, engine(new Model(1), two));
        assertEquals(1, one.calls); assertEquals(1, two.calls);
        int[] fallback = {0};
        FaceSession.FeatureEngine other = (f, r, k, c) -> { fallback[0]++; return () -> new float[]{3}; };
        FaceSession.CapturedFeatures pair = first.capturePair(frame(0), face, keypoints(0), face, other);
        assertEquals(1, fallback[0]);
        assertArrayEquals(new float[]{3}, pair.verification.call(), 0);
    }

    @Test public void singleCaptureReportsKnownBytesWithoutVerifier() throws Exception {
        Converter converter = new Converter();
        NativeFeatureEngine first = engine(new Model(1), converter);
        FaceRect face = new FaceRect(40, 20, 100, 110);
        FaceSession.CapturedFeatures pair = first.capturePair(frame(0), face, keypoints(0), face, null);
        assertEquals(1, converter.calls);
        assertEquals(1, pair.captureCount);
        assertTrue(pair.preparedBytes > 0);
        assertNull(pair.verification);
        assertNotNull(pair.primary.call());
    }

    @Test public void captureFailureDoesNotReturnHalfAPair() {
        NativeFeatureEngine.CropConverter fails = (data, w, h, rot, l, t, cw, ch, out) -> false;
        NativeFeatureEngine first = new NativeFeatureEngine(new Model(1), fails, NativeEngine.LandmarkMode.TTA, 30, 25);
        NativeFeatureEngine second = new NativeFeatureEngine(new Model(1), fails, NativeEngine.LandmarkMode.TTA, 30, 25);
        FaceRect face = new FaceRect(40, 20, 100, 110);
        assertThrows(IllegalArgumentException.class, () -> first.capturePair(frame(0), face, keypoints(0), face, second));
    }
}
