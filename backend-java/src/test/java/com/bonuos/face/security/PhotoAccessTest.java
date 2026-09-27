package com.bonuos.face.security;

import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.util.List;
import java.util.Set;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.DisabledOnOs;
import org.junit.jupiter.api.condition.OS;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.PreparedStatementCreator;
import org.springframework.jdbc.core.ResultSetExtractor;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.authority.AuthorityUtils;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class PhotoAccessTest {
    @TempDir Path directory;
    private JdbcTemplate jdbc;
    private PhotoAccess photos;
    private final Authentication admin = UsernamePasswordAuthenticationToken.authenticated("admin", null,
            AuthorityUtils.createAuthorityList("ROLE_ADMIN"));

    @BeforeEach void setUp() {
        jdbc = mock(JdbcTemplate.class);
        photos = new PhotoAccess(jdbc, directory.toString(), 7);
    }

    @ParameterizedTest
    @ValueSource(strings={"/uploads/uuid_中文 空格.jpg", "/uploads/uuid_%E4%B8%AD%E6%96%87%20%E7%A9%BA%E6%A0%BC.jpg",
            "/uploads/uuid_a+b.jpg", "/uploads/uuid_a%2Bb.jpg"})
    void oldUnicodeSpacesAndPlusFilenamesAreAccepted(String uri) {
        assertTrue(photos.allowed(admin, request(uri)));
        verifyNoInteractions(jdbc);
    }

    @ParameterizedTest
    @ValueSource(strings={"/uploads", "/uploads/", "/uploads/.staging/a.jpg", "/uploads/%2estaging.jpg",
            "/uploads/a%2Fb.jpg", "/uploads/a%5Cb.jpg", "/uploads/%252e%252e%252foutside.jpg",
            "/uploads/a%3Ab.jpg", "/uploads/a%00b.jpg", "/uploads/a%0Ab.jpg", "/uploads/a%25b.jpg",
            "/uploads/%xx.jpg", "/uploads/a/b.jpg", "/uploads/../outside.jpg", "/uploads/a.svg", "/uploads/a.jpg "})
    void ambiguousPathsHiddenFilesAndNonImagesAreDenied(String uri) {
        assertFalse(photos.allowed(admin, request(uri)));
        verifyNoInteractions(jdbc);
    }

    @Test @DisabledOnOs(OS.WINDOWS)
    void symlinkCannotExposeAnOutsidePhoto() throws Exception {
        Path target=Files.createTempFile("photo-outside-", ".jpg");
        try {
            Files.createSymbolicLink(directory.resolve("link.jpg"), target);
            assertFalse(photos.allowed(admin, request("/uploads/link.jpg")));
        } finally { Files.deleteIfExists(target); }
    }

    @Test void decodedLiteralFilenameIsBoundWithoutChangingPlusAndQueryHasABudget() throws Exception {
        Connection connection=mock(Connection.class);
        PreparedStatement statement=mock(PreparedStatement.class);
        ResultSet result=mock(ResultSet.class);
        when(connection.prepareStatement(anyString())).thenReturn(statement);
        when(result.next()).thenReturn(true);
        when(jdbc.query(any(PreparedStatementCreator.class), org.mockito.ArgumentMatchers.<ResultSetExtractor<Boolean>>any()))
                .thenAnswer(invocation -> {
                    PreparedStatementCreator creator=invocation.getArgument(0);
                    assertSame(statement, creator.createPreparedStatement(connection));
                    ResultSetExtractor<Boolean> extractor=invocation.getArgument(1);
                    return extractor.extractData(result);
                });
        Authentication device=UsernamePasswordAuthenticationToken.authenticated(
                new DevicePrincipal("scoped", Set.of(7L), false, 2, false), null, List.of());
        assertTrue(photos.allowed(device, request("/uploads/uuid_%E4%B8%AD%E6%96%87%20a+b.jpg")));
        verify(statement).setObject(1, "uuid_中文 a+b.jpg");
        verify(statement).setObject(2, "uuid_中文 a+b.jpg");
        verify(statement).setObject(3, 7L);
        verify(statement).setQueryTimeout(3);
        verify(statement).setMaxRows(1);
        verify(connection).prepareStatement(contains("LIMIT 1"));
    }

    private static MockHttpServletRequest request(String uri) {
        var request=new MockHttpServletRequest();
        request.setRequestURI(uri);
        return request;
    }
}
