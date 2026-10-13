package com.bonuos.facesdk.engine;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Random;
import org.junit.Test;

public class GalleryTest {
    @Test public void topTwoMatchesBruteForceCosine() {
        Random random = new Random(3);
        int n = 500, d = 64;
        List<Gallery.Entry<Integer>> entries = new ArrayList<>();
        float[][] raw = new float[n][];
        for (int i = 0; i < n; i++) {
            raw[i] = new float[d];
            for (int k = 0; k < d; k++) raw[i][k] = (float) random.nextGaussian() * (1 + i % 3);
            entries.add(new Gallery.Entry<>(i, raw[i]));
        }
        Gallery<Integer> gallery = Gallery.build(entries, d);
        for (int q = 0; q < 20; q++) {
            float[] probe = new float[d];
            for (int k = 0; k < d; k++) probe[k] = raw[q * 7][k] + (float) random.nextGaussian() * 0.5f;
            float[] scores = new float[n];
            for (int i = 0; i < n; i++) scores[i] = Gallery.cosine(probe, raw[i]);
            float[] sorted = scores.clone();
            Arrays.sort(sorted);
            Gallery.Match<Integer> match = gallery.search(probe);
            assertEquals(sorted[n - 1], match.score, 1e-5f);
            assertEquals(sorted[n - 2], match.secondScore, 1e-5f);
            assertEquals(sorted[n - 1], scores[match.best], 1e-5f);
        }
    }

    @Test public void incompatibleEntriesAreSkippedNotCompared() {
        List<Gallery.Entry<String>> entries = Arrays.asList(
                new Gallery.Entry<>("ok", new float[]{1, 0}),
                new Gallery.Entry<>("null", (float[]) null),
                new Gallery.Entry<>("short", new float[]{1}),
                new Gallery.Entry<>("zero", new float[]{0, 0}),
                new Gallery.Entry<>("nan", new float[]{Float.NaN, 1}));
        Gallery<String> gallery = Gallery.build(entries, 2);
        assertEquals(1, gallery.size());
        assertEquals(4, gallery.skipped());
        assertEquals("ok", gallery.search(new float[]{2, 0}).best);
        assertNull(gallery.search(new float[]{1, 0, 0}));
        assertEquals(Float.NEGATIVE_INFINITY, gallery.search(new float[]{1, 0}).secondScore, 0f);
    }

    @Test public void customScorerKeepsItsOwnScale() {
        List<Gallery.Entry<String>> entries = Arrays.asList(
                new Gallery.Entry<>("a", new float[]{1, 0}), new Gallery.Entry<>("b", new float[]{0, 1}));
        Gallery<String> gallery = Gallery.build(entries, 2, (p, e) -> 0.5f + 0.5f * Gallery.cosine(p, e));
        Gallery.Match<String> match = gallery.search(new float[]{0, 3});
        assertEquals("b", match.best);
        assertEquals(1f, match.score, 1e-6f);
        assertEquals(0.5f, match.secondScore, 1e-6f);
    }

    @Test public void emptyGalleryReturnsNoBest() {
        assertNull(Gallery.<String>empty().search(new float[]{1, 0}).best);
    }
}
