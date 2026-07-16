package com.bonuos.face.controller;

import com.bonuos.face.entity.Device;
import com.bonuos.face.service.DeviceService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.*;

import java.util.HashMap;
import java.util.Map;

@RestController
@RequestMapping("/api/devices")
public class DeviceController {

    @Autowired
    private DeviceService deviceService;

    @PostMapping("/register")
    public Map<String, Object> register(
            @RequestParam("deviceSn") String deviceSn,
            @RequestParam(value = "ip", required = false) String ip) {

        Device device = deviceService.registerOrHeartbeat(deviceSn, ip);

        Map<String, Object> result = new HashMap<>();
        result.put("code", 200);
        result.put("msg", "success");
        result.put("data", device);
        return result;
    }
}
