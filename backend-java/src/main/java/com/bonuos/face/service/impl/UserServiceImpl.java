package com.bonuos.face.service.impl;

import cn.hutool.core.util.IdUtil;
import com.bonuos.face.entity.User;
import com.bonuos.face.mapper.UserMapper;
import com.bonuos.face.service.UserService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;

import java.io.File;
import java.io.IOException;
import java.time.LocalDateTime;
import java.util.List;

@Service
public class UserServiceImpl implements UserService {

    @Autowired
    private com.bonuos.face.util.FaceFeatureExtractorClient faceFeatureExtractorClient;

    @Autowired
    private UserMapper userMapper;

    @Autowired
    private com.bonuos.face.handler.SyncWebSocketHandler syncWebSocketHandler;

    @Autowired
    private com.bonuos.face.mapper.UserGroupMapper userGroupMapper;

    @Value("${spring.servlet.multipart.location:e:/Face-app-web/backend-java/uploads}")
    private String uploadPath;

    @Override
    public User addUser(String name, Long groupId, MultipartFile photo) {
        // 1. 保存照片
        String fileName = IdUtil.fastSimpleUUID() + "_" + photo.getOriginalFilename();

        if (groupId != null) {
            if (userGroupMapper.selectById(groupId) == null) {
                throw new RuntimeException("指定的用户分组不存在");
            }
            // 检查重名
            checkDuplicateName(name, groupId, null);
        }

        // 确保上传目录存在
        File uploadDir = new File(uploadPath);
        if (!uploadDir.exists()) {
            uploadDir.mkdirs();
        }

        File dest = new File(uploadDir, fileName);
        try {
            photo.transferTo(dest);
        } catch (IOException e) {
            throw new RuntimeException("照片保存失败: " + e.getMessage(), e);
        }

        // 2. 提取特征 (调用 Python API)
        String featureJson;
        try {
            // 使用已保存的文件进行提取，避免 MultipartFile 在 transferTo 后失效的问题
            List<Float> features = faceFeatureExtractorClient.extractFeature(dest);
            featureJson = convertFeaturesToJson(features);
        } catch (IOException e) {
            // 处理特征提取失败的异常
            if (e.getMessage().contains("No face detected") ||
                    e.getMessage().contains("未检测到人脸") ||
                    e.getMessage().contains("Face quality check failed") ||
                    e.getMessage().toLowerCase().contains("feature extraction failed")) {
                throw new RuntimeException("请上传正确且清晰的人脸照片", e);
            }
            throw new RuntimeException("特征提取失败: " + e.getMessage(), e);
        }

        // 3. 保存用户到数据库
        User user = new User();
        user.setUserNo(IdUtil.getSnowflakeNextIdStr());
        user.setName(name);
        user.setGroupId(groupId);
        user.setPhotoUrl(fileName); // 存储相对路径或文件名
        user.setFeatureData(featureJson);
        user.setStatus(1);
        user.setCreateTime(LocalDateTime.now());
        user.setUpdateTime(LocalDateTime.now());

        userMapper.insert(user);
        syncWebSocketHandler.broadcastUpdate(); // 通知前端更新
        return user;
    }

