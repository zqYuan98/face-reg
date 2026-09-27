package com.bonuos.face.service;

import com.bonuos.face.mapper.UserMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.mock.web.MockMultipartFile;
import java.nio.file.*;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.any;
import static org.junit.jupiter.api.Assertions.*;

class PhotoStorageTest {
    @TempDir Path root;
    @Test void ignoresClientFilenameAndCompensatesUnreferencedPhoto() throws Exception {
        UserMapper users=mock(UserMapper.class); when(users.selectCount(any())).thenReturn(0L);
        PhotoStorage storage=new PhotoStorage(root.toString(),users);
        Path staged=storage.stage(new MockMultipartFile("photo","../../outside.jpg","image/jpeg",new byte[]{1,2}));
        assertEquals(root.resolve(".staging"),staged.getParent());
        String name=storage.publish(staged); assertTrue(Files.exists(root.resolve(name))); assertFalse(Files.exists(staged));
        storage.retire(name); assertFalse(Files.exists(root.resolve(name)));
    }
    @Test void neverRemovesPhotoStillReferencedAfterAmbiguousCommit() throws Exception {
        UserMapper users=mock(UserMapper.class); when(users.selectCount(any())).thenReturn(1L);
        PhotoStorage storage=new PhotoStorage(root.toString(),users);
        Files.writeString(root.resolve("in-use.jpg"),"fixture");
        storage.retire("in-use.jpg"); assertTrue(Files.exists(root.resolve("in-use.jpg")));
    }
    @Test void databaseFailureLeavesDurableRetryMarker() throws Exception {
        UserMapper users=mock(UserMapper.class); when(users.selectCount(any())).thenThrow(new IllegalStateException("offline"));
        PhotoStorage storage=new PhotoStorage(root.toString(),users);
        Files.writeString(root.resolve("old.jpg"),"fixture"); storage.retire("old.jpg");
        assertTrue(Files.exists(root.resolve(".cleanup/old.jpg")));
        doReturn(0L).when(users).selectCount(any()); storage.retryCleanup();
        assertFalse(Files.exists(root.resolve("old.jpg"))); assertFalse(Files.exists(root.resolve(".cleanup/old.jpg")));
    }
    @Test void malformedLegacyFilenameNeverTurnsCommittedDeletionIntoFailure() {
        PhotoStorage storage=new PhotoStorage(root.toString(),mock(UserMapper.class));
        assertDoesNotThrow(()->storage.retire("bad\u0000.jpg"));
    }
    @Test void retainedChangePostimageKeepsOldPhotoUntilLogIsPruned() throws Exception {
        UserMapper users=mock(UserMapper.class); when(users.selectCount(any())).thenReturn(0L);
        when(users.countRetainedPhotoReferences("old.jpg")).thenReturn(1L);
        PhotoStorage storage=new PhotoStorage(root.toString(),users);
        Files.writeString(root.resolve("old.jpg"),"fixture"); storage.retire("old.jpg");
        assertTrue(Files.exists(root.resolve("old.jpg"))); assertTrue(Files.exists(root.resolve(".cleanup/old.jpg")));
    }
}
