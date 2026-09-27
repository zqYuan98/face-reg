package com.bonuos.face.mapper;

import com.baomidou.mybatisplus.annotation.DbType;
import com.baomidou.mybatisplus.core.MybatisConfiguration;
import com.baomidou.mybatisplus.extension.plugins.inner.PaginationInnerInterceptor;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.bonuos.face.config.MybatisConfig;
import org.apache.ibatis.builder.xml.XMLMapperBuilder;
import org.apache.ibatis.executor.Executor;
import org.apache.ibatis.mapping.BoundSql;
import org.apache.ibatis.mapping.MappedStatement;
import org.apache.ibatis.session.RowBounds;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.io.InputStream;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class AdminQueryMapperTest {
    private static final String NAMESPACE = "com.bonuos.face.mapper.AdminQueryMapper.";
    private MybatisConfiguration configuration;
    private PaginationInnerInterceptor pagination;

    @BeforeEach
    void setUp() throws Exception {
        configuration = new MybatisConfiguration();
        configuration.setMapUnderscoreToCamelCase(true);
        try (InputStream xml = getClass().getResourceAsStream("/mapper/AdminQueryMapper.xml")) {
            assertThat(xml).isNotNull();
            new XMLMapperBuilder(xml, configuration, "mapper/AdminQueryMapper.xml", configuration.getSqlFragments()).parse();
        }
        pagination = (PaginationInnerInterceptor) new MybatisConfig().mybatisPlusInterceptor().getInterceptors().get(0);
    }

    @Test
    void configuredInterceptorActuallyAddsMysqlLimitAndOffset() throws Exception {
        assertThat(pagination.getDbType()).isEqualTo(DbType.MYSQL);
        assertThat(pagination.getMaxLimit()).isEqualTo(100L);
        assertThat(pagination.isOverflow()).isFalse();
        Map<String, Object> parameters = userParameters(new Page<Long>(3, 20), "%王%", 7L);
        MappedStatement statement = statement("selectUserIdsPage");
        BoundSql sql = statement.getBoundSql(parameters);

        pagination.beforeQuery(null, statement, parameters, RowBounds.DEFAULT, null, sql);

        assertThat(normalize(sql.getSql())).endsWith("LIMIT ?,?");
        assertThat(values(sql, parameters)).containsExactly("%王%", 7L, 40L, 20L);
    }

    @Test
    void interceptorCapsOversizedPagesAsDefenseInDepth() throws Exception {
        Page<Long> page = new Page<>(1, 500);
        Map<String, Object> parameters = userParameters(page, null, null);
        MappedStatement statement = statement("selectUserIdsPage");
        BoundSql sql = statement.getBoundSql(parameters);

        pagination.beforeQuery(null, statement, parameters, RowBounds.DEFAULT, null, sql);

        assertThat(page.getSize()).isEqualTo(100);
        assertThat(values(sql, parameters)).containsExactly(100L);
    }

    @Test
    void userPageSkipsOnlyIdsUsingTheSameFiltersAndStableOrder() {
        BoundSql sql = statement("selectUserIdsPage").getBoundSql(userParameters(new Page<>(1, 20), "%!%!_%", 5L));
        assertThat(normalize(sql.getSql())).startsWith("SELECT u.id FROM sys_user u")
                .contains("WHERE u.status = 1 AND u.name LIKE ? ESCAPE '!' AND u.group_id = ?")
                .endsWith("ORDER BY u.create_time DESC, u.id DESC")
                .doesNotContain("JOIN", "feature_data", "photo_url", "SELECT *");
        assertThat(values(sql, userParameters(null, "%!%!_%", 5L))).containsExactly("%!%!_%", 5L);
    }

    @Test
    void userDetailsOnlyReadOnePagesIdsAndProjectNoFeaturePayload() {
        Map<String, Object> parameters = Map.of("ids", List.of(7L, 11L));
        BoundSql sql = statement("selectUserDetailsByIds").getBoundSql(parameters);
        String rendered = normalize(sql.getSql());

        assertThat(rendered).contains("LEFT JOIN sys_user_group g ON g.id = u.group_id")
                .contains("CASE WHEN CHAR_LENGTH(u.feature_data) > 20 THEN 1 ELSE 0 END AS has_feature")
                .contains("WHERE u.status = 1 AND u.id IN ( ? , ? )")
                .endsWith("ORDER BY u.create_time DESC, u.id DESC")
                .doesNotContain("SELECT *", "u.*", "featureData");
        assertThat(rendered.split("u\\.feature_data", -1)).hasSize(2);
        assertThat(configuration.getResultMap(NAMESPACE + "userListItem").getConstructorResultMappings())
                .extracting(mapping -> mapping.getColumn())
                .doesNotContain("feature_data")
                .contains("group_name", "has_feature");
        assertThat(values(sql, parameters)).containsExactly(7L, 11L);
    }

    @Test
    void actualCountSqlKeepsFiltersWithoutLoadingOrJoiningAnyDetails() throws Exception {
        Page<Long> page = new Page<>(1, 20);
        page.setOptimizeJoinOfCountSql(false);
        Map<String, Object> parameters = userParameters(page, "%王%", 7L);
        MappedStatement statement = statement("selectUserIdsPage");
        Executor executor = mock(Executor.class);
        when(executor.query(any(), same(parameters), any(), isNull(), any(), any())).thenReturn(List.of(73L));

        assertThat(pagination.willDoQuery(executor, statement, parameters, RowBounds.DEFAULT, null,
                statement.getBoundSql(parameters))).isTrue();

        ArgumentCaptor<BoundSql> count = ArgumentCaptor.forClass(BoundSql.class);
        verify(executor).query(any(), same(parameters), any(), isNull(), any(), count.capture());
        assertThat(normalize(count.getValue().getSql()))
                .startsWith("SELECT COUNT(*) AS total FROM sys_user u WHERE")
                .contains("u.status = 1", "u.name LIKE ? ESCAPE '!'", "u.group_id = ?")
                .doesNotContain("ORDER BY", "feature_data", "JOIN", "photo_url");
        assertThat(values(count.getValue(), parameters)).containsExactly("%王%", 7L);
        assertThat(page.getTotal()).isEqualTo(73);
    }

    @Test
    void groupPageFiltersOnlyNameAndUsesStableOrder() {
        BoundSql sql = statement("selectGroupPage").getBoundSql(Map.of("keywordPattern", "%市场%"));

        assertThat(normalize(sql.getSql())).contains("g.group_name LIKE ? ESCAPE '!'")
                .doesNotContain("g.group_no LIKE")
                .endsWith("ORDER BY g.id DESC");
        assertThat(values(sql, Map.of("keywordPattern", "%市场%"))).containsExactly("%市场%");
    }

    @Test
    void optionsAreProjectedCursorBoundedAndOrdered() {
        Map<String, Object> parameters = Map.of("keywordPattern", "%!_%", "afterId", 9L, "take", 21);
        BoundSql sql = statement("selectGroupOptions").getBoundSql(parameters);

        assertThat(normalize(sql.getSql())).isEqualTo("SELECT g.id, g.group_name, g.group_no FROM sys_user_group g "
                + "WHERE g.id > ? AND g.group_name LIKE ? ESCAPE '!' ORDER BY g.id ASC LIMIT ?");
        assertThat(values(sql, parameters)).containsExactly(9L, "%!_%", 21);
    }

    @Test
    void legacyProbesOnlyIdsAndAtMost101Rows() {
        BoundSql users = statement("selectLegacyUserIds").getBoundSql(userParameters(null, "%王%", 3L));
        BoundSql groups = statement("selectLegacyGroupIds").getBoundSql(Map.of());

        assertThat(normalize(users.getSql())).startsWith("SELECT u.id FROM sys_user u")
                .contains("WHERE u.status = 1 AND u.name LIKE ? ESCAPE '!' AND u.group_id = ?")
                .endsWith("ORDER BY u.create_time DESC, u.id DESC LIMIT 101")
                .doesNotContain("feature_data", "SELECT *");
        assertThat(normalize(groups.getSql())).isEqualTo("SELECT g.id FROM sys_user_group g "
                + "ORDER BY g.id DESC LIMIT 101");
    }

    @Test
    void fullLegacyEntitiesAreFetchedOnlyByProbedIds() {
        Map<String, Object> parameters = Map.of("ids", List.of(7L, 11L));
        BoundSql users = statement("selectLegacyUsersByIds").getBoundSql(parameters);
        BoundSql groups = statement("selectLegacyGroupsByIds").getBoundSql(parameters);

        assertThat(normalize(users.getSql())).contains("u.feature_data", "WHERE u.status = 1 AND u.id IN ( ? , ? )")
                .endsWith("ORDER BY u.create_time DESC, u.id DESC");
        assertThat(normalize(groups.getSql())).contains("WHERE g.id IN ( ? , ? )");
        assertThat(values(users, parameters)).containsExactly(7L, 11L);
        assertThat(values(groups, parameters)).containsExactly(7L, 11L);
    }

    private MappedStatement statement(String id) {
        return configuration.getMappedStatement(NAMESPACE + id);
    }

    private static Map<String, Object> userParameters(Page<?> page, String pattern, Long groupId) {
        Map<String, Object> parameters = new HashMap<>();
        parameters.put("page", page);
        parameters.put("namePattern", pattern);
        parameters.put("groupId", groupId);
        return parameters;
    }

    private static List<Object> values(BoundSql sql, Map<String, Object> parameters) {
        return sql.getParameterMappings().stream().map(mapping -> sql.hasAdditionalParameter(mapping.getProperty())
                ? sql.getAdditionalParameter(mapping.getProperty()) : parameters.get(mapping.getProperty())).toList();
    }

    private static String normalize(String sql) {
        return sql.replaceAll("\\s+", " ").trim();
    }
}
