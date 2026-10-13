package com.bonuos.facebench.clip;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.Arrays;
import java.util.List;

/** Sidecar label for one recorded attempt. A clip is one presentation in ISO/IEC 30107-3 terms. */
public final class ClipMeta {
    public static final String LIVE = "live";
    public static final String ATTACK = "attack";
    /** Attack families reported separately; parity must hold for each one. */
    public static final List<String> ATTACK_TYPES = Arrays.asList("print", "screen", "replay", "other");

    private static final Gson GSON = new GsonBuilder().setPrettyPrinting().create();

    public String clipId;
    public String subjectId;
    public String label;
    public String attackType;
    public String attackMedium;
    public String lighting;
    public String notes;
    public String deviceManufacturer;
    public String deviceModel;
    public int width;
    public int height;
    /** Degrees the sensor image was rotated clockwise to become upright before storage. */
    public int sensorRotationDegrees;
    public boolean frontCamera;
    public int frameCount;
    public long durationMs;
    public long createdAtEpochMs;

    public void validate() {
        if (clipId == null || !clipId.matches("[A-Za-z0-9._-]{1,120}")) throw new IllegalArgumentException("Invalid clip id");
        if (subjectId == null || !subjectId.matches("[A-Za-z0-9._-]{1,64}")) throw new IllegalArgumentException("Subject id must be letters, digits, dot, dash or underscore");
        if (LIVE.equals(label)) {
            if (attackType != null) throw new IllegalArgumentException("Live clips have no attack type");
        } else if (ATTACK.equals(label)) {
            if (!ATTACK_TYPES.contains(attackType)) throw new IllegalArgumentException("Unknown attack type");
        } else {
            throw new IllegalArgumentException("Label must be live or attack");
        }
    }

    public void write(File file) throws IOException {
        validate();
        Files.write(file.toPath(), GSON.toJson(this).getBytes(StandardCharsets.UTF_8));
    }

    public static ClipMeta read(File file) throws IOException {
        ClipMeta meta = GSON.fromJson(new String(Files.readAllBytes(file.toPath()), StandardCharsets.UTF_8), ClipMeta.class);
        if (meta == null) throw new IOException("Empty metadata " + file.getName());
        meta.validate();
        return meta;
    }
}
