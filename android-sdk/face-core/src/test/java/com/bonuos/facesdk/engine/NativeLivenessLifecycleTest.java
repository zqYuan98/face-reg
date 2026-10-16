package com.bonuos.facesdk.engine;

import static org.junit.Assert.assertTrue;

import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import org.junit.Test;

/** Guards the Java lifecycle boundary without loading the Android native library on the host. */
public class NativeLivenessLifecycleTest {
    @Test public void nativeInferenceAndReleaseUseTheSameInstanceMonitor() throws Exception {
        Class<?> type = Class.forName("com.bonuos.facesdk.engine.NativeLiveness", false, getClass().getClassLoader());
        Method score = type.getDeclaredMethod("score", byte[].class, int.class, int.class, int.class,
                int.class, int.class, int.class, int.class);
        Method close = type.getDeclaredMethod("close");
        assertTrue("native inference must be protected from concurrent release",
                Modifier.isSynchronized(score.getModifiers()) && !Modifier.isStatic(score.getModifiers()));
        assertTrue("close must wait on the same object before deleting the native handle",
                Modifier.isSynchronized(close.getModifiers()) && !Modifier.isStatic(close.getModifiers()));
    }
}
