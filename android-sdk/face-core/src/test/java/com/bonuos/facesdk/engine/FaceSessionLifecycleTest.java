package com.bonuos.facesdk.engine;

import org.junit.Test;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
import static org.junit.Assert.*;

/** Real blocked workers plus deterministic executors; no native/Android dependency. */
public class FaceSessionLifecycleTest {
    private static final float[] ALICE = {1, 0}, BORDERLINE = {.6f, .8f};
    private static final Gallery<String> USERS = Gallery.build(
            Collections.singletonList(new Gallery.Entry<>("alice", ALICE)), 2);
    private static final List<Detection> FACE = Collections.singletonList(
            new Detection(new FaceRect(60, 50, 160, 150), 1));
    private final byte[] pixels = new byte[320 * 240 * 3 / 2];
    private final List<FaceSession.RecognitionEvent<String>> events = new CopyOnWriteArrayList<>();
    private final List<FaceSession.Liveness> votes = new CopyOnWriteArrayList<>();
    private volatile List<Detection> faces = FACE;
    private volatile List<FaceSession.FaceState<String>> lastFaces = Collections.emptyList();
    private final SessionConfig config = config();
    private long time;

    public FaceSessionLifecycleTest() { Arrays.fill(pixels, (byte) 100); }
    private static SessionConfig config() {
        SessionConfig c = new SessionConfig();
        c.stableFrames = 2; c.minFaceSize = 20; c.relativeSharpness = 0; c.idleFrameIntervalMs = 0;
        return c;
    }
    private final FaceSession.Listener<String> listener = new FaceSession.Listener<String>() {
        public void onFaces(List<FaceSession.FaceState<String>> f, CameraFrame frame) { lastFaces = f; }
        public void onRecognized(FaceSession.RecognitionEvent<String> e) { events.add(e); }
        public void onRecognitionFailed(long id, float score, FaceSession.Reason reason) { }
        public void onLiveness(long id, float score, FaceSession.Liveness verdict) { votes.add(verdict); }
    };
    private FaceSession<String> session(Executor executor, FaceSession.FeatureEngine features) {
        return new FaceSession<>(config, f -> faces, (f, r) -> .95f, features, () -> USERS, listener, executor);
    }
    private void frame(FaceSession<?> session) { session.onFrame(new CameraFrame(pixels, 320, 240, 0, ++time * 33)); }
    private void stable(FaceSession<?> session) { frame(session); frame(session); frame(session); }
    private static final class Manual implements Executor {
        final BlockingQueue<Runnable> queue = new LinkedBlockingQueue<>();
        public void execute(Runnable command) { queue.add(command); }
        void run() throws Exception { Runnable next = queue.poll(2, TimeUnit.SECONDS); assertNotNull(next); next.run(); }
    }
    private static void await(CountDownLatch latch) throws Exception { assertTrue(latch.await(2, TimeUnit.SECONDS)); }
    private static void awaitUninterruptibly(CountDownLatch latch) {
        boolean interrupted = false;
        for (;;) { try { latch.await(); break; } catch (InterruptedException e) { interrupted = true; } }
        if (interrupted) Thread.currentThread().interrupt();
    }
    private static final class Source implements FaceSession.GallerySource<String> {
        volatile FaceSession.GallerySnapshot<String> view;
        Source(DatasetEpoch epoch) { set(USERS, epoch, epoch.generation(), true); }
        void set(Gallery<String> g, DatasetEpoch e, long n, boolean ready) { view = new FaceSession.GallerySnapshot<>(g, e, n, ready); }
        public Gallery<String> gallery() { return view.gallery; }
        public FaceSession.GallerySnapshot<String> snapshot() { return view; }
    }
    private final class AsyncLive implements FaceSession.LivenessScorer, FaceSession.LivenessEngine {
        public float score(CameraFrame f, FaceRect r) { throw new AssertionError("async required"); }
        public Callable<Float> captureLiveness(CameraFrame f, FaceRect r) { return () -> .95f; }
    }

