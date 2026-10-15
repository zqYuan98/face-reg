package com.bonuos.facebench.arcsoft;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Rect;
import com.arcsoft.face.ErrorInfo;
import com.arcsoft.face.FaceEngine;
import com.arcsoft.face.FaceFeature;
import com.arcsoft.face.FaceInfo;
import com.arcsoft.face.FaceSimilar;
import com.arcsoft.face.LivenessInfo;
import com.arcsoft.face.LivenessParam;
import com.arcsoft.face.VersionInfo;
import com.arcsoft.face.enums.CompareModel;
import com.arcsoft.face.enums.DetectFaceOrientPriority;
import com.arcsoft.face.enums.DetectMode;
import com.arcsoft.face.util.ImageUtils;
import com.bonuos.facebench.backend.FaceBackend;
import com.bonuos.facebench.backend.FrameOutcome;
import com.bonuos.facebench.backend.Timer;
import com.bonuos.facebench.clip.ClipFile;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * ArcSoft ArcFace V5.0 used strictly through its public API, organised like its own demo:
 * a VIDEO tracking engine, an IMAGE liveness engine, an IMAGE recognition engine and an
 * IMAGE enrollment engine. Benchmark use only; never shipped in a product.
 *
 * <p>RGB liveness exposes only ALIVE / NOT_ALIVE / UNKNOWN, so the error-rate curve comes from
 * re-running {@code process} with several thresholds via {@code setLivenessParam}.
 */
public final class ArcSoftBackend implements FaceBackend {
    public static final float DEFAULT_LIVENESS_THRESHOLD = 0.5f;
    private static final int MAX_FACES = 5;

    private final Context context;
    private final float[] sweepThresholds;
    private final CompareModel compareModel;
    private FaceEngine tracker;
    private final FaceEngine liveness = new FaceEngine();
    private final FaceEngine recognizer = new FaceEngine();
    private final FaceEngine enroller = new FaceEngine();
    private String version = "unknown";

    /**
     * @param sweepThresholds liveness thresholds to evaluate per frame; the default 0.5 is always included and timed
     * @param idCardModel use CompareModel.ID_CARD (reference is an ID-card chip photo) instead of LIFE_PHOTO
     */
    public ArcSoftBackend(Context context, float[] sweepThresholds, boolean idCardModel) {
        this.context = context.getApplicationContext();
        this.sweepThresholds = sweepThresholds == null ? new float[0] : sweepThresholds.clone();
        this.compareModel = idCardModel ? CompareModel.ID_CARD : CompareModel.LIFE_PHOTO;
        VersionInfo info = new VersionInfo();
        if (FaceEngine.getVersion(info) == ErrorInfo.MOK) version = info.getVersion();
        check(liveness.init(this.context, DetectMode.ASF_DETECT_MODE_IMAGE, DetectFaceOrientPriority.ASF_OP_0_ONLY,
                MAX_FACES, FaceEngine.ASF_LIVENESS), "liveness engine init");
        check(recognizer.init(this.context, DetectMode.ASF_DETECT_MODE_IMAGE, DetectFaceOrientPriority.ASF_OP_0_ONLY,
                MAX_FACES, FaceEngine.ASF_FACE_RECOGNITION), "recognition engine init");
        check(enroller.init(this.context, DetectMode.ASF_DETECT_MODE_IMAGE, DetectFaceOrientPriority.ASF_OP_0_ONLY,
                MAX_FACES, FaceEngine.ASF_FACE_DETECT | FaceEngine.ASF_FACE_RECOGNITION), "enrollment engine init");
    }

    /**
     * Online activation; MOK and ALREADY_ACTIVATED both mean the device is licensed. ArcFacePro
     * takes a per-device ACTIVE_KEY as well (activeOnline(context, activeKey, appId, sdkKey)); the
     * ArcFaceS edition has no such parameter and binds the APP_ID to package name and signature.
     * Reflection picks whichever the linked SDK provides, so the bench builds against either.
     */
    public static int activate(Context context, String appId, String sdkKey, String activeKey) throws Exception {
        Context app = context.getApplicationContext();
        try {
            java.lang.reflect.Method pro = FaceEngine.class.getMethod("activeOnline", Context.class, String.class, String.class, String.class);
            return (Integer) pro.invoke(null, app, activeKey, appId, sdkKey);
        } catch (NoSuchMethodException standardEdition) {
            java.lang.reflect.Method standard = FaceEngine.class.getMethod("activeOnline", Context.class, String.class, String.class);
            return (Integer) standard.invoke(null, app, appId, sdkKey);
        }
    }