    private String convertFeaturesToJson(List<Float> features) {
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < features.size(); i++) {
            sb.append(features.get(i));
            if (i < features.size() - 1)
                sb.append(",");
        }
        sb.append("]");
        return sb.toString();
    }

    @Override
    public List<User> getUsers(String name, Long groupId) {
        com.baomidou.mybatisplus.core.conditions.query.QueryWrapper<User> queryWrapper = new com.baomidou.mybatisplus.core.conditions.query.QueryWrapper<>();
        // 只返回活跃用户（status=1）
        queryWrapper.eq("status", 1);

        if (name != null && !name.isEmpty()) {
            queryWrapper.like("name", name);
        }

        if (groupId != null) {
            queryWrapper.eq("group_id", groupId);
        }

        queryWrapper.orderByDesc("create_time");

        return userMapper.selectList(queryWrapper);
    }

    @Override
    public long countUsers() {
        return userMapper
                .selectCount(new com.baomidou.mybatisplus.core.conditions.query.QueryWrapper<User>().eq("status", 1));
    }

    @Override
    public User getUserById(Long id) {
        return userMapper.selectById(id);
    }

    @Override
    public User updateUser(Long id, String name, Long groupId, MultipartFile photo) {
        User user = userMapper.selectById(id);
        if (user == null) {
            throw new RuntimeException("用户不存在");
        }

        if (groupId != null) {
            if (userGroupMapper.selectById(groupId) == null) {
                throw new RuntimeException("指定的用户分组不存在");
            }
        }

        // 如果修改了名字或分组，检查重名
        if (!user.getName().equals(name) || (groupId != null && !groupId.equals(user.getGroupId()))) {
            checkDuplicateName(name, groupId != null ? groupId : user.getGroupId(), id);
        }

        user.setName(name);
        user.setGroupId(groupId);
        user.setUpdateTime(LocalDateTime.now());

        // 如果提供了新照片，则更新照片和特征
        if (photo != null && !photo.isEmpty()) {
            String fileName = IdUtil.fastSimpleUUID() + "_" + photo.getOriginalFilename();

            // 确保上传目录存在
            File uploadDir = new File(uploadPath);
            if (!uploadDir.exists()) {
                uploadDir.mkdirs();
            }

            File dest = new File(uploadDir, fileName);
            try {
                photo.transferTo(dest);
                user.setPhotoUrl(fileName);

                // 重新提取特征
                List<Float> features = faceFeatureExtractorClient.extractFeature(dest);
                user.setFeatureData(convertFeaturesToJson(features));
            } catch (IOException e) {
                // 处理特征提取失败的异常
                if (e.getMessage().contains("No face detected") ||
                        e.getMessage().contains("未检测到人脸") ||
                        e.getMessage().contains("Face quality check failed") ||
                        e.getMessage().toLowerCase().contains("feature extraction failed")) {
                    throw new RuntimeException("请上传正确且清晰的人脸照片", e);
                }
                throw new RuntimeException("照片保存或特征提取失败: " + e.getMessage(), e);
            }
        }

        userMapper.updateById(user);
        syncWebSocketHandler.broadcastUpdate(); // 通知前端更新
        return user;
    }

    @Override
    public void deleteUsers(List<Long> ids) {
        if (ids == null || ids.isEmpty()) {
            return;
        }
        for (Long id : ids) {
            try {
                deleteUser(id);
            } catch (Exception e) {
                // log error but continue
                System.err.println("Batch delete error for id " + id + ": " + e.getMessage());
            }
        }
    }

    @Override
    public void deleteUser(Long id) {
        User user = userMapper.selectById(id);
        if (user == null) {
            throw new RuntimeException("用户不存在");
        }

        // 硬删除：直接从数据库删除记录
        userMapper.deleteById(id);
        syncWebSocketHandler.broadcastUpdate(); // 通知前端更新

        // 可选：同时删除用户的照片文件
        if (user.getPhotoUrl() != null && !user.getPhotoUrl().isEmpty()) {
            try {
                File photoFile = new File(uploadPath, user.getPhotoUrl());
                if (photoFile.exists()) {
                    photoFile.delete();
                }
            } catch (Exception e) {
                // 文件删除失败不影响数据库删除
                System.err.println("删除照片文件失败: " + e.getMessage());
            }
        }
    }

    @Override
    public User searchUser(MultipartFile photo, Long groupId) {
        if (groupId == null) {
            throw new RuntimeException("必须指定分组ID");
        }

        // 1. 保存上传的照片 (临时)
        String fileName = IdUtil.fastSimpleUUID() + "_" + photo.getOriginalFilename();
        File uploadDir = new File(uploadPath);
        if (!uploadDir.exists()) {
            uploadDir.mkdirs();
        }
        File dest = new File(uploadDir, fileName);

        try {
            photo.transferTo(dest);
        } catch (IOException e) {
            throw new RuntimeException("照片处理失败", e);
        }

        try {
            // 2. 提取特征
            List<Float> targetFeature = faceFeatureExtractorClient.extractFeature(dest);
            if (targetFeature == null || targetFeature.isEmpty()) {
                throw new RuntimeException("未能从上传图片中提取到人脸特征");
            }

            // 3. 获取分组下所有用户
            List<User> users = getUsers(null, groupId);
            if (users == null || users.isEmpty()) {
                return null;
            }

            // 4. 比对特征
            User bestMatch = null;
            float maxSimilarity = 0f;
            float threshold = 0.6f; // 相似度阈值

            for (User user : users) {
                if (user.getFeatureData() == null)
                    continue;

                List<Float> dbFeature = com.bonuos.face.util.FaceUtils.parseFeatureData(user.getFeatureData());
                if (dbFeature != null) {
                    float similarity = com.bonuos.face.util.FaceUtils.cosineSimilarity(targetFeature, dbFeature);
                    if (similarity > maxSimilarity) {
                        maxSimilarity = similarity;
                        bestMatch = user;
                    }
                }
            }

            // 5. 返回结果
            if (maxSimilarity >= threshold) {
                return bestMatch;
            }
            return null; // 未找到匹配

        } catch (Exception e) {
            throw new RuntimeException("人脸搜索失败: " + e.getMessage(), e);
        } finally {
            // 清理临时文件
            if (dest.exists()) {
                dest.delete();
            }
        }
    }

    /**
     * 检查分组下是否存在同名用户
     */
    private void checkDuplicateName(String name, Long groupId, Long excludeId) {
        com.baomidou.mybatisplus.core.conditions.query.QueryWrapper<User> wrapper = new com.baomidou.mybatisplus.core.conditions.query.QueryWrapper<>();
        wrapper.eq("group_id", groupId)
                .eq("name", name)
                .eq("status", 1); // 只检查有效用户

        if (excludeId != null) {
            wrapper.ne("id", excludeId);
        }

        if (userMapper.selectCount(wrapper) > 0) {
            throw new RuntimeException("该分组下已存在名为 [" + name + "] 的用户");
        }
    }
}