    @Test public void admissionIsPureAndResetRollbackDuplicatesHaveExplicitSemantics() {
        config.idleFrameIntervalMs = 100;
        faces = Collections.emptyList();
        AtomicInteger detections = new AtomicInteger();
        FaceSession<String> s = new FaceSession<>(config, f -> { detections.incrementAndGet(); return faces; },
                (f,r) -> .95f, (f,r,k,c) -> () -> ALICE, () -> USERS, listener, Runnable::run);
        assertTrue(s.shouldProcessFrame(1000)); assertTrue(s.shouldProcessFrame(1000));
        s.onFrame(new CameraFrame(pixels,320,240,0,1000));
        assertFalse(s.shouldProcessFrame(1000)); assertFalse(s.shouldProcessFrame(1099));
        assertTrue(s.shouldProcessFrame(1100)); assertTrue(s.shouldProcessFrame(1100));
        s.onFrame(new CameraFrame(pixels,320,240,0,1100)); assertEquals(2, detections.get());
        assertTrue(s.shouldProcessFrame(3));
        s.onFrame(new CameraFrame(pixels,320,240,0,3)); assertFalse(s.shouldProcessFrame(4));
        s.reset(); assertTrue(s.shouldProcessFrame(3));
        s.onFrame(new CameraFrame(pixels,320,240,0,3)); assertEquals(4, detections.get());
        s.close(); assertFalse(s.shouldProcessFrame(10000));
    }

    @Test public void zeroIntervalStillRejectsDuplicateButAcceptsNextTimestamp() {
        faces = Collections.emptyList();
        FaceSession<String> s = session(Runnable::run, (f,r,k,c) -> () -> ALICE);
        frame(s); assertFalse(s.shouldProcessFrame(33)); assertTrue(s.shouldProcessFrame(34));
        assertTrue(s.finish(0)); s.reset(); assertFalse(s.shouldProcessFrame(34));
    }

    @Test public void resetDoesNotReleaseBusyEngineUntilUninterruptibleTaskActuallyExits() throws Exception {
        ExecutorService worker = Executors.newFixedThreadPool(2);
        CountDownLatch entered = new CountDownLatch(1), release = new CountDownLatch(1), exited = new CountDownLatch(1);
        AtomicInteger running = new AtomicInteger(), max = new AtomicInteger(), captures = new AtomicInteger();
        FaceSession<String> s = session(worker, (f,r,k,c) -> {
            captures.incrementAndGet();
            return () -> { int n = running.incrementAndGet(); max.accumulateAndGet(n, Math::max); entered.countDown();
                awaitUninterruptibly(release); running.decrementAndGet(); exited.countDown(); return ALICE; };
        });
        try {
            stable(s); await(entered); s.reset(); stable(s);
            assertEquals("reset cannot create a second task on the same engine",1,captures.get());
            release.countDown(); await(exited);
            assertTrue(s.finish(2000)); assertTrue(events.isEmpty()); assertEquals(1,max.get());
            assertFalse("external executor remains owned by caller",worker.isShutdown());
        } finally { release.countDown(); s.close(); worker.shutdownNow(); }
    }

    @Test public void timeoutRevokesPermanentlyWithoutWaitingForNativeOrShuttingExternalWorker() throws Exception {
        ExecutorService worker = Executors.newSingleThreadExecutor();
        CountDownLatch entered = new CountDownLatch(1), release = new CountDownLatch(1);
        FaceSession<String> s = session(worker, (f,r,k,c) -> () -> { entered.countDown(); awaitUninterruptibly(release); return ALICE; });
        try {
            stable(s); await(entered);
            assertFalse(s.finish(10)); s.reset(); assertFalse(s.shouldProcessFrame(1000));
            release.countDown(); worker.submit(() -> {}).get(2,TimeUnit.SECONDS);
            assertTrue(events.isEmpty()); assertFalse(s.finish(200));
            s.close(); s.close(); assertFalse(worker.isShutdown());
        } finally { release.countDown(); worker.shutdownNow(); }
    }

