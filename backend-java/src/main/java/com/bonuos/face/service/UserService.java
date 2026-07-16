package com.bonuos.face.service;

import com.bonuos.face.entity.User;
import org.springframework.web.multipart.MultipartFile;

import java.util.List;

public interface UserService {
    /**
     * 添加用户
     * 
     * @param name    姓名
     * @param groupId 分组ID
     * @param photo   照片文件
     * @return 创建的用户对象
     */
    User addUser(String name, Long groupId, MultipartFile photo);

    /**
     * 查询用户列表（支持过滤）
     * 
     * @param name    姓名（模糊查询）
     * @param groupId 分组ID
     * @return 用户列表
     */
    List<User> getUsers(String name, Long groupId);

    long countUsers();

    /**
     * 根据ID查询用户
     * 
     * @param id 用户ID
     * @return 用户对象
     */
    User getUserById(Long id);

    /**
     * 更新用户信息
     * 
     * @param id      用户ID
     * @param name    新姓名
     * @param groupId 新分组ID
     * @param photo   新照片（可选）
     * @return 更新后的用户对象
     */
    User updateUser(Long id, String name, Long groupId, MultipartFile photo);

    /**
     * 批量删除用户
     *
     * @param ids 用户ID列表
     */
    void deleteUsers(List<Long> ids);

    /**
     * 删除用户（软删除/硬删除）
     * 
     * @param id 用户ID
     */
    /**
     * 人脸搜索
     * 
     * @param photo   人脸照片
     * @param groupId 分组ID
     * @return 匹配的用户（如果匹配度过低返回null）
     */
    User searchUser(MultipartFile photo, Long groupId);

    void deleteUser(Long id);
}
