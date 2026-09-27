package com.bonuos.face.controller;

import com.bonuos.face.dto.SyncV2Dtos.AckRequest;
import com.bonuos.face.service.SyncV2Service;
import org.springframework.web.bind.annotation.*;
import java.util.Map;

@RestController
@RequestMapping("/api/sync/v2")
public class SyncV2Controller {
    private final SyncV2Service sync;
    public SyncV2Controller(SyncV2Service sync) { this.sync = sync; }
    @GetMapping("/state") public Map<String,Object> state() { return ok(sync.state()); }
    @PostMapping("/bootstrap") public Map<String,Object> bootstrap() { return ok(sync.bootstrap()); }
    @GetMapping("/bootstrap/{leaseId}/page")
    public Map<String,Object> page(@PathVariable String leaseId,
            @RequestParam(defaultValue="0") long afterId, @RequestParam(defaultValue="200") int limit) {
        return ok(sync.page(leaseId, afterId, limit));
    }
    @PostMapping("/bootstrap/{leaseId}/finish")
    public Map<String,Object> finish(@PathVariable String leaseId) { return ok(sync.finish(leaseId)); }
    @GetMapping("/changes")
    public Map<String,Object> changes(@RequestParam String epoch, @RequestParam long scopeVersion,
            @RequestParam long afterRevision, @RequestParam long untilRevision,
            @RequestParam(defaultValue="200") int limit, @RequestParam(required=false) String leaseId) {
        return ok(sync.changes(epoch, scopeVersion, afterRevision, untilRevision, limit, leaseId));
    }
    @PostMapping("/ack") public Map<String,Object> ack(@RequestBody AckRequest request) { return ok(sync.ack(request)); }
    private Map<String,Object> ok(Object data) { return Map.of("code",200,"msg","success","data",data); }
}
