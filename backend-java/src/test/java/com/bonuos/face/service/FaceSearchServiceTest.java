package com.bonuos.face.service;

import com.bonuos.face.entity.User;
import com.bonuos.face.mapper.*;
import com.bonuos.face.exception.ApiException;
import org.junit.jupiter.api.Test;
import java.util.List;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class FaceSearchServiceTest {
    private User row(long id,String feature) { User u=new User(); u.setId(id); u.setFeatureData(feature); u.setGroupId(1L); u.setStatus(1); return u; }
    @Test void bestMatchMayBeInLaterChunk() {
        FaceSearchMapper chunks=mock(FaceSearchMapper.class); UserMapper users=mock(UserMapper.class);
        when(chunks.nextChunk(1,0,1)).thenReturn(List.of(row(1,"[0,1]")));
        when(chunks.nextChunk(1,1,1)).thenReturn(List.of(row(2,"[1,0]")));
        when(chunks.nextChunk(1,2,1)).thenReturn(List.of());
        when(users.selectById(2L)).thenReturn(row(2,"[1,0]"));
        assertEquals(2L,new FaceSearchService(chunks,users,1,1,1000).search(List.of(1f,0f),1).getId());
        verify(chunks).nextChunk(1,2,1);
    }
    @Test void deadlineIsIncompleteEvenIfASeenRowWouldMatch() {
        FaceSearchMapper chunks=mock(FaceSearchMapper.class); UserMapper users=mock(UserMapper.class);
        when(chunks.nextChunk(1,0,1)).thenAnswer(call->{ Thread.sleep(30); return List.of(row(1,"[1,0]")); });
        var failure=assertThrows(ApiException.class,()->new FaceSearchService(chunks,users,1,1,10).search(List.of(1f,0f),1));
        assertEquals("SEARCH_INCOMPLETE",failure.getErrorCode()); verifyNoInteractions(users);
    }
    @Test void changesDuringScanCannotBeReportedAsNoMatch() {
        FaceSearchMapper chunks=mock(FaceSearchMapper.class); UserMapper users=mock(UserMapper.class);
        when(chunks.revision()).thenReturn("epoch:1","epoch:3");
        when(chunks.nextChunk(1,0,1)).thenReturn(List.of(row(1,"[0,1]")));
        when(chunks.nextChunk(1,1,1)).thenReturn(List.of(row(2,"[0,1]")));
        when(chunks.nextChunk(1,2,1)).thenReturn(List.of());
        assertEquals("SEARCH_CHANGED",assertThrows(ApiException.class,()->new FaceSearchService(chunks,users,1,1,1000).search(List.of(1f,0f),1)).getErrorCode());
    }
    @Test void thresholdIsConfigurableAndScaleInvariant() {
        FaceSearchMapper chunks=mock(FaceSearchMapper.class); UserMapper users=mock(UserMapper.class);
        // cos = 0.8 regardless of vector lengths.
        when(chunks.nextChunk(1,0,10)).thenReturn(List.of(row(1,"[8,6]")));
        when(users.selectById(1L)).thenReturn(row(1,"[8,6]"));
        assertEquals(1L,new FaceSearchService(chunks,users,1,10,1000,0.79f).search(List.of(3f,0f),1).getId());
        assertNull(new FaceSearchService(chunks,users,1,10,1000,0.81f).search(List.of(3f,0f),1));
    }
    @Test void malformedAndMismatchedRowsAreSkipped() {
        FaceSearchMapper chunks=mock(FaceSearchMapper.class); UserMapper users=mock(UserMapper.class);
        when(chunks.nextChunk(1,0,10)).thenReturn(List.of(row(1,"[1,0,0]"),row(2,"not json"),row(3,"[0,0]"),row(4,"[\"a\",1]"),row(5,"[0.6,0.8]")));
        when(users.selectById(5L)).thenReturn(row(5,"[0.6,0.8]"));
        assertEquals(5L,new FaceSearchService(chunks,users,1,10,1000).search(List.of(0f,1f),1).getId());
    }
    @Test void parserMatchesJacksonOnLargeVectors() throws Exception {
        float[] values=new float[1024]; java.util.Random r=new java.util.Random(7);
        for(int i=0;i<values.length;i++) values[i]=(float)r.nextGaussian();
        String json=new com.fasterxml.jackson.databind.ObjectMapper().writeValueAsString(values);
        assertArrayEquals(values,com.bonuos.face.util.FaceUtils.parseFeatureArray(json));
        assertNull(com.bonuos.face.util.FaceUtils.parseFeatureArray("[1,2] trailing"));
        assertNull(com.bonuos.face.util.FaceUtils.parseFeatureArray("{\"a\":1}"));
    }
}