    @Test public void finishDrainsCascadeAndListenerAfterEofWithoutAnotherFrame() throws Exception {
        Manual worker = new Manual();
        FaceSession<String> s = session(worker,(f,r,k,c) -> () -> BORDERLINE)
                .withVerifier(new FaceSession.Verifier<>((f,r,k,c) -> () -> ALICE, () -> USERS,.9f,.3f,.65f));
        stable(s);
        FutureTask<Boolean> done = new FutureTask<>(() -> s.finish(2000)); new Thread(done).start();
        worker.run(); assertFalse(done.isDone()); worker.run();
        assertTrue(done.get(2,TimeUnit.SECONDS)); assertEquals(1,events.size());
        assertEquals(2,events.get(0).frameId); assertTrue(events.get(0).generation > 0);
        assertFalse(s.shouldProcessFrame(1000)); s.close();
    }

    @Test public void finishCountsBlockedCallbackAndDoesNotHoldSessionLock() throws Exception {
        ExecutorService worker = Executors.newSingleThreadExecutor();
        CountDownLatch callback = new CountDownLatch(1), release = new CountDownLatch(1);
        FaceSession.Listener<String> slow = new FaceSession.Listener<String>() {
            public void onFaces(List<FaceSession.FaceState<String>> f, CameraFrame frame) { }
            public void onRecognitionFailed(long id,float score,FaceSession.Reason reason) { }
            public void onRecognized(FaceSession.RecognitionEvent<String> e) { callback.countDown(); awaitUninterruptibly(release); }
        };
        config.livenessEnabled = false;
        FaceSession<String> s = new FaceSession<>(config,f->faces,(f,r)->.95f,(f,r,k,c)->()->ALICE,()->USERS,slow,worker);
        try {
            stable(s); await(callback);
            FutureTask<Boolean> done = new FutureTask<>(() -> s.finish(2000)); new Thread(done).start();
            s.reset(); // callback and finish cannot retain the session lock
            assertFalse(done.isDone()); release.countDown(); assertTrue(done.get(2,TimeUnit.SECONDS));
        } finally { release.countDown(); s.close(); worker.shutdownNow(); }
    }

    @Test public void finishCalledByOwnWorkerFailsFastAndCannotAuthorize() throws Exception {
        Manual worker = new Manual(); AtomicReference<FaceSession<String>> ref = new AtomicReference<>();
        FaceSession<String> s = session(worker,(f,r,k,c) -> () -> { assertFalse(ref.get().finish(2000)); return ALICE; });
        ref.set(s); stable(s); worker.run();
        assertTrue(events.isEmpty()); assertFalse(s.shouldProcessFrame(1000));
    }

    @Test public void finishWaitsForAcceptedDetectorButTimeoutCannotAuthorizeItsResult() throws Exception {
        CountDownLatch entered = new CountDownLatch(1), release = new CountDownLatch(1);
        FaceSession<String> s = new FaceSession<>(config, f -> { entered.countDown(); awaitUninterruptibly(release); return faces; },
                (f,r)->.95f, (f,r,k,c)->()->ALICE,()->USERS,listener,Runnable::run);
        Thread analysis = new Thread(() -> frame(s)); analysis.start(); await(entered);
        assertFalse(s.finish(10)); release.countDown(); analysis.join(2000);
        assertFalse(analysis.isAlive()); assertTrue(events.isEmpty()); assertFalse(s.shouldProcessFrame(1000));
    }

    @Test public void deletionWhileWaitingForLivenessRevokesMatchWithoutAnotherFrame() throws Exception {
        DatasetEpoch epoch = new DatasetEpoch(); Source source = new Source(epoch);
        Manual worker = new Manual(), live = new Manual();
        FaceSession<String> s = new FaceSession<>(config,f->faces,new AsyncLive(),(f,r,k,c)->()->ALICE,source,listener,worker,live);
        stable(s); worker.run(); live.run(); frame(s);
        long n = epoch.invalidate(); source.set(Gallery.empty(),epoch,n,true); epoch.markReady(n);
        live.run(); assertTrue(s.finish(0)); assertTrue(events.isEmpty());
        assertEquals("stale liveness result cannot contribute another vote",1,votes.size());
    }

