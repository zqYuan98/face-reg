package com.bonuos.face.service;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/** Delete a bounded contiguous prefix only; expired devices must explicitly bootstrap again. */
@Service
public class SyncRetentionService {
    private final JdbcTemplate jdbc;
    private final int days;
    public SyncRetentionService(JdbcTemplate jdbc,@Value("${face.sync.retention-days:7}") int days) {
        if(days<1||days>365)throw new IllegalArgumentException("Invalid sync retention period");
        this.jdbc=jdbc;this.days=days;
    }
    @Scheduled(initialDelayString="${face.sync.retention-interval-ms:60000}",fixedDelayString="${face.sync.retention-interval-ms:60000}")
    @Transactional(timeout=5)
    public void prune() {
        var clock=jdbc.queryForMap("SELECT epoch,head,retention_floor FROM sync_revision WHERE id=1 FOR UPDATE");
        String epoch=(String)clock.get("epoch");
        long head=((Number)clock.get("head")).longValue(),floor=((Number)clock.get("retention_floor")).longValue();
        record Candidate(long revision,boolean expired){}
        var candidates=jdbc.query("SELECT revision,created_at<TIMESTAMPADD(DAY,?,NOW(3)) FROM user_change_log WHERE revision>? ORDER BY revision LIMIT 1000",
            (rs,n)->new Candidate(rs.getLong(1),rs.getBoolean(2)),-days,floor);
        long cutoff=floor;
        for(var candidate:candidates) {
            if(!candidate.expired() || candidate.revision()!=cutoff+1)break;
            cutoff=candidate.revision();
        }
        Long leaseFloor=jdbc.queryForObject("SELECT MIN(start_revision) FROM sync_lease WHERE epoch=? AND expires_at>NOW(3)",Long.class,epoch);
        Long activeAck=jdbc.queryForObject("SELECT MIN(ack_revision) FROM sync_device_cursor WHERE epoch=? AND updated_at>=TIMESTAMPADD(DAY,?,NOW(3))",Long.class,epoch,-days);
        if(leaseFloor!=null)cutoff=Math.min(cutoff,leaseFloor);
        if(activeAck!=null)cutoff=Math.min(cutoff,activeAck);
        cutoff=Math.min(cutoff,floor+1000);
        if(cutoff>floor) {
            jdbc.update("DELETE FROM user_change_log WHERE revision<=?",cutoff);
            jdbc.update("UPDATE sync_revision SET retention_floor=? WHERE id=1",cutoff);
        }
        jdbc.update("DELETE FROM sync_lease WHERE expires_at<=NOW(3) LIMIT 100");
        jdbc.update("DELETE FROM sync_device_cursor WHERE updated_at<TIMESTAMPADD(DAY,?,NOW(3)) LIMIT 100",-days);
    }
}
