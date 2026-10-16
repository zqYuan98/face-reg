package com.bonuos.facebench.bench;

import static org.junit.Assert.*;
import java.io.ByteArrayInputStream;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.Map;
import org.junit.Test;

public class HashManifestTest {
    @Test public void sha256IncludesExactBytes() throws Exception {
        Map<String, Object> result = HashManifest.stream(new ByteArrayInputStream("abc".getBytes(StandardCharsets.UTF_8)));
        assertEquals("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", result.get("sha256"));
        assertEquals(3L, result.get("bytes"));
    }
    @Test public void configFingerprintIsCanonicalButNotInsensitiveToChanges() {
        Map<String, Object> a = new LinkedHashMap<>(), b = new LinkedHashMap<>();
        a.put("b", 2); a.put("a", 1);
        b.put("a", 1); b.put("b", 2);
        assertEquals(HashManifest.fingerprint(a), HashManifest.fingerprint(b));
        assertEquals("43258cff783fe7036d8a43033f830adfc60ec037382473548ac742b888292777", HashManifest.fingerprint(a));
        b.put("b", 3);
        assertNotEquals(HashManifest.fingerprint(a), HashManifest.fingerprint(b));
    }
}