    @Test public void failedRefreshRevokesEvenWhenOldGalleryObjectSurvives() throws Exception {
        DatasetEpoch epoch = new DatasetEpoch(); Source source = new Source(epoch); Manual worker = new Manual();
        FaceSession<String> s = new FaceSession<>(config,f->faces,(f,r)->.95f,(f,r,k,c)->()->ALICE,source,listener,worker);
        stable(s); epoch.invalidate(); worker.run();
        assertTrue(events.isEmpty()); assertTrue(s.finish(0));
    }

    @Test public void cascadeRequiresSameReadyEpochAndRechecksAtSecondStageCompletion() throws Exception {
        DatasetEpoch epoch = new DatasetEpoch(); Source primary = new Source(epoch), second = new Source(epoch);
        Manual worker = new Manual();
        FaceSession<String> s = new FaceSession<>(config,f->faces,(f,r)->.95f,(f,r,k,c)->()->BORDERLINE,primary,listener,worker)
                .withVerifier(new FaceSession.Verifier<>((f,r,k,c)->()->ALICE,second,.9f,.3f,.65f));
        long n = epoch.invalidate(); primary.set(USERS,epoch,n,true); epoch.markReady(n);
        stable(s); assertTrue("different epochs must not start features",worker.queue.isEmpty());
        second.set(USERS,epoch,n,true); stable(s); worker.run();
        assertEquals(1,worker.queue.size()); epoch.invalidate(); worker.run();
        assertTrue(events.isEmpty()); assertTrue(s.finish(0));
    }

    @Test public void newPublicationInvalidatesSucceededIdentityAndFusion() {
        DatasetEpoch epoch = new DatasetEpoch(); Source source = new Source(epoch);
        FaceSession<String> s = new FaceSession<>(config,f->faces,(f,r)->.95f,(f,r,k,c)->()->ALICE,source,listener,Runnable::run);
        stable(s); assertEquals(1,events.size());
        long n = epoch.invalidate(); source.set(Gallery.empty(),epoch,n,true); epoch.markReady(n); frame(s);
        assertNull(lastFaces.get(0).user); assertEquals(FaceSession.Recognition.PENDING,lastFaces.get(0).recognition);
    }

    @Test public void temporarilyLostTrackCannotPublishLateCascade() throws Exception {
        Manual worker = new Manual();
        FaceSession<String> s = session(worker,(f,r,k,c)->()->BORDERLINE)
                .withVerifier(new FaceSession.Verifier<>((f,r,k,c)->()->ALICE,()->USERS,.9f,.3f,.65f));
        stable(s); worker.run(); faces = Collections.emptyList(); frame(s); worker.run();
        assertTrue("even before tracker expiry, LOST cannot authorize",events.isEmpty());
        faces = FACE; stable(s); worker.run(); worker.run(); assertEquals(1,events.size());
    }

    @Test public void latestStrictSingleFaceGateBlocksAnAlreadyRunningCascade() throws Exception {
        config.strictSingleFace = true;
        Manual worker = new Manual();
        FaceSession<String> s = session(worker,(f,r,k,c)->()->BORDERLINE)
                .withVerifier(new FaceSession.Verifier<>((f,r,k,c)->()->ALICE,()->USERS,.9f,.3f,.65f));
        stable(s); worker.run();
        faces = Arrays.asList(FACE.get(0),new Detection(new FaceRect(190,50,290,150),1));
        frame(s); worker.run(); assertTrue(events.isEmpty());
    }

    @Test public void oldAttemptLivenessCannotContaminateFreshRetry() throws Exception {
        Manual worker = new Manual(), live = new Manual(); AtomicInteger calls = new AtomicInteger();
        FaceSession<String> s = new FaceSession<>(config,f->faces,new AsyncLive(),
                (f,r,k,c)->()->calls.incrementAndGet()==1?null:ALICE,()->USERS,listener,worker,live);
        stable(s); worker.run(); // first attempt fails while its liveness still runs
        frame(s); worker.run(); live.run(); assertTrue(votes.isEmpty()); assertTrue(events.isEmpty());
        frame(s); live.run(); frame(s); live.run();
        assertEquals(1,events.size()); assertEquals(2,votes.size());
    }

