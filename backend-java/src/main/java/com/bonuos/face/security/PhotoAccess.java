package com.bonuos.face.security;

import jakarta.servlet.http.HttpServletRequest;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.charset.StandardCharsets;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.ResultSetExtractor;
import org.springframework.security.core.Authentication;
import org.springframework.stereotype.Component;
import org.springframework.web.util.UriUtils;

@Component
public class PhotoAccess {
    private final JdbcTemplate jdbc;
    private final Path root;
    private final int retentionDays;
    public PhotoAccess(JdbcTemplate jdbc, @Value("${spring.servlet.multipart.location:./uploads}") String directory,
                       @Value("${face.sync.retention-days:7}") int retentionDays) {
        if (retentionDays < 1 || retentionDays > 365) throw new IllegalArgumentException("Invalid sync retention period");
        this.jdbc = jdbc;
        this.root = Path.of(directory).toAbsolutePath().normalize();
        this.retentionDays = retentionDays;
    }
    public boolean allowed(Authentication authentication, HttpServletRequest request) {
        String path = DeviceAuthenticationFilter.path(request);
        String filename = safeFilename(path);
        if (filename == null) return false;
        Path photo;
        try { photo = root.resolve(filename).normalize(); }
        catch (java.nio.file.InvalidPathException ex) { return false; }
        if (!root.equals(photo.getParent()) || Files.isSymbolicLink(photo)) return false;
        if (authentication == null || !authentication.isAuthenticated()) return false;
        if (authentication.getAuthorities().stream().anyMatch(a -> a.getAuthority().equals("ROLE_ADMIN"))) return true;
        if (!(authentication.getPrincipal() instanceof DevicePrincipal device)) return false;
        if (device.allGroups()) return true;
        if (device.groupIds().isEmpty()) return false;
        List<Object> arguments = new ArrayList<>();
        arguments.add(filename);
        arguments.add(filename);
        arguments.addAll(device.groupIds());
        String placeholders = String.join(",", Collections.nCopies(device.groupIds().size(), "?"));
        if (exists("SELECT id FROM sys_user WHERE photo_url = ? AND CAST(photo_url AS BINARY)=CAST(? AS BINARY) "
                + "AND status = 1 AND group_id IN ("
                + placeholders + ") LIMIT 1", arguments)) return true;
        // A row may have changed after a device fixed U. Retained before/after photos are readable
        // only while that device still needs the event, under the same current epoch and scope.
        return retainedPhoto(filename, device, false, placeholders)
                || retainedPhoto(filename, device, true, placeholders);
    }

    private boolean retainedPhoto(String filename, DevicePrincipal device, boolean before, String placeholders) {
        String photoColumn = before ? "old_photo_url" : "photo_url";
        String groupColumn = before ? "old_group_id" : "new_group_id";
        List<Object> arguments = new ArrayList<>();
        arguments.add(filename);
        arguments.add(filename);
        arguments.addAll(device.groupIds());
        arguments.add(device.deviceSn());
        arguments.add(device.scopeVersion());
        arguments.add(device.deviceSn());
        arguments.add(device.scopeVersion());
        arguments.add(-retentionDays);
        // Separate indexed probes avoid an OR across the before/after filename indexes. Each
        // statement checks its authorization and event in one DB snapshot, not two racing reads.
        String sql = "SELECT c.revision FROM user_change_log c JOIN sync_revision r ON r.id=1 "
                + "WHERE c." + photoColumn + "=? AND CAST(c." + photoColumn + " AS BINARY)=CAST(? AS BINARY) "
                + "AND c." + groupColumn + " IN (" + placeholders + ") "
                + "AND c.revision>r.retention_floor AND c.revision<=r.head AND ("
                + "EXISTS (SELECT 1 FROM sync_lease l WHERE l.device_sn=? AND l.scope_version=? AND l.epoch=r.epoch "
                + "AND l.expires_at>CURRENT_TIMESTAMP(3) AND l.start_revision>=r.retention_floor AND l.start_revision<c.revision) "
                + "OR EXISTS (SELECT 1 FROM sync_device_cursor d WHERE d.device_sn=? AND d.scope_version=? AND d.epoch=r.epoch "
                + "AND d.ack_revision>=r.retention_floor AND d.ack_revision<c.revision "
                + "AND d.updated_at>=TIMESTAMPADD(DAY,?,CURRENT_TIMESTAMP(3)))) LIMIT 1";
        return exists(sql, arguments);
    }

    private boolean exists(String sql, List<Object> arguments) {
        // This path is outside MyBatis: explicitly bound JDBC work instead of inheriting no timeout.
        return Boolean.TRUE.equals(jdbc.query(connection -> {
            var statement = connection.prepareStatement(sql);
            try {
                statement.setQueryTimeout(3);
                statement.setMaxRows(1);
                for (int i = 0; i < arguments.size(); i++) statement.setObject(i + 1, arguments.get(i));
                return statement;
            } catch (SQLException ex) { statement.close(); throw ex; }
        }, (ResultSetExtractor<Boolean>) ResultSet::next));
    }

    private static String safeFilename(String path) {
        if (!path.startsWith("/uploads/") || path.length() > 4096) return null;
        String filename;
        try {
            // URI decoding preserves a literal '+', unlike form URLDecoder.
            filename = UriUtils.decode(path.substring("/uploads/".length()), StandardCharsets.UTF_8);
        } catch (IllegalArgumentException ex) { return null; }
        if (filename.isEmpty() || filename.length() > 255 || filename.startsWith(".")
                || filename.codePoints().anyMatch(c -> Character.isISOControl(c) || "/\\:%<>\"|?*".indexOf(c) >= 0)
                || !filename.matches("(?i).+\\.(jpg|jpeg|png|webp|bmp|gif)")) return null;
        return filename;
    }
}