    public static boolean isActivationSuccess(int code) {
        return code == ErrorInfo.MOK || code == ErrorInfo.MERR_ASF_ALREADY_ACTIVATED;
    }

    @Override public String name() { return "arcsoft"; }

    @Override public String describe() {
        return "ArcSoft ArcFace " + version + "; tracker VIDEO/ASF_OP_0_ONLY, liveness/recognition IMAGE engines"
                + "; compare model " + compareModel + "; default liveness threshold " + DEFAULT_LIVENESS_THRESHOLD;
    }

    @Override
    public void beginClip(int sensorRotationDegrees) {
        // Re-initialise the VIDEO engine so tracking state never carries across clips.
        if (tracker != null) tracker.unInit();
        tracker = new FaceEngine();
        check(tracker.init(context, DetectMode.ASF_DETECT_MODE_VIDEO, DetectFaceOrientPriority.ASF_OP_0_ONLY,
                MAX_FACES, FaceEngine.ASF_FACE_DETECT), "tracking engine init");
    }

    @Override
    public FrameOutcome process(ClipFile.Frame frame, boolean runLiveness, boolean runFeature) {
        FrameOutcome outcome = new FrameOutcome();
        if (frame.width % 4 != 0) {
            outcome.note = "ArcSoft requires width divisible by 4";
            return outcome;
        }
        List<FaceInfo> faces = new ArrayList<>();
        Timer timer = Timer.start();
        int code = tracker.detectFaces(frame.nv21, frame.width, frame.height, FaceEngine.CP_PAF_NV21, faces);
        outcome.detectMs = timer.lapMs();
        if (code != ErrorInfo.MOK) {
            outcome.note = "detectFaces error " + code;
            return outcome;
        }
        outcome.faceCount = faces.size();
        if (faces.isEmpty()) {
            outcome.livenessStatus = FrameOutcome.LIVE_UNKNOWN;
            return outcome;
        }
        FaceInfo face = largest(faces);
        Rect rect = face.getRect();
        outcome.box = new int[]{rect.left, rect.top, rect.right, rect.bottom};
        List<FaceInfo> single = Collections.singletonList(face);

        if (runLiveness) {
            timer.lapMs();
            outcome.livenessStatus = liveness(frame, single, DEFAULT_LIVENESS_THRESHOLD);
            outcome.livenessMs = timer.lapMs();
            outcome.livenessStatusByThreshold.put(DEFAULT_LIVENESS_THRESHOLD, outcome.livenessStatus);
            for (float threshold : sweepThresholds) {
                if (threshold == DEFAULT_LIVENESS_THRESHOLD) continue;
                outcome.livenessStatusByThreshold.put(threshold, liveness(frame, single, threshold));
            }
        }
        if (runFeature) {
            outcome.featureAttempted = true;
            FaceFeature feature = new FaceFeature();
            timer.lapMs();
            code = extract(recognizer, frame.nv21, frame.width, frame.height, FaceEngine.CP_PAF_NV21, face, false, feature);
            outcome.featureMs = timer.lapMs();
            if (code == ErrorInfo.MOK) outcome.feature = feature;
            else outcome.note = "extractFaceFeature error " + code;
        }
        return outcome;
    }

    private int liveness(ClipFile.Frame frame, List<FaceInfo> single, float threshold) {
        if (liveness.setLivenessParam(livenessParam(threshold)) != ErrorInfo.MOK) return FrameOutcome.LIVE_ERROR;
        int code = liveness.process(frame.nv21, frame.width, frame.height, FaceEngine.CP_PAF_NV21, single, FaceEngine.ASF_LIVENESS);
        if (code != ErrorInfo.MOK) return FrameOutcome.LIVE_ERROR;
        List<LivenessInfo> result = new ArrayList<>();
        if (liveness.getLiveness(result) != ErrorInfo.MOK || result.isEmpty()) return FrameOutcome.LIVE_ERROR;
        // ArcSoft's LivenessInfo codes are the shared status codes in FrameOutcome.
        return result.get(0).getLiveness();
    }

    // ---- ArcFaceS / ArcFacePro differences, resolved once by reflection so either jar compiles ----

    private static final java.lang.reflect.Method PRO_EXTRACT;
    private static final Object PRO_REGISTER, PRO_RECOGNIZE;
    private static final int PRO_MASK_NOT_WORN;
    private static final java.lang.reflect.Constructor<LivenessParam> LIVENESS_PRO, LIVENESS_STANDARD;

