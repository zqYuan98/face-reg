package com.bonuos.face.controller;

import com.bonuos.face.entity.User;
import com.bonuos.face.service.UserService;
import com.bonuos.face.service.AdminQueryService;
import com.bonuos.face.dto.BatchDeleteResult;
import org.springframework.http.ResponseEntity;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;

import java.util.HashMap;
import java.util.Map;
import java.util.List;

@RestController
@RequestMapping("/api/users")
public class UserController {

    @Autowired
    private UserService userService;

    @Autowired
    private AdminQueryService adminQueryService;

    @PostMapping("/add")
    public Map<String, Object> addUser(
            @RequestParam("name") String name,
            @RequestParam(value = "groupId", required = false) Long groupId,
            @RequestParam("photo") MultipartFile photo) {

        User user = userService.addUser(name, groupId, photo);

        Map<String, Object> result = new HashMap<>();
        result.put("code", 200);
        result.put("msg", "success");
        result.put("data", user);
        return result;
    }

    @GetMapping("/list")
    public Map<String, Object> listUsers(
            @RequestParam(value = "name", required = false) String name,
            @RequestParam(value = "groupId", required = false) Long groupId) {
        List<User> users = adminQueryService.legacyUsers(name, groupId);

        Map<String, Object> result = new HashMap<>();
        result.put("code", 200);
        result.put("msg", "success");
        result.put("data", users);
        return result;
    }

    @GetMapping("/{id}")
    public Map<String, Object> getUser(@PathVariable Long id) {
        User user = userService.getUserById(id);

        Map<String, Object> result = new HashMap<>();
        if (user != null) {
            result.put("code", 200);
            result.put("msg", "success");
            result.put("data", user);
        } else {
            throw new com.bonuos.face.exception.ApiException(org.springframework.http.HttpStatus.NOT_FOUND,"NOT_FOUND","用户不存在");
        }
        return result;
    }

    @PostMapping("/update/{id}")
    public Map<String, Object> updateUser(
            @PathVariable Long id,
            @RequestParam("name") String name,
            @RequestParam(value = "groupId", required = false) Long groupId,
            @RequestParam(value = "photo", required = false) MultipartFile photo) {

        User user = userService.updateUser(id, name, groupId, photo);

        Map<String, Object> result = new HashMap<>();
        result.put("code", 200);
        result.put("msg", "success");
        result.put("data", user);
        return result;
    }

    @DeleteMapping("/{id}")
    public Map<String, Object> deleteUser(@PathVariable Long id) {
        userService.deleteUser(id);

        Map<String, Object> result = new HashMap<>();
        result.put("code", 200);
        result.put("msg", "删除成功");
        return result;
    }

    @PostMapping("/deleteBatch")
    public ResponseEntity<Map<String, Object>> deleteBatch(@RequestBody List<Long> ids) {
        BatchDeleteResult data = userService.deleteUsers(ids);
        int status = data.getFailureCount() == 0 ? 200 : data.getSuccessCount() > 0 ? 207 : 422;
        String msg = status == 200 ? "批量处理完成" : status == 207 ? "部分人员处理失败" : "批量处理失败";
        return ResponseEntity.status(status).body(Map.of("code", status, "msg", msg, "data", data));
    }

    @GetMapping("/count")
    public Map<String, Object> countUsers() {
        long count = userService.countUsers();
        Map<String, Object> result = new HashMap<>();
        result.put("code", 200);
        result.put("msg", "success");
        result.put("data", count);
        return result;
    }

    @PostMapping("/search")
    public Map<String, Object> searchUser(
            @RequestParam("photo") MultipartFile photo,
            @RequestParam("groupId") Long groupId) {

        User user = userService.searchUser(photo, groupId);

        Map<String, Object> result = new HashMap<>();
        if (user != null) {
            result.put("code", 200);
            result.put("msg", "success");
            result.put("data", user);
        } else {
            throw new com.bonuos.face.exception.ApiException(org.springframework.http.HttpStatus.NOT_FOUND,"NO_MATCH","未找到匹配用户");
        }
        return result;
    }
}
