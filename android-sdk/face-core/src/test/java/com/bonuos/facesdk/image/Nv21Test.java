package com.bonuos.facesdk.image;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;

import java.nio.ByteBuffer;
import org.junit.Test;

public class Nv21Test {
    private static final int W = 4, H = 2;

    /** Y = 0..7; VU pairs (V,U) = (100,200),(101,201). */
    private static byte[] sample() {
        byte[] nv21 = new byte[Nv21.size(W, H)];
        for (int i = 0; i < W * H; i++) nv21[i] = (byte) i;
        nv21[8] = 100; nv21[9] = (byte) 200; nv21[10] = 101; nv21[11] = (byte) 201;
        return nv21;
    }

    @Test public void packsPaddedPlanarAndSemiPlanarLayouts() {
        byte[] expected = sample();
        // Y with row padding 2, chroma planar (pixel stride 1) with row padding 3.
        ByteBuffer y = ByteBuffer.wrap(new byte[]{0, 1, 2, 3, 9, 9, 4, 5, 6, 7, 9, 9});
        ByteBuffer u = ByteBuffer.wrap(new byte[]{(byte) 200, (byte) 201, 9, 9, 9});
        ByteBuffer v = ByteBuffer.wrap(new byte[]{100, 101, 9, 9, 9});
        byte[] out = new byte[Nv21.size(W, H)];
        Nv21.fromYuv420(W, H, y, 6, u, v, 5, 1, out);
        assertArrayEquals(expected, out);

        // Semi-planar chroma (pixel stride 2), the common Camera2 layout.
        ByteBuffer y2 = ByteBuffer.wrap(new byte[]{0, 1, 2, 3, 4, 5, 6, 7});
        ByteBuffer u2 = ByteBuffer.wrap(new byte[]{(byte) 200, 101, (byte) 201});
        ByteBuffer v2 = ByteBuffer.wrap(new byte[]{100, (byte) 200, 101});
        byte[] out2 = new byte[Nv21.size(W, H)];
        Nv21.fromYuv420(W, H, y2, 4, u2, v2, 4, 2, out2);
        assertArrayEquals(expected, out2);
    }

    @Test public void rotatesLumaAndChromaClockwise() {
        byte[] src = sample();
        byte[] r90 = Nv21.rotate(src, W, H, 90);
        // 4x2 rotated clockwise becomes 2x4: column 0 is the old bottom row read upward.
        assertArrayEquals(new byte[]{4, 0, 5, 1, 6, 2, 7, 3}, java.util.Arrays.copyOf(r90, 8));
        // Chroma 2x1 pairs become 1x2: first pair stays on top.
        assertEquals(100, r90[8]); assertEquals((byte) 200, r90[9]);
        assertEquals(101, r90[10]); assertEquals((byte) 201, r90[11]);

        byte[] r180 = Nv21.rotate(src, W, H, 180);
        assertArrayEquals(new byte[]{7, 6, 5, 4, 3, 2, 1, 0}, java.util.Arrays.copyOf(r180, 8));
        assertEquals(101, r180[8]); assertEquals(100, r180[10]);

        byte[] r270 = Nv21.rotate(src, W, H, 270);
        assertArrayEquals(new byte[]{3, 7, 2, 6, 1, 5, 0, 4}, java.util.Arrays.copyOf(r270, 8));
        assertEquals(101, r270[8]); assertEquals(100, r270[10]);
    }

    @Test public void fourQuarterTurnsAreIdentity() {
        byte[] src = new byte[Nv21.size(8, 6)];
        for (int i = 0; i < src.length; i++) src[i] = (byte) (i * 7);
        byte[] a = Nv21.rotate(src, 8, 6, 90);
        byte[] b = Nv21.rotate(a, 6, 8, 90);
        byte[] c = Nv21.rotate(b, 8, 6, 90);
        byte[] d = Nv21.rotate(c, 6, 8, 90);
        assertArrayEquals(src, d);
        assertArrayEquals(Nv21.rotate(src, 8, 6, 270), c);
    }

    @Test public void convertsNeutralAndPrimaryColours() {
        byte[] gray = new byte[Nv21.size(2, 2)];
        java.util.Arrays.fill(gray, 0, 4, (byte) 128);
        gray[4] = (byte) 128; gray[5] = (byte) 128;
        int[] argb = new int[4];
        Nv21.toArgb(gray, 2, 2, argb);
        assertEquals(0xff808080, argb[0]);

        byte[] red = new byte[Nv21.size(2, 2)];
        java.util.Arrays.fill(red, 0, 4, (byte) 76);   // JFIF red: Y=76, U=85, V=255
        red[4] = (byte) 255; red[5] = 85;
        Nv21.toArgb(red, 2, 2, argb);
        int r = (argb[0] >> 16) & 0xff, g = (argb[0] >> 8) & 0xff, b = argb[0] & 0xff;
        assertEquals(true, r >= 250 && g <= 5 && b <= 5);
    }
}
