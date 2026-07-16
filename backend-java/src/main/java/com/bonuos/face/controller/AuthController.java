package com.bonuos.face.controller;

import cn.hutool.core.util.IdUtil;
import org.springframework.web.bind.annotation.*;
import java.util.HashMap;
import java.util.Map;

@RestController
@RequestMapping("/api")
public class AuthController {

    @PostMapping("/login")
    public Map<String, Object> login(@RequestBody Map<String, String> loginData) {
        String username = loginData.get("username");
        String password = loginData.get("password");

        Map<String, Object> result = new HashMap<>();

        // Simple hardcoded check
        if ("admin".equals(username) && "123456".equals(password)) {
            result.put("code", 200);
            result.put("msg", "登录成功");
            result.put("token", IdUtil.simpleUUID()); // Return a dummy token
        } else {
            result.put("code", 401);
            result.put("msg", "用户名或密码错误");
        }
        return result;
    }
}