    @Test public void missingEofVoteStaysUnresolvedAndTailVoteIsObservable() throws Exception {
        Manual worker = new Manual(), live = new Manual();
        FaceSession<String> s = new FaceSession<>(config,f->faces,new AsyncLive(),(f,r,k,c)->()->ALICE,()->USERS,listener,worker,live);
        stable(s); worker.run(); live.run();
        assertTrue(s.finish(0)); assertTrue(events.isEmpty());
        assertEquals(Collections.singletonList(FaceSession.Liveness.UNKNOWN),votes);
        assertTrue(live.queue.isEmpty()); // finish never manufactures a second observation
    }

    @Test public void rejectedSubmissionAndTaskExceptionReleaseBarrierAndAllowRetry() throws Exception {
        AtomicInteger submitted = new AtomicInteger(); Manual worker = new Manual();
        Executor rejectOnce = r -> { if (submitted.incrementAndGet()==1) throw new RejectedExecutionException(); worker.execute(r); };
        AtomicInteger executions = new AtomicInteger();
        FaceSession<String> s = session(rejectOnce,(f,r,k,c)->()->{
            if (executions.incrementAndGet()==1) throw new IllegalStateException("feature failed"); return ALICE;
        });
        stable(s); worker.run(); frame(s); worker.run();
        assertEquals(1,events.size()); assertTrue(s.finish(0));
    }

    @Test public void capturePairFallbackPreservesLambdaAndCopiesBothBeforeInputReuse() throws Exception {
        config.shareFeatureCapture = true;
        Manual worker = new Manual(); AtomicInteger capture = new AtomicInteger(), verify = new AtomicInteger();
        FaceSession.FeatureEngine primary = (f,r,k,c) -> {
            capture.incrementAndGet(); int value=f.nv21[0]; return ()->{ assertEquals(100,value); return BORDERLINE; };
        };
        FaceSession.FeatureEngine slow = (f,r,k,c) -> {
            capture.incrementAndGet(); int value=f.nv21[0]; return ()->{ verify.incrementAndGet(); assertEquals(100,value); return ALICE; };
        };
        FaceSession<String> s = session(worker,primary).withVerifier(new FaceSession.Verifier<>(slow,()->USERS,.9f,.3f,.65f));
        stable(s); pixels[0]=0; worker.run(); worker.run();
        assertEquals(2,capture.get()); assertEquals(1,verify.get()); assertEquals(1,events.size());
    }

    @Test public void baselineDoesNotInvokePairOverrideAndCandidateDoes() {
        AtomicInteger pair = new AtomicInteger(), single = new AtomicInteger();
        FaceSession.FeatureEngine primary = new FaceSession.FeatureEngine() {
            public Callable<float[]> capture(CameraFrame f,FaceRect r,float[] k,FaceRect c) { single.incrementAndGet(); return ()->ALICE; }
            public FaceSession.CapturedFeatures capturePair(CameraFrame f,FaceRect r,float[] k,FaceRect c,FaceSession.FeatureEngine other) {
                pair.incrementAndGet(); return new FaceSession.CapturedFeatures(()->ALICE,()->ALICE,1,123);
            }
        };
        FaceSession.FeatureEngine slow = (f,r,k,c)->{ single.incrementAndGet(); return ()->ALICE; };
        FaceSession<String> baseline = session(Runnable::run,primary).withVerifier(new FaceSession.Verifier<>(slow,()->USERS,.9f,.3f,.65f));
        stable(baseline); assertEquals(0,pair.get()); assertEquals(2,single.get());
        config.shareFeatureCapture=true;
        FaceSession<String> candidate = session(Runnable::run,primary).withVerifier(new FaceSession.Verifier<>(slow,()->USERS,.9f,.3f,.65f));
        stable(candidate); assertEquals(1,pair.get()); assertEquals(2,single.get());
    }

    @Test public void resetKeepsIdentityCooldownWhileRollbackStartsANewClockDomain() {
        FaceSession<String> s = session(Runnable::run,(f,r,k,c)->()->ALICE);
        stable(s); assertEquals(1,events.size());
        s.reset(); stable(s); assertEquals(1,events.size());
        time=0; stable(s); assertEquals(2,events.size());
    }

