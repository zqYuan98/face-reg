package com.bonuos.facebench.clip;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertThrows;

import java.io.File;
import java.io.IOException;
import java.io.RandomAccessFile;
import java.nio.file.Files;
import org.junit.Test;

public class ClipFileTest {
    @Test public void roundTripsFramesAndTimestamps() throws IOException {
        File file = Files.createTempFile("clip", ".fclip").toFile();
        byte[] first = new byte[4 * 2 * 3 / 2], second = new byte[first.length];
        for (int i = 0; i < first.length; i++) { first[i] = (byte) i; second[i] = (byte) (100 + i); }
        try (ClipFile.Writer writer = new ClipFile.Writer(file, 4, 2)) {
            writer.write(1_000L, first);
            writer.write(34_000_000L, second);
            assertEquals(2, writer.frames());
        }
        try (ClipFile.Reader reader = new ClipFile.Reader(file)) {
            assertEquals(4, reader.width);
            assertEquals(2, reader.height);
            ClipFile.Frame a = reader.next(), b = reader.next();
            assertEquals(0, a.index);
            assertEquals(1_000L, a.timestampNs);
            assertArrayEquals(first, a.nv21);
            assertEquals(1, b.index);
            assertEquals(34_000_000L, b.timestampNs);
            assertArrayEquals(second, b.nv21);
            assertNull(reader.next());
        }
    }

    @Test public void truncatedFrameIsAnErrorNotACleanEnd() throws IOException {
        File file = Files.createTempFile("clip", ".fclip").toFile();
        try (ClipFile.Writer writer = new ClipFile.Writer(file, 4, 2)) {
            writer.write(1L, new byte[12]);
        }
        try (RandomAccessFile raf = new RandomAccessFile(file, "rw")) {
            raf.setLength(raf.length() - 3);
        }
        try (ClipFile.Reader reader = new ClipFile.Reader(file)) {
            assertThrows(IOException.class, reader::next);
        }
    }

    @Test public void rejectsForeignFiles() throws IOException {
        File file = Files.createTempFile("clip", ".fclip").toFile();
        Files.write(file.toPath(), new byte[]{1, 2, 3, 4, 0, 0, 0, 1, 0, 0, 0, 4, 0, 0, 0, 2});
        assertThrows(IOException.class, () -> new ClipFile.Reader(file).close());
    }

    @Test public void metadataValidatesLabels() throws IOException {
        ClipMeta meta = new ClipMeta();
        meta.clipId = "p001_live_20261012-101010";
        meta.subjectId = "p001";
        meta.label = ClipMeta.LIVE;
        File json = Files.createTempFile("clip", ".json").toFile();
        meta.write(json);
        assertEquals("p001", ClipMeta.read(json).subjectId);

        meta.label = ClipMeta.ATTACK;
        assertThrows(IllegalArgumentException.class, meta::validate); // attack needs a type
        meta.attackType = "screen";
        meta.validate();
        meta.subjectId = "../etc";
        assertThrows(IllegalArgumentException.class, meta::validate);
    }
}
