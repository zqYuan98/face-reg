package com.bonuos.facebench.bench;

import static org.junit.Assert.*;
import com.bonuos.facesdk.engine.AnalysisProfile;
import java.io.ByteArrayInputStream;
import java.nio.charset.StandardCharsets;
import org.junit.Test;

public class AnalysisProfileFingerprintTest {
    // Same canonical payload is asserted by the Python v3 audit tests, including float spelling.
    private static final String BASELINE = "{\"detectorLongSide\":320,\"detectorThreads\":2,"
            + "\"detectorThreshold\":0.5,\"earlyFrameAdmission\":false,\"featureThreads\":4,"
            + "\"fullScanEvery\":10,\"landmarkThreads\":1,\"livenessThreads\":2,\"regionLongSide\":160,"
            + "\"regionMinIou\":0.2,\"regionScale\":2.0,\"shareFeatureCapture\":false,"
            + "\"timingLevel\":\"detector_stages_v1\",\"variant\":\"baseline\",\"version\":1}";

    @Test public void baselineFingerprintMatchesCrossLanguageCanonicalPayload() throws Exception {
        assertFingerprint("baseline", BASELINE);
    }

    @Test public void sharedCaptureFingerprintChangesOnlyTheIntendedFields() throws Exception {
        String shared = BASELINE.replace("\"shareFeatureCapture\":false", "\"shareFeatureCapture\":true")
                .replace("\"variant\":\"baseline\"", "\"variant\":\"shared_capture\"");
        assertFingerprint("shared_capture", shared);
        assertNotEquals(HashManifest.fingerprint(AnalysisProfile.forVariant("baseline").toMap()),
                HashManifest.fingerprint(AnalysisProfile.forVariant("shared_capture").toMap()));
    }

    private static void assertFingerprint(String variant, String expected) throws Exception {
        Object digest = HashManifest.stream(new ByteArrayInputStream(expected.getBytes(StandardCharsets.UTF_8))).get("sha256");
        assertEquals(digest, HashManifest.fingerprint(AnalysisProfile.forVariant(variant).toMap()));
    }
}
