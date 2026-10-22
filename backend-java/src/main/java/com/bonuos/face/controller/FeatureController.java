package com.bonuos.face.controller;

import com.bonuos.face.service.FaceSearchService;
import com.bonuos.face.service.FeatureBackfillService;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

/** Admin: feature versions, and backfilling a version from stored photos. */
@RestController
@RequestMapping("/api/features")
public class FeatureController {
    private final FeatureBackfillService backfill;
    private final FaceSearchService search;

    public FeatureController(FeatureBackfillService backfill, FaceSearchService search) {
        this.backfill = backfill; this.search = search;
    }

    @GetMapping("/status")
    public Map<String, Object> status(@RequestParam("version") String version) {
        return Map.of("code", 200, "msg", "success", "data", Map.of(
                "searchVersion", search.featureVersion(), "backfill", backfill.status(version)));
    }

    @PostMapping("/backfill")
    public ResponseEntity<Map<String, Object>> start(@RequestParam("version") String version) {
        if (!backfill.start(version))
            return ResponseEntity.status(409).body(Map.of("code", 409, "errorCode", "BACKFILL_RUNNING", "msg", "补提任务正在运行"));
        return ResponseEntity.accepted().body(Map.of("code", 202, "msg", "补提任务已启动", "data", backfill.status(version)));
    }

    @PostMapping("/backfill/stop")
    public Map<String, Object> stop() {
        backfill.stop();
        return Map.of("code", 200, "msg", "已请求停止");
    }
}
