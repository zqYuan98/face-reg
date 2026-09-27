package com.bonuos.face.service;

import com.baomidou.mybatisplus.core.metadata.IPage;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.bonuos.face.dto.GroupOption;
import com.bonuos.face.dto.GroupOptionsResult;
import com.bonuos.face.dto.PageResult;
import com.bonuos.face.dto.UserListItem;
import com.bonuos.face.entity.User;
import com.bonuos.face.entity.UserGroup;
import com.bonuos.face.exception.ApiException;
import com.bonuos.face.mapper.AdminQueryMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.mockito.ArgumentCaptor;
import org.springframework.http.HttpStatus;

import java.util.List;
import java.util.stream.LongStream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class AdminQueryServiceTest {
    private AdminQueryMapper mapper;
    private AdminQueryService service;

    @BeforeEach
    void setUp() {
        mapper = mock(AdminQueryMapper.class);
        service = new AdminQueryService(mapper, new AdminUserPageReader(mapper));
    }

    @ParameterizedTest
    @CsvSource({"0,20", "-1,20", "1,0", "1,-1", "1,101", "50002,20", "9223372036854775807,100"})
    void rejectsInvalidOrOverflowingPagesBeforeQuerying(long page, long size) {
        assertBadRequest(() -> service.pageUsers(page, size, null, null));
        assertBadRequest(() -> service.pageGroups(page, size, null));
        verifyNoInteractions(mapper);
    }

    @Test
    void acceptsMaximumOffsetAndSkipsDetailsWhenNoIdsRemain() {
        when(mapper.selectUserIdsPage(any(), isNull(), isNull())).thenAnswer(invocation -> invocation.getArgument(0));

        PageResult<UserListItem> result = service.pageUsers(10_001, 100, null, null);

        ArgumentCaptor<IPage<Long>> pageCaptor = ArgumentCaptor.forClass(IPage.class);
        verify(mapper).selectUserIdsPage(pageCaptor.capture(), isNull(), isNull());
        verify(mapper, never()).selectUserDetailsByIds(any());
        assertThat(pageCaptor.getValue().offset()).isEqualTo(1_000_000);
        assertThat(pageCaptor.getValue().optimizeJoinOfCountSql()).isFalse();
        assertThat(result.page()).isEqualTo(10_001);
        assertThat(result.size()).isEqualTo(100);
    }

    @Test
    void trimsAndEscapesLiteralKeywordsForEveryQueryPath() {
        when(mapper.selectUserIdsPage(any(), anyString(), eq(3L))).thenReturn(new Page<Long>(1, 20));
        when(mapper.selectGroupPage(any(), anyString())).thenReturn(new Page<UserGroup>(1, 20));
        when(mapper.selectGroupOptions(anyString(), eq(0L), eq(21))).thenReturn(List.of());
        when(mapper.selectLegacyUserIds(anyString(), eq(3L))).thenReturn(List.of());

        service.pageUsers(1, 20, "  A!%_\\B  ", 3L);
        service.pageGroups(1, 20, "  A!%_\\B  ");
        service.groupOptions("  A!%_\\B  ", 0, 20);
        service.legacyUsers("  A!%_\\B  ", 3L);

        verify(mapper).selectUserIdsPage(any(), eq("%A!!!%!_\\B%"), eq(3L));
        verify(mapper).selectGroupPage(any(), eq("%A!!!%!_\\B%"));
        verify(mapper).selectGroupOptions("%A!!!%!_\\B%", 0, 21);
        verify(mapper).selectLegacyUserIds("%A!!!%!_\\B%", 3L);
    }

    @Test
    void blankKeywordIsUnfilteredAndPageMetadataComesFromQuery() {
        UserListItem item = new UserListItem(9L, "U9", "王", null, null, "9.jpg", true, 1, null, null);
        Page<Long> response = new Page<>(2, 20, 23);
        response.setRecords(List.of(9L));
        when(mapper.selectUserIdsPage(any(), isNull(), isNull())).thenReturn(response);
        when(mapper.selectUserDetailsByIds(List.of(9L))).thenReturn(List.of(item));

        PageResult<UserListItem> result = service.pageUsers(2, 20, " \t ", null);

        assertThat(result.records()).containsExactly(item);
        assertThat(result.total()).isEqualTo(23);
        assertThat(result.page()).isEqualTo(2);
        assertThat(result.size()).isEqualTo(20);
        verify(mapper).selectUserDetailsByIds(List.of(9L));
        verify(mapper, never()).selectLegacyUsersByIds(any());
    }

    @Test
    void rejectsLongKeywordsAndNonpositiveGroupIdsBeforeQuerying() {
        String longKeyword = "x".repeat(101);
        assertBadRequest(() -> service.pageUsers(1, 20, longKeyword, null));
        assertBadRequest(() -> service.pageGroups(1, 20, longKeyword));
        assertBadRequest(() -> service.groupOptions(longKeyword, 0, 20));
        assertBadRequest(() -> service.legacyUsers(longKeyword, null));
        assertBadRequest(() -> service.pageUsers(1, 20, null, 0L));
        assertBadRequest(() -> service.legacyUsers(null, -1L));
        verifyNoInteractions(mapper);
    }

    @Test
    void keywordLengthIsCheckedAfterTrimming() {
        String keyword = "x".repeat(100);
        when(mapper.selectGroupPage(any(), anyString())).thenReturn(new Page<UserGroup>(1, 20));

        service.pageGroups(1, 20, "  " + keyword + "  ");

        verify(mapper).selectGroupPage(any(), eq("%" + keyword + "%"));
    }

    @ParameterizedTest
    @CsvSource({"-1,20", "0,0", "0,-1", "0,51"})
    void rejectsInvalidCursorRequestsBeforeQuerying(long afterId, int limit) {
        assertBadRequest(() -> service.groupOptions(null, afterId, limit));
        verifyNoInteractions(mapper);
    }

    @Test
    void optionsProbeOneExtraAndCursorUsesLastReturnedId() {
        GroupOption a = new GroupOption(8L, "A", "G8");
        GroupOption b = new GroupOption(13L, "B", "G13");
        GroupOption extra = new GroupOption(21L, "C", "G21");
        when(mapper.selectGroupOptions(null, 5, 3)).thenReturn(List.of(a, b, extra));

        GroupOptionsResult result = service.groupOptions(null, 5, 2);

        assertThat(result.items()).containsExactly(a, b);
        assertThat(result.hasMore()).isTrue();
        assertThat(result.nextCursor()).isEqualTo(13L);
        verify(mapper).selectGroupOptions(null, 5, 3);
    }

    @Test
    void finalAndEmptyOptionsHaveNoNextCursor() {
        when(mapper.selectGroupOptions(null, 0, 51))
                .thenReturn(List.of(new GroupOption(2L, "A", "G2")));
        when(mapper.selectGroupOptions(null, 2, 51)).thenReturn(List.of());

        GroupOptionsResult last = service.groupOptions(null, 0, 50);
        GroupOptionsResult empty = service.groupOptions(null, 2, 50);

        assertThat(last.hasMore()).isFalse();
        assertThat(last.nextCursor()).isNull();
        assertThat(empty.items()).isEmpty();
        assertThat(empty.hasMore()).isFalse();
        assertThat(empty.nextCursor()).isNull();
    }

    @Test
    void legacyOverflowNeverFetchesFullEntities() {
        List<Long> ids = LongStream.rangeClosed(1, 101).boxed().toList();
        when(mapper.selectLegacyUserIds(null, null)).thenReturn(ids);
        when(mapper.selectLegacyGroupIds()).thenReturn(ids);

        assertPaginationRequired(() -> service.legacyUsers(null, null));
        assertPaginationRequired(service::legacyGroups);

        verify(mapper, never()).selectLegacyUsersByIds(any());
        verify(mapper, never()).selectLegacyGroupsByIds(any());
    }

    @Test
    void legacyAtLimitFetchesOnlyProbedIdsAndKeepsFullEntityContract() {
        List<Long> ids = LongStream.rangeClosed(1, 100).boxed().toList();
        User user = new User();
        user.setFeatureData("full legacy face feature");
        UserGroup group = new UserGroup();
        when(mapper.selectLegacyUserIds(null, null)).thenReturn(ids);
        when(mapper.selectLegacyUsersByIds(ids)).thenReturn(List.of(user));
        when(mapper.selectLegacyGroupIds()).thenReturn(ids);
        when(mapper.selectLegacyGroupsByIds(ids)).thenReturn(List.of(group));

        assertThat(service.legacyUsers(null, null)).containsExactly(user);
        assertThat(service.legacyGroups()).containsExactly(group);
        verify(mapper).selectLegacyUsersByIds(ids);
        verify(mapper).selectLegacyGroupsByIds(ids);
    }

    @Test
    void emptyLegacyResultsDoNotBuildAnEmptyInClause() {
        when(mapper.selectLegacyUserIds(null, null)).thenReturn(List.of());
        when(mapper.selectLegacyGroupIds()).thenReturn(List.of());

        assertThat(service.legacyUsers(null, null)).isEmpty();
        assertThat(service.legacyGroups()).isEmpty();
        verify(mapper, never()).selectLegacyUsersByIds(any());
        verify(mapper, never()).selectLegacyGroupsByIds(any());
    }

    @Test
    void countDoesNotLoadGroupEntities() {
        when(mapper.countGroups()).thenReturn(120_000L);
        assertThat(service.countGroups()).isEqualTo(120_000L);
        verify(mapper).countGroups();
        verifyNoMoreInteractions(mapper);
    }

    private static void assertBadRequest(Runnable operation) {
        assertThatThrownBy(operation::run).isInstanceOfSatisfying(ApiException.class, exception -> {
            assertThat(exception.getStatus()).isEqualTo(HttpStatus.BAD_REQUEST);
            assertThat(exception.getErrorCode()).isEqualTo("INVALID_REQUEST");
        });
    }

    private static void assertPaginationRequired(Runnable operation) {
        assertThatThrownBy(operation::run).isInstanceOfSatisfying(ApiException.class, exception -> {
            assertThat(exception.getStatus()).isEqualTo(HttpStatus.CONFLICT);
            assertThat(exception.getErrorCode()).isEqualTo("PAGINATION_REQUIRED");
            assertThat(exception.getMessage()).isEqualTo("请使用分页接口");
        });
    }
}
