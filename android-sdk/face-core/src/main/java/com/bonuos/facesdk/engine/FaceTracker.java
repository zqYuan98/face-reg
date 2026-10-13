package com.bonuos.facesdk.engine;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * Greedy IoU tracker. Gives each face a stable id while it stays in view, so the session can do
 * the expensive work once per person instead of once per frame. Single-threaded.
 */
public final class FaceTracker {
    private final float minIou;
    private final int maxMissed;
    private final int history;
    private final float smoothing;
    private final List<Track> tracks = new ArrayList<>();
    private long nextId;

    /**
     * @param minIou     minimum overlap to continue a track
     * @param maxMissed  frames a track survives without a match (absorbs detector drop-outs)
     * @param history    centre history length used by the stability filter
     * @param smoothing  EMA weight of the new box for the display box, in (0, 1]
     * @param firstId    first id handed out; persist {@link #nextId()} to keep ids unique across restarts
     */
    public FaceTracker(float minIou, int maxMissed, int history, float smoothing, long firstId) {
        if (!(minIou > 0 && minIou < 1) || maxMissed < 0 || history < 2 || !(smoothing > 0 && smoothing <= 1))
            throw new IllegalArgumentException("Invalid tracker parameters");
        this.minIou = minIou;
        this.maxMissed = maxMissed;
        this.history = history;
        this.smoothing = smoothing;
        this.nextId = firstId;
    }

    /** Returns every live track (visible or briefly lost); ids are stable across calls. */
    public List<Track> update(List<Detection> detections) {
        int nd = detections.size(), nt = tracks.size();
        boolean[] usedDetection = new boolean[nd];
        boolean[] usedTrack = new boolean[nt];
        // All candidate pairs above the IoU floor, best overlap first; ties go to the closer centre.
        List<float[]> pairs = new ArrayList<>();
        for (int t = 0; t < nt; t++) {
            FaceRect previous = tracks.get(t).rect;
            for (int d = 0; d < nd; d++) {
                FaceRect current = detections.get(d).rect;
                float iou = previous.iou(current);
                if (iou >= minIou) {
                    float dx = previous.centerX() - current.centerX(), dy = previous.centerY() - current.centerY();
                    pairs.add(new float[]{iou, -(dx * dx + dy * dy), t, d});
                }
            }
        }
        Collections.sort(pairs, (a, b) -> a[0] != b[0] ? Float.compare(b[0], a[0]) : Float.compare(b[1], a[1]));
        for (float[] pair : pairs) {
            int t = (int) pair[2], d = (int) pair[3];
            if (usedTrack[t] || usedDetection[d]) continue;
            usedTrack[t] = usedDetection[d] = true;
            Track track = tracks.get(t);
            Detection detection = detections.get(d);
            track.rect = detection.rect;
            track.smoothed = track.smoothed.lerp(detection.rect, smoothing);
            track.score = detection.score;
            track.missed = 0;
            track.record();
        }
        for (int t = nt - 1; t >= 0; t--) {
            if (!usedTrack[t] && ++tracks.get(t).missed > maxMissed) tracks.remove(t);
        }
        for (int d = 0; d < nd; d++) {
            if (!usedDetection[d]) tracks.add(new Track(nextId++, detections.get(d), history));
        }
        return Collections.unmodifiableList(new ArrayList<>(tracks));
    }

    public void reset() { tracks.clear(); }

    /** Next id that will be handed out. */
    public long nextId() { return nextId; }
}
