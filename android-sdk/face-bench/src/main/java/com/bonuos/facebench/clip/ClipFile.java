package com.bonuos.facebench.clip;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.Closeable;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.EOFException;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;

/**
 * Raw upright NV21 frame sequence. No compression, so replay feeds every backend the exact
 * pixels the camera produced, including the moire and print texture liveness relies on.
 *
 * Layout: "FCLP", int version, int width, int height, then frames of
 * (long timestampNs, width*height*3/2 bytes NV21). Big-endian.
 */
public final class ClipFile {
    static final int MAGIC = 0x46434c50; // "FCLP"
    static final int VERSION = 1;

    private ClipFile() { }

    public static final class Writer implements Closeable {
        private final DataOutputStream out;
        private final int frameBytes;
        private int frames;

        public Writer(File file, int width, int height) throws IOException {
            if (width <= 0 || height <= 0 || (width & 1) != 0 || (height & 1) != 0)
                throw new IllegalArgumentException("Frame size must be positive and even");
            frameBytes = width * height * 3 / 2;
            out = new DataOutputStream(new BufferedOutputStream(new FileOutputStream(file), 1 << 20));
            out.writeInt(MAGIC);
            out.writeInt(VERSION);
            out.writeInt(width);
            out.writeInt(height);
        }

        public void write(long timestampNs, byte[] nv21) throws IOException {
            if (nv21.length < frameBytes) throw new IllegalArgumentException("Frame buffer too small");
            out.writeLong(timestampNs);
            out.write(nv21, 0, frameBytes);
            frames++;
        }

        public int frames() { return frames; }

        @Override public void close() throws IOException { out.close(); }
    }

    public static final class Reader implements Closeable {
        private final DataInputStream in;
        public final int width, height;
        private final int frameBytes;
        private int index;

        public Reader(File file) throws IOException {
            in = new DataInputStream(new BufferedInputStream(new FileInputStream(file), 1 << 20));
            try {
                if (in.readInt() != MAGIC) throw new IOException("Not a clip file: " + file.getName());
                int version = in.readInt();
                if (version != VERSION) throw new IOException("Unsupported clip version " + version);
                width = in.readInt();
                height = in.readInt();
                if (width <= 0 || height <= 0 || (width & 1) != 0 || (height & 1) != 0 || width * (long) height > 16_000_000)
                    throw new IOException("Invalid frame size " + width + "x" + height);
                frameBytes = width * height * 3 / 2;
            } catch (IOException | RuntimeException error) {
                in.close();
                throw error;
            }
        }

        /** Returns the next frame, or null at a clean end of file. A truncated last frame is an error. */
        public Frame next() throws IOException {
            int first = in.read();
            if (first < 0) return null;
            // EOF is clean only before the first timestamp byte, not halfway through its 8 bytes.
            long timestamp = (long) first << 56;
            for (int shift = 48; shift >= 0; shift -= 8) timestamp |= (long) in.readUnsignedByte() << shift;
            byte[] data = new byte[frameBytes];
            in.readFully(data);
            return new Frame(index++, timestamp, data, width, height);
        }

        @Override public void close() throws IOException { in.close(); }
    }

    public static final class Frame {
        public final int index;
        public final long timestampNs;
        public final byte[] nv21;
        public final int width, height;

        public Frame(int index, long timestampNs, byte[] nv21, int width, int height) {
            this.index = index;
            this.timestampNs = timestampNs;
            this.nv21 = nv21;
            this.width = width;
            this.height = height;
        }
    }
}
