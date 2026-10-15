package com.bonuos.facesdk.engine;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * Detection schedule that follows faces instead of scanning every frame, like ArcSoft's VIDEO
 * mode: a whole-frame scan every {@code fullEvery} frames finds new faces; in between each known
 * face is re-detected only inside a square region around its last box. A face the region misses
 * forces a whole-frame scan on the next frame, so nothing is lost for longer than one frame.
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
        if (forceFull || last.isEmpty() || ++sinceFull >= fullEvery) {
            forceFull = false;
            sinceFull = 0;
            fullScans++;
            last = source.full();
            return last;
        }
        List<Detection> out = new ArrayList<>(last.size());
        for (Detection previous : last) {
            FaceRect p = previous.rect;
            float half = Math.max(p.width(), p.height()) * regionScale / 2;
            regionScans++;
            Detection best = null;
            float bestIou = minIou;
            for (Detection found : source.region(new FaceRect(p.centerX() - half, p.centerY() - half,
                    p.centerX() + half, p.centerY() + half))) {
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
}
