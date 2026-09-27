package com.bonuos.face.controller;

import com.bonuos.face.service.AdminQueryService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

@RestController
public class AdminQueryController {
    private final AdminQueryService queries;

    public AdminQueryController(AdminQueryService queries) {
        this.queries = queries;
    }

    @GetMapping("/api/users/page")
    public Map<String, Object> users(
            @RequestParam(defaultValue = "1") long page,
            @RequestParam(defaultValue = "20") long size,
            @RequestParam(required = false) String name,
            @RequestParam(required = false) Long groupId) {
        return success(queries.pageUsers(page, size, name, groupId));
    }

    @GetMapping("/api/groups/page")
    public Map<String, Object> groups(
            @RequestParam(defaultValue = "1") long page,
            @RequestParam(defaultValue = "20") long size,
            @RequestParam(required = false) String keyword) {
        return success(queries.pageGroups(page, size, keyword));
    }

    @GetMapping("/api/groups/options")
    public Map<String, Object> options(
            @RequestParam(required = false) String keyword,
            @RequestParam(defaultValue = "0") long afterId,
            @RequestParam(defaultValue = "20") int limit) {
        return success(queries.groupOptions(keyword, afterId, limit));
    }

    @GetMapping("/api/groups/count")
    public Map<String, Object> count() {
        return success(queries.countGroups());
    }

    private static Map<String, Object> success(Object data) {
        return Map.of("code", 200, "msg", "success", "data", data);
    }
}
