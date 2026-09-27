package com.bonuos.face.controller;

import com.bonuos.face.dto.BatchDeleteResult;
import com.bonuos.face.exception.ApiException;
import com.bonuos.face.handler.GlobalExceptionHandler;
import com.bonuos.face.service.AdminQueryService;
import com.bonuos.face.service.UserService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.util.List;

import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

class AdminApiContractTest {
    private final UserService users = mock(UserService.class);
    private final AdminQueryService queries = mock(AdminQueryService.class);
    private MockMvc mvc;

    @BeforeEach void init() {
        UserController controller = new UserController();
        ReflectionTestUtils.setField(controller, "userService", users);
        ReflectionTestUtils.setField(controller, "adminQueryService", queries);
        mvc = MockMvcBuilders.standaloneSetup(controller).setControllerAdvice(new GlobalExceptionHandler()).build();
    }

    @Test void legacyOverflowIsNotA200PartialList() throws Exception {
        when(queries.legacyUsers(null, null)).thenThrow(new ApiException(HttpStatus.CONFLICT, "PAGINATION_REQUIRED", "请使用分页接口"));
        mvc.perform(get("/api/users/list"))
                .andExpect(status().isConflict()).andExpect(jsonPath("$.errorCode").value("PAGINATION_REQUIRED"))
                .andExpect(jsonPath("$.data").doesNotExist());
    }

    @Test void partialBatchIsVisibleInHttpAndBody() throws Exception {
        when(users.deleteUsers(List.of(1L, 2L))).thenReturn(new BatchDeleteResult(2, 2,
                List.of(1L), List.of(), 1, List.of(new BatchDeleteResult.Failure(2L, "DELETE_FAILED", "失败"))));
        mvc.perform(post("/api/users/deleteBatch").contentType("application/json").content("[1,2]"))
                .andExpect(status().isMultiStatus()).andExpect(jsonPath("$.code").value(207))
                .andExpect(jsonPath("$.data.successCount").value(1))
                .andExpect(jsonPath("$.data.failureCount").value(1));
    }

    @Test void invalidJsonCannotBecomeHttp200() throws Exception {
        mvc.perform(post("/api/users/deleteBatch").contentType("application/json").content("not json"))
                .andExpect(status().isBadRequest());
        verifyNoInteractions(users);
    }
}
