package com.bonuos.face.controller;

import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.bonuos.face.dto.GroupOption;
import com.bonuos.face.dto.UserListItem;
import com.bonuos.face.entity.UserGroup;
import com.bonuos.face.handler.GlobalExceptionHandler;
import com.bonuos.face.mapper.AdminQueryMapper;
import com.bonuos.face.service.AdminQueryService;
import com.bonuos.face.service.AdminUserPageReader;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.util.List;

import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

class AdminQueryControllerTest {
    private AdminQueryMapper mapper;
    private MockMvc mvc;

    @BeforeEach
    void setUp() {
        mapper = mock(AdminQueryMapper.class);
        mvc = MockMvcBuilders.standaloneSetup(new AdminQueryController(new AdminQueryService(mapper, new AdminUserPageReader(mapper))))
                .setControllerAdvice(new GlobalExceptionHandler()).build();
    }

    @Test
    void userPageDefaultsAndProjectionAreVisibleInJson() throws Exception {
        UserListItem user = new UserListItem(8L, "U8", "测试人员", null, null, "8.jpg", true, 1, null, null);
        Page<Long> result = new Page<>(1, 20, 51);
        result.setRecords(List.of(8L));
        when(mapper.selectUserIdsPage(any(), isNull(), isNull())).thenReturn(result);
        when(mapper.selectUserDetailsByIds(List.of(8L))).thenReturn(List.of(user));

        mvc.perform(get("/api/users/page"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(200))
                .andExpect(jsonPath("$.msg").value("success"))
                .andExpect(jsonPath("$.data.page").value(1))
                .andExpect(jsonPath("$.data.size").value(20))
                .andExpect(jsonPath("$.data.total").value(51))
                .andExpect(jsonPath("$.data.records[0].id").value(8))
                .andExpect(jsonPath("$.data.records[0].hasFeature").value(true))
                .andExpect(jsonPath("$.data.records[0].featureData").doesNotExist())
                .andExpect(jsonPath("$.data.records[0].feature_data").doesNotExist());
        verify(mapper).selectUserIdsPage(argThat(page -> page.getCurrent() == 1 && page.getSize() == 20), isNull(), isNull());
        verify(mapper).selectUserDetailsByIds(List.of(8L));
    }

    @Test
    void groupPageAcceptsExplicitPageSizeAndKeyword() throws Exception {
        UserGroup group = new UserGroup();
        group.setId(7L);
        group.setGroupName("市场部");
        group.setGroupNo("G7");
        Page<UserGroup> result = new Page<>(2, 5, 7);
        result.setRecords(List.of(group));
        when(mapper.selectGroupPage(any(), eq("%市场%"))).thenReturn(result);

        mvc.perform(get("/api/groups/page").param("page", "2").param("size", "5").param("keyword", " 市场 "))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.page").value(2))
                .andExpect(jsonPath("$.data.size").value(5))
                .andExpect(jsonPath("$.data.total").value(7))
                .andExpect(jsonPath("$.data.records[0].groupName").value("市场部"));
    }

    @Test
    void optionDefaultsAndCountHaveIndependentSmallResponseContracts() throws Exception {
        when(mapper.selectGroupOptions(null, 0, 21)).thenReturn(List.of(new GroupOption(3L, "技术部", "G3")));
        when(mapper.countGroups()).thenReturn(300L);

        mvc.perform(get("/api/groups/options"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.items[0].id").value(3))
                .andExpect(jsonPath("$.data.items[0].groupName").value("技术部"))
                .andExpect(jsonPath("$.data.items[0].groupNo").value("G3"))
                .andExpect(jsonPath("$.data.hasMore").value(false))
                .andExpect(jsonPath("$.data.nextCursor").isEmpty());
        mvc.perform(get("/api/groups/count"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data").value(300));
        verify(mapper).selectGroupOptions(null, 0, 21);
        verify(mapper).countGroups();
        verifyNoMoreInteractions(mapper);
    }

    @ParameterizedTest
    @ValueSource(strings = {
            "/api/users/page?page=0", "/api/users/page?size=101", "/api/users/page?groupId=-1",
            "/api/users/page?page=9223372036854775807&size=100", "/api/users/page?size=abc",
            "/api/users/page?page=9223372036854775808", "/api/groups/page?size=0",
            "/api/groups/options?afterId=-1", "/api/groups/options?limit=51",
            "/api/groups/options?limit=abc"
    })
    void invalidRequestsAre400WithoutDatabaseWork(String uri) throws Exception {
        mvc.perform(get(uri))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value(400))
                .andExpect(jsonPath("$.errorCode").isString())
                .andExpect(jsonPath("$.data").doesNotExist());
        verifyNoInteractions(mapper);
    }
}
