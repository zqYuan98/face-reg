package com.bonuos.facesdk.engine;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * A whole-frame scan every {@code fullEvery} analyzed frames finds new faces; in between each
 * known face is re-detected inside a square region around its last box. A missed face or detector
 * failure forces a whole-frame scan on the next analyzed frame. This is not a wall-clock bound.
 * Pure policy; the detector is supplied per call. Single-threaded.
 */
public final class RoiTracker {
    public interface Source {
        /** Whole-frame detection. */
        List<Detection> full();

        /** Detection inside {@code region} (may extend past the frame), results in frame coordinates. */
        List<Detection> region(FaceRect region);
    }

    private final int fullEvery;
    private final float regionScale;
    private final float minIou;
    private List<Detection> last = Collections.emptyList();
    private int sinceFull;
    private boolean forceFull = true;
    private int fullScans, regionScans;

    /**
     * @param fullEvery   a whole-frame scan at least every this many frames (1 = every frame)
     * @param regionScale side of the search square relative to the face's longer side
     * @param minIou      overlap a region result needs with the previous box to continue that face
     */
    public RoiTracker(int fullEvery, float regionScale, float minIou) {
        if (fullEvery < 1 || !(regionScale >= 1f) || !(minIou >= 0f && minIou < 1f))
            throw new IllegalArgumentException("Invalid tracker schedule");
        this.fullEvery = fullEvery;
        this.regionScale = regionScale;
        this.minIou = minIou;
    }

    public List<Detection> next(Source source) {
        try {
            return detectNext(source);
        } catch (RuntimeException | Error failure) {
            reset(); // no old or partially updated ROI evidence survives a failed observation
            throw failure;
        }
    }

    private static List<Detection> checked(List<Detection> detections) {
        if (detections == null) throw new IllegalStateException("Detector returned null");
        return detections;
    }

    private List<Detection> detectNext(Source source) {
        if (forceFull || last.isEmpty() || ++sinceFull >= fullEvery) {
            forceFull = false;
            sinceFull = 0;
            fullScans++;
            last = checked(source.full());
            return last;
        }
        List<Detection> out = new ArrayList<>(last.size());
        for (Detection previous : last) {
            FaceRect p = previous.rect;
            float half = Math.max(p.width(), p.height()) * regionScale / 2;
            regionScans++;
            Detection best = null;
            float bestIou = minIou;
            for (Detection found : checked(source.region(new FaceRect(p.centerX() - half, p.centerY() - half,
                    p.centerX() + half, p.centerY() + half)))) {
                float iou = found.rect.iou(p);
                if (iou > bestIou) {
                    bestIou = iou;
                    best = found;
                }
            }
            if (best == null) {
                forceFull = true; // lost, or moved fast: rescan the whole frame next time
                continue;
            }
            boolean duplicate = false; // two tracked faces converging on one person
            for (Detection kept : out) duplicate |= kept.rect.iou(best.rect) > 0.5f;
            if (!duplicate) out.add(best);
        }
        last = out;
        return out;
    }

    public void reset() {
        last = Collections.emptyList();
        sinceFull = 0;
        forceFull = true;
    }

    public int fullScans() { return fullScans; }
    public int regionScans() { return regionScans; }
    public int fullEvery() { return fullEvery; }
    public float regionScale() { return regionScale; }
    public float minIou() { return minIou; }
}
