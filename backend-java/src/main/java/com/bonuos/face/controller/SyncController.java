package com.bonuos.face.controller;

import com.bonuos.face.entity.User;
import com.bonuos.face.service.SyncService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.*;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/sync")
public class SyncController {

    @Autowired
    private SyncService syncService;

    @GetMapping("/users")
    public Map<String, Object> syncUsers(@RequestParam(required = false) Long lastSyncTime) {
        List<User> users = syncService.getSyncData(lastSyncTime);

        Map<String, Object> result = new HashMap<>();
        result.put("code", 200);
        result.put("msg", "success");
        result.put("data", users);
        result.put("timestamp", System.currentTimeMillis());

        return result;
    }
}
