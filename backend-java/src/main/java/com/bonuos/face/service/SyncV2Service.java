package com.bonuos.face.service;

import com.bonuos.face.dto.SyncV2Dtos.*;
import com.bonuos.face.entity.User;
import com.bonuos.face.exception.ApiException;
import com.bonuos.face.security.DevicePrincipal;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.util.*;

/** Bounded reads; every response is either a complete page of its interval or an explicit failure. */
@Service
public class SyncV2Service {
    private final JdbcTemplate jdbc;
    private final ObjectMapper json;
    private final int leaseSeconds;
    public SyncV2Service(JdbcTemplate jdbc, ObjectMapper json,
            @Value("${face.sync.lease-seconds:3600}") int leaseSeconds) {
        if (leaseSeconds < 1 || leaseSeconds > 86400) throw new IllegalArgumentException("Invalid sync lease duration");
        this.jdbc=jdbc; this.json=json; this.leaseSeconds=leaseSeconds;
    }
    private record Clock(String epoch, long head, long floor) {}
    private record Lease(String id, String device, long scope, String epoch, long start, long maxId,
                         Long upper, long scanCursor, boolean scanComplete, boolean live, String expiresAt) {}
    record ChangeRow(long revision, long userId, Long oldGroup, Long newGroup, String operation, String postimage) {}

    @Transactional(readOnly=true, isolation=Isolation.REPEATABLE_READ, timeout=10)
    public State state() {
        DevicePrincipal device=device(); Clock clock=clock(false);
        return new State(clock.epoch,clock.head,clock.floor,device.scopeVersion());
    }

    @Transactional(isolation=Isolation.REPEATABLE_READ, timeout=10)
    public Bootstrap bootstrap() {
        DevicePrincipal device=device(); Clock clock=clock(true);
        // Clock-first locking also serializes lease acquisition with any compliant retention worker.
        long maxId=Objects.requireNonNull(jdbc.queryForObject("SELECT COALESCE(MAX(id),0) FROM sys_user",Long.class));
        jdbc.update("DELETE FROM sync_lease WHERE device_sn=?",device.deviceSn());
        jdbc.update("DELETE FROM sync_lease WHERE expires_at<=CURRENT_TIMESTAMP(3) LIMIT 100");
        String id=UUID.randomUUID().toString();
        jdbc.update("INSERT INTO sync_lease(id,device_sn,scope_version,epoch,start_revision,max_id,expires_at,scan_cursor,scan_complete) "
                +"VALUES(?,?,?,?,?,?,TIMESTAMPADD(SECOND,?,CURRENT_TIMESTAMP(3)),0,0)",
                id,device.deviceSn(),device.scopeVersion(),clock.epoch,clock.head,maxId,leaseSeconds);
        Lease lease=lease(id,false);
        return new Bootstrap(id,clock.epoch,device.scopeVersion(),clock.head,maxId,lease.expiresAt);
    }

    @Transactional(isolation=Isolation.REPEATABLE_READ, timeout=10)
    public SnapshotPage page(String leaseId, long afterId, int limit) {
        validateLimit(limit);
        if (afterId<0) throw invalid("afterId 必须为非负整数");
        DevicePrincipal device=device();
        // Only this device's lease is locked while reading a bounded page. No network I/O holds it.
        Lease lease=lease(leaseId,true); Clock clock=clock(false); validateLease(lease,device,clock);
        if (lease.upper!=null) throw conflict("SNAPSHOT_FINISHED","初始化扫描已结束，请继续读取变更");
        if (afterId>lease.scanCursor || afterId>lease.maxId) throw invalid("初始化扫描不能跳过尚未读取的页面");
        List<Object> args=new ArrayList<>(List.of(afterId,lease.maxId));
        String scope=scopePredicate(device,args);
        args.add(limit+1);
        List<User> fetched=jdbc.query("SELECT id,user_no,name,group_id,feature_data,photo_url,status,create_time,update_time,sync_version "
                +"FROM sys_user WHERE id>? AND id<=? AND status=1"+scope+" ORDER BY id ASC LIMIT ?",
                this::user,args.toArray());
        boolean more=fetched.size()>limit;
        List<User> records=more?new ArrayList<>(fetched.subList(0,limit)):fetched;
        long next=more?records.get(records.size()-1).getId():lease.maxId;
        jdbc.update("UPDATE sync_lease SET scan_cursor=GREATEST(scan_cursor,?),scan_complete=GREATEST(scan_complete,?) WHERE id=?",
                next,more?0:1,lease.id);
        return new SnapshotPage(records,next,more);
    }

