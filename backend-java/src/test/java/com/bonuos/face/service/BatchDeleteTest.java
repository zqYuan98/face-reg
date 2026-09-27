package com.bonuos.face.service;

import com.bonuos.face.dto.BatchDeleteResult;
import com.bonuos.face.entity.User;
import com.bonuos.face.exception.ApiException;
import com.bonuos.face.handler.SyncWebSocketHandler;
import com.bonuos.face.mapper.UserMapper;
import com.bonuos.face.service.impl.UserServiceImpl;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.dao.DataAccessResourceFailureException;
import org.springframework.test.util.ReflectionTestUtils;

import java.util.List;
import java.util.stream.LongStream;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.Mockito.*;

class BatchDeleteTest {
    private final UserMutationService mapper = mock(UserMutationService.class);
    private final PhotoStorage photos = mock(PhotoStorage.class);
    private final SyncWebSocketHandler notifications = mock(SyncWebSocketHandler.class);
    private final UserServiceImpl service = new UserServiceImpl();

    @BeforeEach void init() {
        ReflectionTestUtils.setField(service, "mutations", mapper);
        ReflectionTestUtils.setField(service, "syncWebSocketHandler", notifications);
        ReflectionTestUtils.setField(service, "photos", photos);
        when(mapper.delete(anyLong())).thenReturn(new UserMutationService.Deleted(false,null));
    }

    private void existing(long id) {
        when(mapper.delete(id)).thenReturn(new UserMutationService.Deleted(true,null));
    }

    @Test void boundsAreCheckedBeforeAnyWrite() {
        assertThrows(ApiException.class, () -> service.deleteUsers(LongStream.rangeClosed(1, 101).boxed().toList()));
        assertThrows(ApiException.class, () -> service.deleteUsers(List.of(0L)));
        verifyNoInteractions(mapper, notifications);
    }

    @Test void deduplicatesAndNotifiesOnlyOnceForCommittedChanges() {
        existing(1); existing(2);
        BatchDeleteResult result = service.deleteUsers(List.of(1L, 2L, 1L, 3L));
        assertEquals(4, result.requestedCount());
        assertEquals(3, result.uniqueCount());
        assertEquals(3, result.getSuccessCount());
        assertEquals(2, result.deletedCount());
        assertEquals(List.of(3L), result.alreadyAbsentIds());
        assertEquals(0, result.getFailureCount());
        verify(mapper, times(3)).delete(anyLong());
        verify(notifications).broadcastUpdate();
    }

    @Test void preservesPartialResultWhenAnItemFails() {
        existing(1); existing(2);
        when(mapper.delete(2L)).thenThrow(new IllegalStateException("injected failure"));
        BatchDeleteResult result = service.deleteUsers(List.of(1L, 2L));
        assertEquals(List.of(1L), result.successfulIds());
        assertEquals(2L, result.failures().get(0).id());
        assertEquals(1, result.getFailureCount());
        verify(notifications).broadcastUpdate();
    }

    @Test void connectionFailureStopsFurtherAttempts() {
        when(mapper.delete(1L)).thenThrow(new DataAccessResourceFailureException("unavailable"));
        BatchDeleteResult result = service.deleteUsers(List.of(1L, 2L, 3L));
        assertEquals(0, result.getSuccessCount());
        assertEquals(3, result.getFailureCount());
        assertEquals("NOT_ATTEMPTED", result.failures().get(1).errorCode());
        verify(mapper, never()).delete(2L);
        verifyNoInteractions(notifications);
    }

    @Test void notificationFailureDoesNotChangeDatabaseOutcome() {
        existing(1);
        doThrow(new IllegalStateException("slow connection")).when(notifications).broadcastUpdate();
        BatchDeleteResult result = service.deleteUsers(List.of(1L));
        assertEquals(1, result.deletedCount());
        assertEquals(0, result.getFailureCount());
    }

    @Test void allAlreadyAbsentIsSuccessfulWithoutBroadcast() {
        BatchDeleteResult result = service.deleteUsers(List.of(1L, 2L));
        assertEquals(2, result.getSuccessCount());
        assertEquals(0, result.deletedCount());
        verifyNoInteractions(notifications);
    }
}
