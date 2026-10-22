package com.bonuos.face.service;

import com.bonuos.face.exception.ApiException;
import com.bonuos.face.mapper.UserMapper;
import com.baomidou.mybatisplus.core.conditions.query.QueryWrapper;
import com.bonuos.face.entity.User;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import java.nio.file.*;
import java.io.IOException;
import java.util.UUID;

/** New uploads never use a client supplied filename. Durable markers make cleanup retryable. */
@Service
public class PhotoStorage {
    private static final Logger log = LoggerFactory.getLogger(PhotoStorage.class);
    private final Path root;
    private final UserMapper users;
    @Value("${face.photos.cleanup-grace-ms:86400000}")
    private long graceMillis;
    public PhotoStorage(@Value("${spring.servlet.multipart.location:./uploads}") String path, UserMapper users) {
        this.root = Path.of(path).toAbsolutePath().normalize(); this.users = users;
    }
    public Path stage(MultipartFile photo) {
        if (photo == null || photo.isEmpty()) throw new ApiException(HttpStatus.BAD_REQUEST, "PHOTO_REQUIRED", "请上传人脸照片");
        try {
            Files.createDirectories(root.resolve(".staging"));
            Path path = root.resolve(".staging").resolve(UUID.randomUUID() + ".jpg");
            try { photo.transferTo(path); return path; }
            catch (Exception e) { Files.deleteIfExists(path); throw e; }
        } catch (IOException e) { throw new ApiException(HttpStatus.SERVICE_UNAVAILABLE, "PHOTO_STORAGE_FAILED", "照片保存失败，请稍后重试"); }
    }
    public String publish(Path staged) {
        String filename = UUID.randomUUID() + ".jpg";
        try { Files.move(staged, root.resolve(filename), StandardCopyOption.ATOMIC_MOVE); return filename; }
        catch (IOException e) { discard(staged); throw new ApiException(HttpStatus.SERVICE_UNAVAILABLE, "PHOTO_STORAGE_FAILED", "照片保存失败，请稍后重试"); }
    }
    public void discard(Path staged) {
        if (staged == null) return;
        try { Files.deleteIfExists(staged); }
        catch (IOException e) { log.warn("Staged photo cleanup failed"); }
    }
    public void retire(String filename) {
        try {
            Path photo = directChild(filename);
            if (photo == null) return;
            Files.createDirectories(root.resolve(".cleanup"));
            Path marker = root.resolve(".cleanup").resolve(filename);
            Files.writeString(marker, "pending", StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING);
            if (readyForCleanup(marker)) cleanup(marker);
        } catch (Exception e) { log.warn("Photo cleanup deferred: {}", e.getClass().getSimpleName()); }
    }
    @Scheduled(fixedDelayString = "${face.photos.cleanup-interval-ms:60000}")
    public void retryCleanup() {
        Path queue = root.resolve(".cleanup");
        if (!Files.isDirectory(queue)) return;
        try (var entries = Files.list(queue)) {
            entries.filter(this::readyForCleanup).limit(20).forEach(marker -> {
                try { cleanup(marker); } catch (Exception e) { log.warn("Photo cleanup retry deferred"); }
            });
        } catch (IOException e) { log.warn("Photo cleanup queue unavailable"); }
    }
    private boolean readyForCleanup(Path marker) {
        try { return Files.isRegularFile(marker) && System.currentTimeMillis()-Files.getLastModifiedTime(marker).toMillis()>=graceMillis; }
        catch(IOException e) { return false; }
    }
    private void cleanup(Path marker) throws IOException {
        String filename = marker.getFileName().toString();
        Path photo = directChild(filename);
        if (photo == null) { Files.deleteIfExists(marker); return; }
        // Also protects compensation after an ambiguous database commit failure.
        if (users.selectCount(new QueryWrapper<User>().eq("photo_url", filename)) == 0) {
            if (users.countRetainedPhotoReferences(filename)>0) {
                Files.setLastModifiedTime(marker,java.nio.file.attribute.FileTime.fromMillis(System.currentTimeMillis()+600000));
                return;
            }
            Files.deleteIfExists(photo);
        }
        Files.deleteIfExists(marker);
    }
    /** The stored file of a published photo, or null when the name is invalid or the file is gone. */
    public Path published(String filename) {
        Path photo = directChild(filename);
        return photo != null && Files.isRegularFile(photo) ? photo : null;
    }
    private Path directChild(String filename) {
        if (filename == null || filename.isBlank() || filename.startsWith(".")) return null;
        Path target = root.resolve(filename).normalize();
        return target.getParent() != null && target.getParent().equals(root) && !Files.isSymbolicLink(target) && !Files.isDirectory(target) ? target : null;
    }
}