    @Transactional(isolation=Isolation.REPEATABLE_READ, timeout=10)
    public UpperBound finish(String leaseId) {
        DevicePrincipal device=device(); Clock clock=clock(true); Lease lease=lease(leaseId,true);
        validateLease(lease,device,clock);
        if (!lease.scanComplete) throw conflict("SNAPSHOT_INCOMPLETE","初始化页面尚未读取完成");
        long upper=lease.upper==null?clock.head:lease.upper;
        if (lease.upper==null) jdbc.update("UPDATE sync_lease SET upper_revision=? WHERE id=?",upper,lease.id);
        return new UpperBound(clock.epoch,device.scopeVersion(),upper);
    }

    @Transactional(readOnly=true, isolation=Isolation.REPEATABLE_READ, timeout=10)
    public Changes changes(String epoch,long scopeVersion,long after,long until,int limit,String leaseId) {
        validateLimit(limit);
        if (after<0 || until<after) throw invalid("变更区间无效");
        DevicePrincipal device=device(); Clock clock=clock(false); validateCursor(epoch,scopeVersion,after,device,clock);
        if (until>clock.head) throw invalid("变更上界超过已提交版本");
        if (leaseId!=null) {
            Lease lease=lease(leaseId,false); validateLease(lease,device,clock);
            if (lease.upper==null) throw conflict("SNAPSHOT_INCOMPLETE","请先完成初始化扫描");
            if (after<lease.start || until!=lease.upper) throw invalid("变更区间不属于当前初始化租约");
        }
        List<ChangeRow> scanned=jdbc.query("SELECT revision,user_id,old_group_id,new_group_id,operation,postimage "
                        +"FROM user_change_log WHERE revision>? AND revision<=? ORDER BY revision ASC LIMIT ?",
                (rs,n)->new ChangeRow(rs.getLong(1),rs.getLong(2),rs.getObject(3,Long.class),rs.getObject(4,Long.class),rs.getString(5),rs.getString(6)),
                after,until,limit+1);
        boolean more=scanned.size()>limit;
        List<ChangeRow> rows=more?scanned.subList(0,limit):scanned;
        List<Event> events=new ArrayList<>();
        long previous=after;
        for (ChangeRow row:rows) {
            // Every committed revision has one log row. Never silently cross a missing interval.
            if (row.revision!=previous+1) throw corruptEvent();
            previous=row.revision;
            Event visible=project(device,row);
            if (visible!=null) events.add(visible);
        }
        if (!more && previous!=until) throw corruptEvent();
        // A scoped page can contain zero visible events and still needs to advance its global cursor.
        long next=more?rows.get(rows.size()-1).revision:until;
        return new Changes(events,next,more,until);
    }

    @Transactional(isolation=Isolation.REPEATABLE_READ, timeout=10)
    public Ack ack(AckRequest request) {
        if (request==null || request.scopeVersion()==null || request.revision()==null || request.revision()<0)
            throw invalid("确认游标参数不完整");
        DevicePrincipal device=device(); Clock clock=clock(true);
        validateCursor(request.epoch(),request.scopeVersion(),request.revision(),device,clock);
        if (request.revision()>clock.head) throw invalid("确认游标超过已提交版本");
        if (request.leaseId()!=null) {
            List<Lease> leases=findLease(request.leaseId(),true);
            if (leases.isEmpty()) {
                // Response-loss retry after releasing the lease is safe only if this cursor was already acknowledged.
                Long confirmed=jdbc.query("SELECT ack_revision FROM sync_device_cursor WHERE device_sn=? AND epoch=? AND scope_version=?",
                        (rs,n)->rs.getLong(1),device.deviceSn(),clock.epoch,device.scopeVersion()).stream().findFirst().orElse(null);
                if (confirmed==null || confirmed<request.revision()) throw snapshotRequired();
                return new Ack(confirmed);
            }
            Lease lease=leases.get(0); validateLease(lease,device,clock);
            if (!lease.scanComplete || lease.upper==null || request.revision()!=lease.upper.longValue())
                throw conflict("SNAPSHOT_INCOMPLETE","初始化确认必须覆盖完整变更上界");
            jdbc.update("DELETE FROM sync_lease WHERE id=?",lease.id);
        }
        jdbc.update("INSERT INTO sync_device_cursor(device_sn,scope_version,epoch,ack_revision,updated_at) VALUES(?,?,?,?,CURRENT_TIMESTAMP(3)) "
                +"ON DUPLICATE KEY UPDATE ack_revision=IF(epoch=VALUES(epoch) AND scope_version=VALUES(scope_version),"
                +"GREATEST(ack_revision,VALUES(ack_revision)),VALUES(ack_revision)),scope_version=VALUES(scope_version),epoch=VALUES(epoch),updated_at=VALUES(updated_at)",
                device.deviceSn(),device.scopeVersion(),clock.epoch,request.revision());
        long ack=Objects.requireNonNull(jdbc.queryForObject("SELECT ack_revision FROM sync_device_cursor WHERE device_sn=?",Long.class,device.deviceSn()));
        return new Ack(ack);
    }

