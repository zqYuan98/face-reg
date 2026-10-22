package com.bonuos.face.service;

import com.bonuos.face.entity.User;
import com.bonuos.face.mapper.*;
import com.bonuos.face.exception.ApiException;
import org.junit.jupiter.api.Test;
import java.util.List;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.*;

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
    @Test void closeRunnerUpIsAmbiguousWhenMarginIsSet() {
        FaceSearchMapper chunks=mock(FaceSearchMapper.class); UserMapper users=mock(UserMapper.class);
        when(chunks.nextChunk(1,0,10)).thenReturn(List.of(row(1,"[0.8,0.6]"),row(2,"[0.79,0.613]")));
        when(users.selectById(1L)).thenReturn(row(1,"[0.8,0.6]"));
        assertEquals(1L,new FaceSearchService(chunks,users,null,1,10,1000,0.6f,0f).search(List.of(1f,0f),1).getId());
        assertEquals("AMBIGUOUS_MATCH",assertThrows(ApiException.class,
                ()->new FaceSearchService(chunks,users,null,1,10,1000,0.6f,0.05f).search(List.of(1f,0f),1)).getErrorCode());
    }
    @Test void cachedGroupIsSearchedWithoutReadingRows() {
        FaceSearchMapper chunks=mock(FaceSearchMapper.class); UserMapper users=mock(UserMapper.class);
        FaceGallery gallery=mock(FaceGallery.class);
        com.bonuos.face.dto.RevisionClock clock=new com.bonuos.face.dto.RevisionClock(); clock.setEpoch("e"); clock.setHead(4);
        when(chunks.clock()).thenReturn(clock); when(chunks.revision()).thenReturn("e:4");
        when(gallery.search(eq(1L),eq(clock),any())).thenReturn(new FaceGallery.Top2(7L,3L,0.9f,0.2f));
        User winner=row(7,"[1,0]"); winner.setSyncVersion(3L);
        when(users.selectById(7L)).thenReturn(winner);
        assertEquals(7L,new FaceSearchService(chunks,users,gallery,1,10,1000,0.6f,0f).search(List.of(1f,0f),1).getId());
        verify(chunks,never()).nextChunk(anyLong(),anyLong(),anyInt());
    }
    @Test void staleCachedWinnerIsAConflict() {
        FaceSearchMapper chunks=mock(FaceSearchMapper.class); UserMapper users=mock(UserMapper.class);
        FaceGallery gallery=mock(FaceGallery.class);
        com.bonuos.face.dto.RevisionClock clock=new com.bonuos.face.dto.RevisionClock(); clock.setEpoch("e"); clock.setHead(4);
        when(chunks.clock()).thenReturn(clock); when(chunks.revision()).thenReturn("e:4");
        when(gallery.search(eq(1L),eq(clock),any())).thenReturn(new FaceGallery.Top2(7L,3L,0.9f,0.2f));
        User winner=row(7,"[1,0]"); winner.setSyncVersion(5L);
        when(users.selectById(7L)).thenReturn(winner);
        assertEquals("SEARCH_CHANGED",assertThrows(ApiException.class,
                ()->new FaceSearchService(chunks,users,gallery,1,10,1000,0.6f,0f).search(List.of(1f,0f),1)).getErrorCode());
    }
    private static com.bonuos.face.dto.FeatureRow stored(long id, long version, float... feature) {
        com.bonuos.face.dto.FeatureRow r=new com.bonuos.face.dto.FeatureRow(); r.setId(id); r.setSyncVersion(version);
        r.setFeatureBlob(com.bonuos.face.util.FaceUtils.toBlob(feature)); return r;
    }
    @Test void v2SearchReadsStoredBlobsAndSkipsWrongDimensions() {
        FaceSearchMapper chunks=mock(FaceSearchMapper.class); UserMapper users=mock(UserMapper.class);
        com.bonuos.face.mapper.UserFeatureMapper features=mock(com.bonuos.face.mapper.UserFeatureMapper.class);
        float[] a=new float[512], b=new float[512]; a[0]=1; b[1]=1;
        when(features.nextChunk("arcface_r50_512",1,0,10)).thenReturn(List.of(stored(1,1,a),stored(2,1,b),stored(3,1,1f,0f)));
        User winner=row(2,null); winner.setSyncVersion(1L); when(users.selectById(2L)).thenReturn(winner);
        var source=FeatureSource.stored("arcface_r50_512",features);
        var service=new FaceSearchService(chunks,users,source,null,1,10,1000,0.45f,0f);
        assertEquals("arcface_r50_512",service.featureVersion());
        float[] probe=new float[512]; probe[1]=2;
        assertEquals(2L,service.search(probe,1).getId());
        verify(chunks,never()).nextChunk(anyLong(),anyLong(),anyInt());
    }
    @Test void blobsRoundTripLittleEndian() {
        float[] v={1.5f,-0.25f,3e-8f};
        byte[] blob=com.bonuos.face.util.FaceUtils.toBlob(v);
        assertEquals(0x3f,blob[3]&0xff); // 1.5f = 0x3fc00000, little-endian
        assertArrayEquals(v,com.bonuos.face.util.FaceUtils.fromBlob(blob));
        assertNull(com.bonuos.face.util.FaceUtils.fromBlob(new byte[5]));
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
