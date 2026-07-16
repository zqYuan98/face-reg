package com.bonuos.face.service;

import com.bonuos.face.entity.UserGroup;
import java.util.List;

public interface UserGroupService {
    UserGroup addGroup(UserGroup group);

    UserGroup updateGroup(UserGroup group);

    void deleteGroup(Long id);

    UserGroup getGroupById(Long id);

    List<UserGroup> listGroups();
}
