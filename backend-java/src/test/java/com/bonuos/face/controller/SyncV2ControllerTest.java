package com.bonuos.face.controller;

import com.bonuos.face.dto.SyncV2Dtos.*;
import com.bonuos.face.exception.ApiException;
import com.bonuos.face.handler.GlobalExceptionHandler;
import com.bonuos.face.service.SyncV2Service;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import java.util.List;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

class SyncV2ControllerTest {
    private final SyncV2Service sync=mock(SyncV2Service.class);
    private MockMvc mvc;
    @BeforeEach void setup() { mvc=MockMvcBuilders.standaloneSetup(new SyncV2Controller(sync)).setControllerAdvice(new GlobalExceptionHandler()).build(); }
    @Test void bootstrapNeedsNoRequestBody() throws Exception {
        when(sync.bootstrap()).thenReturn(new Bootstrap("lease","epoch",1,3,7,"2026-09-24T00:00:00Z"));
        mvc.perform(post("/api/sync/v2/bootstrap")).andExpect(status().isOk())
                .andExpect(jsonPath("$.data.startRevision").value(3)).andExpect(jsonPath("$.data.maxId").value(7));
    }
    @Test void filteredPageCarriesItsAdvancedGlobalCursor() throws Exception {
        when(sync.changes("e",1,2,9,200,null)).thenReturn(new Changes(List.of(),7,true,9));
        mvc.perform(get("/api/sync/v2/changes?epoch=e&scopeVersion=1&afterRevision=2&untilRevision=9"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.data.events").isEmpty())
                .andExpect(jsonPath("$.data.nextRevision").value(7)).andExpect(jsonPath("$.data.hasMore").value(true));
    }
    @Test void historyExpiryIsExplicitFailureWithoutData() throws Exception {
        when(sync.changes("e",1,2,9,200,null)).thenThrow(new ApiException(HttpStatus.CONFLICT,"SNAPSHOT_REQUIRED","重建"));
        mvc.perform(get("/api/sync/v2/changes?epoch=e&scopeVersion=1&afterRevision=2&untilRevision=9"))
                .andExpect(status().isConflict()).andExpect(jsonPath("$.errorCode").value("SNAPSHOT_REQUIRED"))
                .andExpect(jsonPath("$.data").doesNotExist());
    }
    @Test void malformedCursorsAreRejectedBeforeServiceCall() throws Exception {
        mvc.perform(get("/api/sync/v2/changes?epoch=e&scopeVersion=1&afterRevision=1.2&untilRevision=9"))
                .andExpect(status().isBadRequest());
        verifyNoInteractions(sync);
    }
}