    static {
        java.lang.reflect.Method extract = null;
        Object register = null, recognize = null;
        int notWorn = 0;
        try {
            Class<?> type = Class.forName("com.arcsoft.face.enums.ExtractType");
            extract = FaceEngine.class.getMethod("extractFaceFeature", byte[].class, int.class, int.class, int.class,
                    FaceInfo.class, type, int.class, FaceFeature.class);
            register = type.getField("REGISTER").get(null);
            recognize = type.getField("RECOGNIZE").get(null);
            notWorn = Class.forName("com.arcsoft.face.MaskInfo").getField("NOT_WORN").getInt(null);
        } catch (ReflectiveOperationException standardEdition) {
            extract = null;
        }
        PRO_EXTRACT = extract;
        PRO_REGISTER = register;
        PRO_RECOGNIZE = recognize;
        PRO_MASK_NOT_WORN = notWorn;
        java.lang.reflect.Constructor<LivenessParam> pro = null, standard = null;
        try {
            pro = LivenessParam.class.getConstructor(float.class, float.class);
        } catch (NoSuchMethodException e) {
            try {
                standard = LivenessParam.class.getConstructor(float.class);
            } catch (NoSuchMethodException impossible) {
                throw new IllegalStateException("Unsupported ArcSoft LivenessParam", impossible);
            }
        }
        LIVENESS_PRO = pro;
        LIVENESS_STANDARD = standard;
    }

    /** Pro: ExtractType REGISTER for enrolment photos, RECOGNIZE for probes, mask NOT_WORN (as its demo). */
    static int extract(FaceEngine engine, byte[] data, int width, int height, int format, FaceInfo face,
                               boolean register, FaceFeature feature) {
        try {
            if (PRO_EXTRACT != null) {
                return (Integer) PRO_EXTRACT.invoke(engine, data, width, height, format, face,
                        register ? PRO_REGISTER : PRO_RECOGNIZE, PRO_MASK_NOT_WORN, feature);
            }
            return (Integer) FaceEngine.class.getMethod("extractFaceFeature", byte[].class, int.class, int.class, int.class,
                    FaceInfo.class, FaceFeature.class).invoke(engine, data, width, height, format, face, feature);
        } catch (ReflectiveOperationException error) {
            throw new IllegalStateException("ArcSoft extractFaceFeature unavailable", error);
        }
    }

    /** Pro takes an IR threshold too; no IR camera here, so its own default (0.7) is passed. */
    static LivenessParam livenessParam(float rgbThreshold) {
        try {
            return LIVENESS_PRO != null ? LIVENESS_PRO.newInstance(rgbThreshold, 0.7f) : LIVENESS_STANDARD.newInstance(rgbThreshold);
        } catch (ReflectiveOperationException error) {
            throw new IllegalStateException("ArcSoft LivenessParam unavailable", error);
        }
    }

    private static FaceInfo largest(List<FaceInfo> faces) {
        FaceInfo best = faces.get(0);
        for (FaceInfo face : faces) {
            if (face.getRect().width() * face.getRect().height() > best.getRect().width() * best.getRect().height()) best = face;
        }
        return best;
    }

    @Override
    public Object enroll(Bitmap photo) {
        Bitmap aligned = ImageUtils.alignBitmapForBgr24(photo);
        if (aligned == null) return null;
        byte[] bgr = ImageUtils.bitmapToBgr24(aligned);
        List<FaceInfo> faces = new ArrayList<>();
        if (enroller.detectFaces(bgr, aligned.getWidth(), aligned.getHeight(), FaceEngine.CP_PAF_BGR24, faces) != ErrorInfo.MOK
                || faces.isEmpty()) return null;
        FaceFeature feature = new FaceFeature();
        int code = extract(enroller, bgr, aligned.getWidth(), aligned.getHeight(), FaceEngine.CP_PAF_BGR24,
                largest(faces), true, feature);
        return code == ErrorInfo.MOK ? feature : null;
    }

    @Override
    public float compare(Object probe, Object reference) {
        FaceSimilar similar = new FaceSimilar();
        int code = recognizer.compareFaceFeature((FaceFeature) probe, (FaceFeature) reference, compareModel, similar);
        if (code != ErrorInfo.MOK) throw new IllegalStateException("compareFaceFeature error " + code);
        return similar.getScore();
    }

    @Override
    public void close() {
        if (tracker != null) tracker.unInit();
        liveness.unInit();
        recognizer.unInit();
        enroller.unInit();
    }

    private static void check(int code, String step) {
        if (code != ErrorInfo.MOK) throw new IllegalStateException("ArcSoft " + step + " failed: " + code);
    }
}
