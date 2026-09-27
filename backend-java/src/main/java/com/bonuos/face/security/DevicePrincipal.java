package com.bonuos.face.security;

import java.security.Principal;
import java.util.Set;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;

/** Identity and authorization scope, never a credential or client-supplied scope. */
public record DevicePrincipal(String deviceSn, Set<Long> groupIds, boolean allGroups,
                              long scopeVersion, boolean legacy) implements Principal {
    public DevicePrincipal { groupIds = Set.copyOf(groupIds); }
    @Override public String getName() { return deviceSn; }
    public boolean allowsGroup(Long groupId) { return allGroups || (groupId != null && groupIds.contains(groupId)); }
    public static DevicePrincipal current() {
        Authentication authentication = SecurityContextHolder.getContext().getAuthentication();
        if (authentication != null && authentication.getPrincipal() instanceof DevicePrincipal device) return device;
        throw new org.springframework.security.access.AccessDeniedException("Device identity required");
    }
}
