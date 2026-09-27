package com.bonuos.face.service;

import com.baomidou.mybatisplus.core.conditions.query.QueryWrapper;
import com.bonuos.face.entity.User;
import com.bonuos.face.exception.ApiException;
import com.bonuos.face.mapper.UserMapper;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import java.time.LocalDateTime;
import java.util.Objects;

/** The single write boundary: the revision lock is held through business/log COMMIT. */
@Service
public class UserMutationService {
    private final UserMapper users;
    private final JdbcTemplate jdbc;
    private final ObjectMapper json;
    public UserMutationService(UserMapper users, JdbcTemplate jdbc, ObjectMapper json) {
        this.users = users; this.jdbc = jdbc; this.json = json;
    }
    public long lockClock() {
        return Objects.requireNonNull(jdbc.queryForObject("SELECT head FROM sync_revision WHERE id=1 FOR UPDATE", Long.class));
    }
    @Transactional(timeout = 5)
    public User insert(User user) {
        long revision = lockClock() + 1;
        validate(user.getName(), user.getGroupId(), null);
        user.setSyncVersion(revision);
        users.insert(user);
        publish(user, null, null, revision, "upsert");
        return user;
    }
    public record Updated(User user, String previousPhoto) {}
    @Transactional(timeout = 5)
    public Updated update(long id, String name, Long groupId, String photo, String feature) {
        long revision = lockClock() + 1;
        User current = users.selectById(id);
        if (current == null) throw new ApiException(HttpStatus.NOT_FOUND, "USER_NOT_FOUND", "人员不存在");
        validate(name, groupId, id);
        Long oldGroup = current.getGroupId(); String oldPhoto = current.getPhotoUrl();
        current.setName(name); current.setGroupId(groupId); current.setUpdateTime(LocalDateTime.now());
        current.setSyncVersion(revision);
        if (photo != null) { current.setPhotoUrl(photo); current.setFeatureData(feature); }
        // Explicit SQL updates a null group too; MyBatis' default NOT_NULL policy cannot ungroup.
        jdbc.update("UPDATE sys_user SET name=?,group_id=?,photo_url=?,feature_data=?,update_time=?,sync_version=? WHERE id=?",
                current.getName(), current.getGroupId(), current.getPhotoUrl(), current.getFeatureData(), current.getUpdateTime(), revision, id);
        publish(current, oldGroup, oldPhoto, revision, "upsert");
        return new Updated(current, oldPhoto);
    }
    public record Deleted(boolean deleted, String photo) {}
    @Transactional(timeout = 5)
    public Deleted delete(long id) {
        long revision = lockClock() + 1;
        User current = jdbc.query("SELECT id,photo_url,group_id FROM sys_user WHERE id=?", (rs,n) -> {
            User row = new User(); row.setId(rs.getLong(1)); row.setPhotoUrl(rs.getString(2));
            row.setGroupId(rs.getObject(3,Long.class)); return row;
        }, id).stream().findFirst().orElse(null);
        if (current == null) return new Deleted(false, null);
        if (users.deleteById(id) != 1) throw new IllegalStateException("Concurrent writer bypassed revision lock");
        publish(current, current.getGroupId(), current.getPhotoUrl(), revision, "delete");
        return new Deleted(true, current.getPhotoUrl());
    }
    private void validate(String name, Long groupId, Long excludeId) {
        if (name == null || name.isBlank() || name.length() > 100)
            throw new ApiException(HttpStatus.BAD_REQUEST, "INVALID_NAME", "姓名必须为 1 至 100 个字符");
        if (groupId != null && (groupId <= 0 || jdbc.queryForObject("SELECT COUNT(*) FROM sys_user_group WHERE id=?", Long.class, groupId) == 0))
            throw new ApiException(HttpStatus.BAD_REQUEST, "INVALID_GROUP", "指定分组不存在");
        QueryWrapper<User> query = new QueryWrapper<User>().eq("name", name).eq("status",1);
        if (groupId == null) query.isNull("group_id"); else query.eq("group_id",groupId);
        if (excludeId != null) query.ne("id",excludeId);
        if (users.selectCount(query) > 0)
            throw new ApiException(HttpStatus.CONFLICT,"DUPLICATE_NAME","该分组已有同名有效人员");
    }
    private void publish(User user, Long oldGroup, String oldPhoto, long revision, String operation) {
        String postimage = null;
        try { if (operation.equals("upsert")) postimage = json.writeValueAsString(user); }
        catch (JsonProcessingException e) { throw new IllegalStateException("Cannot serialize sync event",e); }
        jdbc.update("INSERT INTO user_change_log(revision,user_id,old_group_id,new_group_id,operation,postimage,photo_url,old_photo_url) VALUES (?,?,?,?,?,?,?,?)",
                revision,user.getId(),oldGroup,operation.equals("delete")?null:user.getGroupId(),operation,postimage,user.getPhotoUrl(),oldPhoto);
        jdbc.update("UPDATE sync_revision SET head=? WHERE id=1",revision);
    }
}
