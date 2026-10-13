package com.bonuos.facesdk.image;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;

import java.util.Random;
import org.junit.Test;

public class Nv21CropTest {
    private static byte[] randomFrame(int w, int h, long seed) {
        byte[] nv21 = new byte[Nv21.size(w, h)];
        new Random(seed).nextBytes(nv21);
        return nv21;
    }

    @Test public void cropMatchesRotateThenConvertForEveryRotation() {
        int w = 24, h = 16;
        byte[] src = randomFrame(w, h, 1);
        for (int degrees : new int[]{0, 90, 180, 270, -90, 450}) {
            byte[] upright = Nv21.rotate(src, w, h, degrees);
            int uw = degrees % 180 == 0 ? w : h, uh = degrees % 180 == 0 ? h : w;
            int[] full = new int[uw * uh];
            Nv21.toArgb(upright, uw, uh, full);
            // Odd offsets and sizes exercise chroma alignment inside 2x2 blocks.
            int left = 3, top = 5, cw = uw - 6, ch = uh - 8;
            int[] expected = new int[cw * ch];
            for (int y = 0; y < ch; y++) System.arraycopy(full, (top + y) * uw + left, expected, y * cw, cw);
            int[] actual = new int[cw * ch];
            Nv21.cropToArgb(src, w, h, degrees, left, top, cw, ch, actual);
            assertArrayEquals("rotation " + degrees, expected, actual);
        }
    }

    @Test public void lumaSamplesFollowTheRotation() {
        int w = 8, h = 4;
        byte[] src = new byte[Nv21.size(w, h)];
        for (int i = 0; i < w * h; i++) src[i] = (byte) i;
        byte[] upright = Nv21.rotate(src, w, h, 90);
        float[] grid = new float[h * w];
        Nv21.sampleLuma(src, w, h, 90, 0, 0, h, w, h, w, grid);
        for (int i = 0; i < grid.length; i++) assertEquals(upright[i] & 0xff, grid[i], 0f);
    }
}