    @Test public void publicationDoesNotWaitForUserCallbackOrReverseLockOrder() throws Exception {
        DatasetEpoch epoch = new DatasetEpoch(); Source source = new Source(epoch);
        CountDownLatch callback = new CountDownLatch(1), release = new CountDownLatch(1);
        ExecutorService worker = Executors.newSingleThreadExecutor();
        config.livenessEnabled=false;
        FaceSession.Listener<String> slow = new FaceSession.Listener<String>() {
            public void onFaces(List<FaceSession.FaceState<String>> f,CameraFrame frame) { }
            public void onRecognitionFailed(long id,float score,FaceSession.Reason reason) { }
            public void onRecognized(FaceSession.RecognitionEvent<String> e) { callback.countDown(); awaitUninterruptibly(release); }
        };
        FaceSession<String> s = new FaceSession<>(config,f->faces,(f,r)->.95f,(f,r,k,c)->()->ALICE,source,slow,worker);
        try {
            stable(s); await(callback);
            long n=epoch.invalidate(); source.set(Gallery.empty(),epoch,n,true); epoch.markReady(n);
            assertTrue(epoch.isCurrent(n)); // no callback under the short publication monitor
            assertFalse(s.finish(5));
        } finally { release.countDown(); s.close(); worker.shutdownNow(); }
    }
    @Test public void publicationDuringBlockedSearchWinsBeforeAuthorizationCommit() throws Exception {
        DatasetEpoch epoch = new DatasetEpoch(); Source source = new Source(epoch);
        CountDownLatch search = new CountDownLatch(1), release = new CountDownLatch(1);
        Gallery<String> blocking = Gallery.build(Collections.singletonList(new Gallery.Entry<>("alice",ALICE)),2,
                (probe,enrolled) -> { search.countDown(); awaitUninterruptibly(release); return 1f; });
        source.set(blocking,epoch,epoch.generation(),true);
        config.livenessEnabled=false;
        ExecutorService worker = Executors.newSingleThreadExecutor();
        FaceSession<String> s = new FaceSession<>(config,f->faces,(f,r)->.95f,(f,r,k,c)->()->ALICE,source,listener,worker);
        try {
            frame(s); frame(s); await(search);
            long n=epoch.invalidate(); source.set(Gallery.empty(),epoch,n,true); epoch.markReady(n);
            release.countDown(); assertTrue(s.finish(2000)); assertTrue(events.isEmpty());
        } finally { release.countDown(); s.close(); worker.shutdownNow(); }
    }

    @Test public void closeThenSuccessfulDrainCancelsLateResultWithoutOwningExternalExecutor() throws Exception {
        ExecutorService worker=Executors.newSingleThreadExecutor();
        CountDownLatch entered=new CountDownLatch(1), release=new CountDownLatch(1);
        FaceSession<String> s=session(worker,(f,r,k,c)->()->{
            entered.countDown(); awaitUninterruptibly(release); return ALICE;
        });
        try {
            stable(s); await(entered); s.close(); s.close(); release.countDown();
            assertTrue(s.finish(2000)); assertTrue(events.isEmpty()); assertFalse(worker.isShutdown());
        } finally { release.countDown(); worker.shutdownNow(); }
    }

    @Test public void resetCancelsQueuedOldFeatureAndOldLivenessWithoutReleasingSlotsEarly() throws Exception {
        Manual worker=new Manual(), live=new Manual(); AtomicInteger calls=new AtomicInteger();
        FaceSession<String> s=new FaceSession<>(config,f->faces,new AsyncLive(),
                (f,r,k,c)->()->{ calls.incrementAndGet(); return ALICE; },()->USERS,listener,worker,live);
        stable(s); s.reset(); stable(s);
        assertEquals(1,worker.queue.size()); assertEquals(1,live.queue.size());
        worker.run(); live.run(); assertEquals(0,calls.get()); assertTrue(votes.isEmpty());
        frame(s); worker.run(); live.run(); frame(s); live.run();
        assertEquals(1,events.size()); assertEquals(1,calls.get());
    }

}
