package com.bonuos.facesdk.engine;

import static org.junit.Assert.*;
import java.lang.reflect.Field;
import java.util.LinkedHashMap;
import java.util.Map;
import org.junit.Test;

public class AnalysisProfileTest {
    @Test public void presetsDifferOnlyInNameAndSharedCapture() {
        AnalysisProfile baseline = AnalysisProfile.forVariant("baseline");
        AnalysisProfile shared = AnalysisProfile.forVariant("shared_capture");
        assertFalse(baseline.shareFeatureCapture);
        assertTrue(shared.shareFeatureCapture);
        Map<String, Object> a = new LinkedHashMap<>(baseline.toMap());
        Map<String, Object> b = new LinkedHashMap<>(shared.toMap());
        a.remove("variant"); b.remove("variant");
        a.remove("shareFeatureCapture"); b.remove("shareFeatureCapture");
        assertEquals(a, b);
        assertEquals("detector_stages_v1", baseline.timingLevel);
        assertFalse(baseline.earlyFrameAdmission);
        assertEquals(320, baseline.detectorLongSide);
        assertEquals(160, baseline.regionLongSide);
        assertEquals(10, baseline.fullScanEvery);
        assertEquals(2, baseline.detectorThreads);
        assertEquals(4, baseline.featureThreads);
        assertEquals(2, baseline.livenessThreads);
        assertEquals(1, baseline.landmarkThreads);
        assertEquals(.5f, baseline.detectorThreshold, 0f);
        assertEquals(.2f, baseline.regionMinIou, 0f);
        assertEquals(2f, baseline.regionScale, 0f);
    }

    @Test public void serializationIsExplicitAndImmutable() {
        AnalysisProfile profile = AnalysisProfile.forVariant("baseline");
        Map<String, Object> snapshot = profile.toMap();
        assertEquals(15, snapshot.size());
        assertEquals(1, snapshot.get("version"));
        assertEquals("baseline", snapshot.get("variant"));
        assertEquals(false, snapshot.get("shareFeatureCapture"));
        assertEquals(profile.detectorLongSide, snapshot.get("detectorLongSide"));
        assertEquals(profile.detectorThreshold, snapshot.get("detectorThreshold"));
        assertEquals(profile.regionScale, snapshot.get("regionScale"));
        assertThrows(UnsupportedOperationException.class, () -> snapshot.put("version", 2));
        assertEquals(snapshot, profile.toMap());
    }

    @Test public void applyOnlyChangesCaptureNotSecurityPolicy() throws Exception {
        SessionConfig config = new SessionConfig();
        config.thresholdHigh = .8f;
        config.livenessRequired = 3;
        config.stableFrames = 7;
        config.strictSingleFace = true;
        SessionConfig before = config.clone();
        AnalysisProfile.forVariant("shared_capture").applyTo(config);
        assertTrue(config.shareFeatureCapture);
        for (Field field : SessionConfig.class.getFields()) {
            if (!field.getName().equals("shareFeatureCapture"))
                assertEquals(field.getName(), field.get(before), field.get(config));
        }
        AnalysisProfile.forVariant("baseline").applyTo(config);
        assertFalse(config.shareFeatureCapture);
        assertEquals(3, config.livenessRequired);
        assertEquals(.8f, config.thresholdHigh, 0f);
    }

    @Test public void unknownEmptyOrNullVariantNeverFallsBack() {
        for (String value : new String[] {null, "", "BASELINE", " baseline", "shared_captur"})
            assertThrows(IllegalArgumentException.class, () -> AnalysisProfile.forVariant(value));
        assertThrows(IllegalArgumentException.class, () -> AnalysisProfile.forVariant("baseline").applyTo(null));
    }
}
