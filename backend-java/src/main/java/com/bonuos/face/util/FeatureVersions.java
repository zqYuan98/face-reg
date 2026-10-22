package com.bonuos.face.util;

import java.util.List;

/** Feature versions the algorithm service produces. Features of different versions never compare. */
public final class FeatureVersions {
    /** SeetaFace-style recognizer; stored in sys_user.feature_data and synced to v1 devices. */
    public static final String V1 = "recognizer_v1_1024";
    /** SCRFD + ArcFace IResNet-50 (research-licensed weights); stored in sys_user_feature. */
    public static final String V2 = "arcface_r50_512";
    public static final List<String> ALL = List.of(V1, V2);

    private FeatureVersions() {}

    public static int dimension(String version) {
        if (V1.equals(version)) return 1024;
        if (V2.equals(version)) return 512;
        throw new IllegalArgumentException("Unsupported feature version " + version);
    }

    public static String require(String version) {
        dimension(version);
        return version;
    }
}
