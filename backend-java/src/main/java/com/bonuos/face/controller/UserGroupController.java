package com.bonuos.face.controller;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.bonuos.face.entity.UserGroup;
import com.bonuos.face.service.UserGroupService;
import com.bonuos.face.service.AdminQueryService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.*;

import java.time.LocalDateTime;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/groups")
public class UserGroupController {

    @Autowired
    private UserGroupService userGroupService;

    @Autowired
    private AdminQueryService adminQueryService;

    @GetMapping("/list")
    public Map<String, Object> list() {
        List<UserGroup> groups = adminQueryService.legacyGroups();
        Map<String, Object> result = new HashMap<>();
        result.put("code", 200);
        result.put("msg", "success");
        result.put("data", groups);
        return result;
    }

    @GetMapping("/{id}")
    public Map<String, Object> get(@PathVariable Long id) {
        UserGroup group = userGroupService.getGroupById(id);
        Map<String, Object> result = new HashMap<>();
        if (group != null) {
            result.put("code", 200);
            result.put("msg", "success");
            result.put("data", group);
        } else {
            throw new com.bonuos.face.exception.ApiException(org.springframework.http.HttpStatus.NOT_FOUND,"NOT_FOUND","分组不存在");
        }
        return result;
    }

    @PostMapping("/add")
    public Map<String, Object> add(@RequestBody UserGroup group) {
        userGroupService.addGroup(group);
        Map<String, Object> result = new HashMap<>();
        result.put("code", 200);
        result.put("msg", "success");
        result.put("data", group);
        return result;
    }

    @PostMapping("/update")
    public Map<String, Object> update(@RequestBody UserGroup group) {
        userGroupService.updateGroup(group);
        Map<String, Object> result = new HashMap<>();
        result.put("code", 200);
        result.put("msg", "success");
        result.put("data", group);
        return result;
    }

    @DeleteMapping("/{id}")
    public Map<String, Object> delete(@PathVariable Long id) {
        userGroupService.deleteGroup(id);
        Map<String, Object> result = new HashMap<>();
        result.put("code", 200);
        result.put("msg", "success");
        return result;
    }
}
