package com.bonuos.facebench.bench;

import static org.junit.Assert.*;
import android.graphics.Bitmap;
import com.bonuos.facebench.backend.*;
import com.bonuos.facebench.clip.*;
import java.io.File;
import java.io.RandomAccessFile;
import java.nio.file.Files;
import java.util.Collections;
import java.util.List;
import org.junit.Test;

public class BenchRunnerTest {
    private static final class Fake implements FaceBackend {
        final BackendEvents events = new BackendEvents(8);
        int calls, finishes;
        boolean drain = true, processError;
        long epoch;
        public String name() { return "session_fake"; }
        public String describe() { return "fake"; }
        public void beginClip(int rotation) { epoch = events.begin(); }
        public FrameOutcome process(ClipFile.Frame frame, boolean live, boolean feature) {
            calls++;
            if (processError) throw new IllegalStateException("controlled frame failure");
            return new FrameOutcome();
        }
        public boolean finishClip(long timeoutMs) {
            finishes++;
            events.add(epoch, 1, 0, "recognized", "alice", 1234, .9, 1, "");
            return drain;
        }
        public long clipEpoch() { return epoch; }
        public List<BackendEvent> drainEvents() { return events.drain(); }
        public Object enroll(Bitmap photo) { return null; }
        public float compare(Object a, Object b) { return 0; }
        public void close() { }
    }
    private ClipCompletion replay(Fake fake, boolean cancel, boolean truncate) throws Exception {
        File dir = Files.createTempDirectory("bench-replay").toFile(), clip = new File(dir, "one.fclip");
        try (ClipFile.Writer writer = new ClipFile.Writer(clip, 4, 2)) { writer.write(1, new byte[12]); }
        if (truncate) try (RandomAccessFile file = new RandomAccessFile(clip, "rw")) { file.setLength(20); }
        ClipMeta meta = new ClipMeta();
        meta.clipId = "one"; meta.subjectId = "alice"; meta.label = "live"; meta.frameCount = 1;
        ClipCompletion result = new ClipCompletion(fake.name(), meta.clipId);
        BenchRunner.Progress progress = new BenchRunner.Progress() {
            public void update(String text) { }
            public boolean cancelled() { return cancel; }
        };
        try (CsvWriter frames = new CsvWriter(new File(dir, "frames.csv"), new String[22]);
             CsvWriter sweep = new CsvWriter(new File(dir, "sweep.csv"), new String[5]);
             CsvWriter scores = new CsvWriter(new File(dir, "scores.csv"), new String[7]);
             CsvWriter events = new CsvWriter(new File(dir, "events.csv"), new String[15])) {
            BenchRunner.replayClip(fake, clip, meta, new BenchConfig(), Collections.emptyMap(),
                    frames, sweep, scores, events, progress, result);
        }
        assertEquals(1, fake.finishes);
        assertEquals(1, result.eventCount);
        assertEquals(1, result.tailEvents);
        assertTrue(new String(Files.readAllBytes(new File(dir, "events.csv").toPath()), "UTF-8").contains("alice,alice,1234"));
        return result;
    }
    @Test public void eofDrainsTailWithoutSyntheticFrame() throws Exception {
        Fake fake = new Fake(); ClipCompletion c = replay(fake, false, false);
        assertEquals(1, fake.calls); assertTrue(c.eof); assertTrue(c.drainOk); assertTrue(c.complete());
    }
    @Test public void cancellationStillDrains() throws Exception {
        Fake fake = new Fake(); ClipCompletion c = replay(fake, true, false);
        assertEquals(0, fake.calls); assertTrue(c.cancelled); assertFalse(c.complete());
    }
    @Test public void corruptInputStillDrainsAndDoesNotCountAsEof() throws Exception {
        ClipCompletion c = replay(new Fake(), false, true);
        assertFalse(c.eof); assertFalse(c.error.isEmpty()); assertFalse(c.complete());
    }
    @Test public void frameFailureAndDrainTimeoutStayVisible() throws Exception {
        Fake fake = new Fake(); fake.processError = true; fake.drain = false;
        ClipCompletion c = replay(fake, false, false);
        assertEquals(1, c.frameErrors); assertFalse(c.drainOk); assertFalse(c.complete());
    }
}
