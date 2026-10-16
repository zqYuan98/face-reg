package com.bonuos.faceapp;

import androidx.camera.core.ImageProxy;
import com.bonuos.facesdk.engine.CameraFrame;

/** The one place that transfers ownership from CameraX to an independent analysis frame. */
final class CameraFrameAdmission {
    interface Processor {
        boolean shouldProcess(long timestampMs);
        CameraFrame capture(ImageProxy image, long timestampMs);
        void process(CameraFrame frame);
    }

    static final class Counters {
        long received, skipped, packed, captureBytes, captureNanos, errors;
    }

    private CameraFrameAdmission() { }

    static void analyze(ImageProxy image, Processor processor) {
        analyze(image, processor, new Counters());
    }

    static void analyze(ImageProxy image, Processor processor, Counters counters) {
        counters.received++;
        CameraFrame frame;
        try {
            try {
                long timestampMs = image.getImageInfo().getTimestamp() / 1_000_000L;
                if (!processor.shouldProcess(timestampMs)) {
                    counters.skipped++;
                    return;
                }
                long captureStarted = System.nanoTime();
                try {
                    frame = processor.capture(image, timestampMs);
                    counters.packed++;
                    counters.captureBytes += (long) frame.width * frame.height * 3 / 2;
                } finally {
                    counters.captureNanos += System.nanoTime() - captureStarted;
                }
            } finally {
                // All capture/skip/error paths close exactly once, before the models start running.
                image.close();
            }
            processor.process(frame);
        } catch (RuntimeException error) {
            counters.errors++;
            throw error;
        }
    }
}
