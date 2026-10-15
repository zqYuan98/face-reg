package com.bonuos.facebench.backend;

import android.content.Context;
import com.bonuos.facebench.BuildConfig;
import com.bonuos.facesdk.engine.NativeEngine;
import java.lang.reflect.InvocationTargetException;
import java.util.ArrayList;
import java.util.List;

/**
 * Creates backends by name. The ArcSoft backend is compiled only when the build was given a
 * local ArcSoft SDK path, so it is looked up reflectively and the app still builds without it.
 */
public final class BackendRegistry {
    public static final String LEGACY_AS_IS = "legacy_asis";
    public static final String LEGACY_FIXED = "legacy_fixed";
    public static final String ENGINE_CROP = "engine_crop";
    public static final String ENGINE_NATIVE = "engine_native";
    public static final String ENGINE_NATIVE_INT8 = "engine_native_int8";
    public static final String INSIGHTFACE_R50 = "insightface_r50_int8";
    public static final String INSIGHTFACE_MBF = "insightface_mbf";
    public static final String SESSION_R50 = "session_r50_int8";
    public static final String SESSION_MBF = "session_mbf";
    public static final String ARCSOFT = "arcsoft";
    private static final String ARCSOFT_CLASS = "com.bonuos.facebench.arcsoft.ArcSoftBackend";
    public static final String SESSION_ARCSOFT = "session_arcsoft";
    private static final String ARCSOFT_SESSION_CLASS = "com.bonuos.facebench.arcsoft.ArcSoftSessionBackend";

    private BackendRegistry() { }

    public static List<String> available() {
        List<String> names = new ArrayList<>();
        names.add(LEGACY_AS_IS);
        names.add(LEGACY_FIXED);
        names.add(ENGINE_CROP);
        names.add(ENGINE_NATIVE);
        names.add(ENGINE_NATIVE_INT8);
        if (BuildConfig.INSIGHTFACE_AVAILABLE) {
            names.add(INSIGHTFACE_R50);
            names.add(INSIGHTFACE_MBF);
            names.add(SESSION_R50);
            names.add(SESSION_MBF);
        }
        if (BuildConfig.ARCSOFT_AVAILABLE) {
            names.add(ARCSOFT);
            names.add(SESSION_ARCSOFT);
        }
        return names;
    }

    public static FaceBackend create(Context context, String name, BenchConfig config) throws Exception {
        switch (name) {
            case LEGACY_AS_IS: return new LegacyBackend(context, true);
            case LEGACY_FIXED: return new LegacyBackend(context, false);
            case ENGINE_CROP: return new EngineCropBackend(context);
            case ENGINE_NATIVE: return new NativeEngineBackend(context, false);
            case ENGINE_NATIVE_INT8: return new NativeEngineBackend(context, true);
            case INSIGHTFACE_R50: return new InsightFaceBackend(context, INSIGHTFACE_R50, NativeEngine.INSIGHTFACE_R50_INT8);
            case INSIGHTFACE_MBF: return new InsightFaceBackend(context, INSIGHTFACE_MBF, NativeEngine.INSIGHTFACE_MBF);
            case SESSION_R50: return new SessionBackend(context, SESSION_R50, NativeEngine.INSIGHTFACE_R50_INT8);
            case SESSION_MBF: return new SessionBackend(context, SESSION_MBF, NativeEngine.INSIGHTFACE_MBF);
            case ARCSOFT:
                requireArcSoft();
                return (FaceBackend) unwrap(() -> Class.forName(ARCSOFT_CLASS)
                        .getConstructor(Context.class, float[].class, boolean.class)
                        .newInstance(context, config.arcSoftLivenessThresholds, config.arcSoftIdCardModel));
            case SESSION_ARCSOFT:
                requireArcSoft();
                return (FaceBackend) unwrap(() -> Class.forName(ARCSOFT_SESSION_CLASS)
                        .getConstructor(Context.class, boolean.class)
                        .newInstance(context, config.arcSoftIdCardModel));
            default: throw new IllegalArgumentException("Unknown backend " + name);
        }
    }

    /** Returns ArcSoft's activation code; see {@link #isArcSoftActivationSuccess(int)}. */
    public static int activateArcSoft(Context context, String appId, String sdkKey, String activeKey) throws Exception {
        requireArcSoft();
        return (Integer) unwrap(() -> Class.forName(ARCSOFT_CLASS)
                .getMethod("activate", Context.class, String.class, String.class, String.class)
                .invoke(null, context, appId, sdkKey, activeKey));
    }

    public static boolean isArcSoftActivationSuccess(int code) throws Exception {
        requireArcSoft();
        return (Boolean) unwrap(() -> Class.forName(ARCSOFT_CLASS)
                .getMethod("isActivationSuccess", int.class).invoke(null, code));
    }

    private static void requireArcSoft() {
        if (!BuildConfig.ARCSOFT_AVAILABLE)
            throw new IllegalStateException("This build has no ArcSoft SDK; rebuild with -Parcsoft.sdk.libs=<path to ArcSoft libs>");
    }

    private interface Reflective { Object call() throws Exception; }

    private static Object unwrap(Reflective call) throws Exception {
        try {
            return call.call();
        } catch (InvocationTargetException error) {
            Throwable cause = error.getCause();
            if (cause instanceof Exception) throw (Exception) cause;
            if (cause instanceof Error) throw (Error) cause;
            throw error;
        }
    }
}
