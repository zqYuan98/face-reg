package com.bonuos.faceapp;

import static org.junit.Assert.*;

import androidx.camera.core.ImageInfo;
import androidx.camera.core.ImageProxy;
import com.bonuos.facesdk.engine.CameraFrame;
import java.lang.reflect.Proxy;
import org.junit.Test;

public class CameraFrameAdmissionTest {
    private static final class FrameStub {
        int closes, planeReads;
        boolean failTimestamp;
        final ImageInfo info = (ImageInfo) Proxy.newProxyInstance(ImageInfo.class.getClassLoader(),
                new Class<?>[]{ImageInfo.class}, (p, method, args) -> {
                    if (method.getName().equals("getTimestamp")) {
                        if (failTimestamp) throw new IllegalStateException("clock unavailable");
                        return 123_000_000L;
                    }
                    throw new AssertionError("Unexpected ImageInfo method " + method.getName());
                });
        final ImageProxy image = (ImageProxy) Proxy.newProxyInstance(ImageProxy.class.getClassLoader(),
                new Class<?>[]{ImageProxy.class}, (p, method, args) -> {
                    switch (method.getName()) {
                        case "getImageInfo": return info;
                        case "close": closes++; return null;
                        case "getPlanes": planeReads++; return new ImageProxy.PlaneProxy[0];
                        default: throw new AssertionError("Unexpected frame read " + method.getName());
                    }
                });
    }

    @Test public void skippedFrameDoesNotAccessPlanesOrCapture() {
        FrameStub frame = new FrameStub();
        CameraFrameAdmission.Counters counters = new CameraFrameAdmission.Counters();
        CameraFrameAdmission.analyze(frame.image, new CameraFrameAdmission.Processor() {
            @Override public boolean shouldProcess(long timestamp) { assertEquals(123, timestamp); return false; }
            @Override public CameraFrame capture(ImageProxy image, long timestamp) { throw new AssertionError("packed skipped frame"); }
            @Override public void process(CameraFrame input) { throw new AssertionError("processed skipped frame"); }
        }, counters);
        assertEquals(0, frame.planeReads);
        assertEquals(1, frame.closes);
        assertEquals(1, counters.received);
        assertEquals(1, counters.skipped);
        assertEquals(0, counters.packed);
        assertEquals(0, counters.captureBytes);
    }

    @Test public void acceptedFrameClosesBeforeProcessingExactlyOnce() {
        FrameStub frame = new FrameStub();
        int[] processed = {0};
        CameraFrameAdmission.analyze(frame.image, new CameraFrameAdmission.Processor() {
            @Override public boolean shouldProcess(long timestamp) { return true; }
            @Override public CameraFrame capture(ImageProxy image, long timestamp) {
                assertEquals(0, frame.closes);
                image.getPlanes();
                return new CameraFrame(new byte[6], 2, 2, 0, timestamp);
            }
            @Override public void process(CameraFrame input) {
                assertEquals(1, frame.closes);
                assertEquals(123, input.timestampMs);
                processed[0]++;
            }
        });
        assertEquals(1, frame.planeReads);
        assertEquals(1, frame.closes);
        assertEquals(1, processed[0]);
    }

    @Test public void everyExceptionPathStillClosesExactlyOnce() {
        for (int stage = 0; stage < 4; stage++) {
            final int failureStage = stage;
            FrameStub frame = new FrameStub();
            frame.failTimestamp = stage == 0;
            assertThrows(IllegalStateException.class, () -> CameraFrameAdmission.analyze(frame.image,
                    new CameraFrameAdmission.Processor() {
                        @Override public boolean shouldProcess(long timestamp) {
                            if (failureStage == 1) throw new IllegalStateException("admission");
                            return true;
                        }
                        @Override public CameraFrame capture(ImageProxy image, long timestamp) {
                            if (failureStage == 2) throw new IllegalStateException("capture");
                            return new CameraFrame(new byte[6], 2, 2, 0, timestamp);
                        }
                        @Override public void process(CameraFrame input) { throw new IllegalStateException("process"); }
                    }));
            assertEquals("failure stage " + stage, 1, frame.closes);
        }
    }
}
