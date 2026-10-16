package com.bonuos.face.service.impl;

import cn.hutool.core.util.IdUtil;
import com.bonuos.face.dto.BatchDeleteResult;
import com.bonuos.face.entity.User;
import com.bonuos.face.exception.ApiException;
import com.bonuos.face.mapper.UserMapper;
import com.bonuos.face.service.*;
import com.bonuos.face.util.FaceFeatureExtractorClient;
import com.bonuos.face.handler.SyncWebSocketHandler;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.dao.DataAccessResourceFailureException;
import org.springframework.dao.TransientDataAccessResourceException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import java.io.IOException;
import java.nio.file.Path;
import java.time.LocalDateTime;
import java.util.*;

@Service
public class UserServiceImpl implements UserService {
    private static final Logger log = LoggerFactory.getLogger(UserServiceImpl.class);
    @Autowired private FaceFeatureExtractorClient faceFeatureExtractorClient;
    @Autowired private UserMapper userMapper;
    @Autowired private SyncWebSocketHandler syncWebSocketHandler;
    @Autowired private UserMutationService mutations;
    @Autowired private PhotoStorage photos;
    @Autowired private FaceSearchService search;
    @Autowired private AdminQueryService adminQueries;

    @Override
    public User addUser(String name, Long groupId, MultipartFile photo) {
        name = validateName(name);
        Path staged = photos.stage(photo); String published = null;
        try {
            String feature = featureJson(extract(staged));
            published = photos.publish(staged);
            User user = new User(); user.setUserNo(IdUtil.getSnowflakeNextIdStr());
            user.setName(name); user.setGroupId(groupId); user.setPhotoUrl(published);
            user.setFeatureData(feature); user.setStatus(1);
            user.setCreateTime(LocalDateTime.now()); user.setUpdateTime(LocalDateTime.now());
            User saved = mutations.insert(user);
            notifyUpdate(); return saved;
        } catch (RuntimeException e) {
            if (published != null) photos.retire(published);
            throw e;
        } finally { photos.discard(staged); }
    }

    @Override
    public User updateUser(Long id, String name, Long groupId, MultipartFile photo) {
        if (id == null || id <= 0) throw new ApiException(HttpStatus.BAD_REQUEST,"INVALID_ID","人员 ID 无效");
        name = validateName(name);
        Path staged = null; String published = null; String feature = null;
        try {
            if (photo != null && !photo.isEmpty()) {
                staged = photos.stage(photo); feature = featureJson(extract(staged));
                published = photos.publish(staged);
            }
            var result = mutations.update(id,name,groupId,published,feature);
            if (published != null) photos.retire(result.previousPhoto());
            notifyUpdate(); return result.user();
        } catch (RuntimeException e) {
            if (published != null) photos.retire(published);
            throw e;
        } finally { photos.discard(staged); }
    }
    private static String validateName(String name) {
        if (name == null || name.isBlank() || name.trim().length() > 100)
            throw new ApiException(HttpStatus.BAD_REQUEST,"INVALID_NAME","姓名必须为 1 至 100 个字符");
        return name.trim();
    }
    private List<Float> extract(Path staged) {
        try { return faceFeatureExtractorClient.extractFeature(staged.toFile()); }
        catch (IOException e) { throw new ApiException(HttpStatus.BAD_GATEWAY,"EXTRACTION_FAILED","照片特征提取失败，请检查照片或稍后重试"); }
    }
    private static String featureJson(List<Float> feature) {
        if (feature == null || feature.size() != 1024 || feature.stream().anyMatch(v -> v == null || !Float.isFinite(v)))
            throw new ApiException(HttpStatus.BAD_GATEWAY,"INVALID_FEATURE","算法返回的特征格式无效");
        double norm = 0; for (float v : feature) norm += (double)v * v;
        if (norm == 0) throw new ApiException(HttpStatus.BAD_GATEWAY,"INVALID_FEATURE","算法返回的特征无效");
        return feature.toString();
    }
    @Override public List<User> getUsers(String name, Long groupId) { return adminQueries.legacyUsers(name,groupId); }
    @Override public long countUsers() {
        return userMapper.selectCount(new com.baomidou.mybatisplus.core.conditions.query.QueryWrapper<User>().eq("status",1));
    }
    @Override public User getUserById(Long id) { return userMapper.selectById(id); }

    @Override
    public BatchDeleteResult deleteUsers(List<Long> ids) {
        if (ids == null || ids.isEmpty() || ids.size() > 100 ||
                ids.stream().anyMatch(id -> id == null || id <= 0)) {
            throw new ApiException(HttpStatus.BAD_REQUEST, "INVALID_BATCH", "请选择 1 至 100 个有效人员 ID");
        }
        List<Long> uniqueIds = new ArrayList<>(new LinkedHashSet<>(ids));
        List<Long> successful = new ArrayList<>();
        List<Long> absent = new ArrayList<>();
        List<BatchDeleteResult.Failure> failures = new ArrayList<>();
        boolean unavailable = false;
        int deleted = 0;
        for (Long id : uniqueIds) {
            if (unavailable) {
                failures.add(new BatchDeleteResult.Failure(id, "NOT_ATTEMPTED", "数据库暂不可用，本项尚未执行"));
                continue;
            }
            try {
                if (deleteWithoutNotification(id)) {
                    deleted++;
                } else {
                    absent.add(id);
                }
                successful.add(id);
            } catch (DataAccessResourceFailureException | TransientDataAccessResourceException e) {
                unavailable = true;
                failures.add(new BatchDeleteResult.Failure(id, "DATABASE_UNAVAILABLE", "删除结果未确认，请刷新后重试"));
                log.warn("Batch deletion stopped: database unavailable");
            } catch (Exception e) {
                failures.add(new BatchDeleteResult.Failure(id, "DELETE_FAILED", "删除失败，请刷新后重试"));
                log.warn("User deletion failed for id={}, type={}", id, e.getClass().getSimpleName());
            }
        }
        if (deleted > 0) notifyUpdate();
        return new BatchDeleteResult(ids.size(), uniqueIds.size(), successful, absent, deleted, failures);
    }

    @Override
    public void deleteUser(Long id) {
        if (id == null || id <= 0) throw new ApiException(HttpStatus.BAD_REQUEST, "INVALID_ID", "人员 ID 无效");
        if (deleteWithoutNotification(id)) notifyUpdate();
    }

    private boolean deleteWithoutNotification(Long id) {
        var result = mutations.delete(id);
        if (result.deleted()) photos.retire(result.photo());
        return result.deleted();
    }

    private void notifyUpdate() {
        try {
            syncWebSocketHandler.broadcastUpdate();
        } catch (RuntimeException e) {
            log.warn("Update notification failed after committed mutation: {}", e.getClass().getSimpleName());
        }
    }

    @Override
    public User searchUser(MultipartFile photo, Long groupId) {
        if (groupId == null || groupId <= 0) throw new ApiException(HttpStatus.BAD_REQUEST,"INVALID_GROUP","必须指定有效分组");
        Path staged = photos.stage(photo);
        try {
            List<Float> feature = extract(staged);
            featureJson(feature);
            User match = search.search(feature,groupId);
            // A biometric template is not part of a search answer.
            if (match != null) match.setFeatureData(null);
            return match;
        } finally { photos.discard(staged); }
    }
}