    Event project(DevicePrincipal device,ChangeRow row) {
        if ("delete".equals(row.operation))
            return device.allowsGroup(row.oldGroup)?new Event(row.revision,row.userId,"delete",null):null;
        if (!"upsert".equals(row.operation)) throw corruptEvent();
        if (device.allowsGroup(row.newGroup)) {
            User user;
            try { user=json.readValue(row.postimage,User.class); }
            catch (JsonProcessingException | IllegalArgumentException e) { throw corruptEvent(); }
            if (user==null || !Objects.equals(user.getId(),row.userId) || !Objects.equals(user.getSyncVersion(),row.revision)
                    || !Objects.equals(user.getGroupId(),row.newGroup)) throw corruptEvent();
            if (Objects.equals(user.getStatus(),1)) return new Event(row.revision,row.userId,"upsert",user);
        }
        return device.allowsGroup(row.oldGroup)?new Event(row.revision,row.userId,"delete",null):null;
    }
    private Clock clock(boolean lock) {
        return jdbc.queryForObject("SELECT epoch,head,retention_floor FROM sync_revision WHERE id=1"+(lock?" FOR UPDATE":""),
                (rs,n)->new Clock(rs.getString(1),rs.getLong(2),rs.getLong(3)));
    }
    private List<Lease> findLease(String id,boolean lock) {
        if (id==null || id.length()!=36) throw snapshotRequired();
        return jdbc.query("SELECT id,device_sn,scope_version,epoch,start_revision,max_id,upper_revision,scan_cursor,scan_complete,"
                        +"expires_at>CURRENT_TIMESTAMP(3) AS live,UNIX_TIMESTAMP(expires_at)*1000 AS expiry_ms FROM sync_lease WHERE id=?"+(lock?" FOR UPDATE":""),
                (rs,n)->new Lease(rs.getString(1),rs.getString(2),rs.getLong(3),rs.getString(4),rs.getLong(5),rs.getLong(6),
                        rs.getObject(7,Long.class),rs.getLong(8),rs.getBoolean(9),rs.getBoolean(10),Instant.ofEpochMilli(rs.getLong(11)).toString()),id);
    }
    private Lease lease(String id,boolean lock) { return findLease(id,lock).stream().findFirst().orElseThrow(SyncV2Service::snapshotRequired); }
    private void validateLease(Lease lease,DevicePrincipal device,Clock clock) {
        if (!lease.live || !lease.device.equals(device.deviceSn()) || lease.scope!=device.scopeVersion()
                || !lease.epoch.equals(clock.epoch) || lease.start<clock.floor) throw snapshotRequired();
    }
    private void validateCursor(String epoch,long scope,long after,DevicePrincipal device,Clock clock) {
        if (!clock.epoch.equals(epoch) || scope!=device.scopeVersion() || after<clock.floor) throw snapshotRequired();
    }
    private String scopePredicate(DevicePrincipal device,List<Object> args) {
        if (device.allGroups()) return "";
        if (device.groupIds().isEmpty()) return " AND 1=0";
        List<Long> groups=device.groupIds().stream().sorted().toList(); args.addAll(groups);
        return " AND group_id IN ("+String.join(",",Collections.nCopies(groups.size(),"?"))+")";
    }
    private User user(ResultSet rs,int row) throws SQLException {
        User user=new User(); user.setId(rs.getLong("id")); user.setUserNo(rs.getString("user_no")); user.setName(rs.getString("name"));
        user.setGroupId(rs.getObject("group_id",Long.class)); user.setFeatureData(rs.getString("feature_data")); user.setPhotoUrl(rs.getString("photo_url"));
        user.setStatus(rs.getObject("status",Integer.class)); user.setSyncVersion(rs.getLong("sync_version"));
        var created=rs.getTimestamp("create_time"); var updated=rs.getTimestamp("update_time");
        user.setCreateTime(created==null?null:created.toLocalDateTime()); user.setUpdateTime(updated==null?null:updated.toLocalDateTime());
        return user;
    }
    private DevicePrincipal device() {
        DevicePrincipal device=DevicePrincipal.current();
        if (device.legacy()) throw new ApiException(HttpStatus.FORBIDDEN,"DEVICE_CREDENTIAL_REQUIRED","新版同步需要独立设备凭据");
        return device;
    }
    private static void validateLimit(int limit) { if (limit<1 || limit>200) throw invalid("limit 必须在 1 至 200 之间"); }
    private static ApiException invalid(String message) { return new ApiException(HttpStatus.BAD_REQUEST,"INVALID_SYNC_REQUEST",message); }
    private static ApiException conflict(String code,String message) { return new ApiException(HttpStatus.CONFLICT,code,message); }
    private static ApiException snapshotRequired() { return conflict("SNAPSHOT_REQUIRED","同步水位或授权已失效，请重新初始化"); }
    private static ApiException corruptEvent() { return new ApiException(HttpStatus.SERVICE_UNAVAILABLE,"SYNC_DATA_INVALID","同步变更暂不可用，请联系管理员检查数据"); }
}
