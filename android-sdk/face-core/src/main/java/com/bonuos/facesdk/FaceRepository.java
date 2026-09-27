package com.bonuos.facesdk;

import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONException;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.ArrayList;
import java.util.List;

public class FaceRepository {
    private static final String TAG = "FaceRepository";
    private FaceDatabaseHelper dbHelper;

    public FaceRepository(Context context) {
        dbHelper = new FaceDatabaseHelper(context);
    }

    public void saveUser(long id, String userNo, String name, Long groupId, String featureJson, String photoPath, long updateTime) {
        byte[] featureBlob = convertFeatureJsonToBlob(featureJson);
        saveUserInternal(id, userNo, name, groupId, featureBlob, photoPath, updateTime);
    }

    public void saveUser(long id, String userNo, String name, Long groupId, float[] feature, String photoPath, long updateTime) {
        byte[] featureBlob = convertFloatArrayToBlob(feature);
        saveUserInternal(id, userNo, name, groupId, featureBlob, photoPath, updateTime);
    }

    private void saveUserInternal(long id, String userNo, String name, Long groupId, byte[] featureBlob, String photoPath,
            long updateTime) {
        SQLiteDatabase db = dbHelper.getWritableDatabase();
        ContentValues values = new ContentValues();
        values.put(FaceDatabaseHelper.COLUMN_ID, id);
        values.put(FaceDatabaseHelper.COLUMN_USER_NO, userNo);
        values.put(FaceDatabaseHelper.COLUMN_NAME, name);
        if (groupId != null) {
            values.put(FaceDatabaseHelper.COLUMN_GROUP_ID, groupId);
        }
        values.put(FaceDatabaseHelper.COLUMN_PHOTO_PATH, photoPath);
        values.put(FaceDatabaseHelper.COLUMN_UPDATE_TIME, updateTime);

        if (featureBlob != null) {
            values.put(FaceDatabaseHelper.COLUMN_FEATURE, featureBlob);
        }

        long rowId = db.replace(FaceDatabaseHelper.TABLE_USERS, null, values);
        if (rowId == -1) {
            throw new IllegalStateException("Failed to save user");
        } else {
            // Do not log person names or feature values.
        }
    }

    public List<FaceUser> getAllUsers() {
        List<FaceUser> users = new ArrayList<>();
        SQLiteDatabase db = dbHelper.getReadableDatabase();
        Cursor cursor = db.query(FaceDatabaseHelper.TABLE_USERS, null, null, null, null, null, null);

        if (cursor != null) {
            while (cursor.moveToNext()) {
                FaceUser user = new FaceUser();
                user.id = cursor.getLong(cursor.getColumnIndexOrThrow(FaceDatabaseHelper.COLUMN_ID));
                user.userNo = cursor.getString(cursor.getColumnIndexOrThrow(FaceDatabaseHelper.COLUMN_USER_NO));
                user.name = cursor.getString(cursor.getColumnIndexOrThrow(FaceDatabaseHelper.COLUMN_NAME));
                int groupIdx = cursor.getColumnIndex(FaceDatabaseHelper.COLUMN_GROUP_ID);
                if (groupIdx != -1 && !cursor.isNull(groupIdx)) {
                    user.groupId = cursor.getLong(groupIdx);
                }
                user.photoPath = cursor.getString(cursor.getColumnIndexOrThrow(FaceDatabaseHelper.COLUMN_PHOTO_PATH));
                byte[] blob = cursor.getBlob(cursor.getColumnIndexOrThrow(FaceDatabaseHelper.COLUMN_FEATURE));
                user.feature = convertBlobToFloatArray(blob);
                users.add(user);
            }
            cursor.close();
        }
        return users;
    }

    private byte[] convertFloatArrayToBlob(float[] features) {
        if (features == null)
            return null;
        ByteBuffer buffer = ByteBuffer.allocate(features.length * 4);
        buffer.order(ByteOrder.LITTLE_ENDIAN);
        for (float f : features) {
            buffer.putFloat(f);
        }
        return buffer.array();
    }

    private byte[] convertFeatureJsonToBlob(String json) {
        if (json == null) return null;
        try {
            JSONArray array = new JSONArray(json);
            if (array.length() != 512 && array.length() != 1024) throw new IllegalArgumentException("Invalid feature dimension");
            ByteBuffer buffer = ByteBuffer.allocate(array.length() * 4).order(ByteOrder.LITTLE_ENDIAN);
            for (int i = 0; i < array.length(); i++) {
                float value = (float) array.getDouble(i);
                if (Float.isNaN(value) || Float.isInfinite(value)) throw new IllegalArgumentException("Invalid feature value");
                buffer.putFloat(value);
            }
            return buffer.array();
        } catch (JSONException error) {
            throw new IllegalArgumentException("Invalid feature data", error);
        }
    }

    private float[] convertBlobToFloatArray(byte[] blob) {
        if (blob == null)
            return null;
        ByteBuffer buffer = ByteBuffer.wrap(blob);
        buffer.order(ByteOrder.LITTLE_ENDIAN);
        float[] features = new float[blob.length / 4];
        for (int i = 0; i < features.length; i++) {
            features[i] = buffer.getFloat();
        }
        return features;
    }

    public void close() {
        if (dbHelper != null) {
            dbHelper.close();
        }
    }

    /**
     * 删除本地存在但不在指定ID集合中的用户
     * 
     * @param validIds 有效的用户ID集合
     * @return 删除的记录数
     */
    public int deleteUsersNotInSet(java.util.Set<Long> validIds) {
        if (validIds == null || validIds.isEmpty()) {
            // 如果后端没有用户,清空所有本地用户
            return clearAllUsers();
        }

        SQLiteDatabase db = dbHelper.getWritableDatabase();

        // 构建 NOT IN 子句
        StringBuilder whereClause = new StringBuilder(FaceDatabaseHelper.COLUMN_ID + " NOT IN (");
        String[] whereArgs = new String[validIds.size()];
        int index = 0;
        for (Long id : validIds) {
            if (index > 0) {
                whereClause.append(",");
            }
            whereClause.append("?");
            whereArgs[index++] = String.valueOf(id);
        }
        whereClause.append(")");

        int count = db.delete(FaceDatabaseHelper.TABLE_USERS, whereClause.toString(), whereArgs);
        if (count > 0) {
            Log.i(TAG, "Deleted " + count + " users that are not in backend");
        }
        return count;
    }

    /**
     * 清空所有用户数据
     * 
     * @return 删除的记录数
     */
    public int clearAllUsers() {
        SQLiteDatabase db = dbHelper.getWritableDatabase();
        int count = db.delete(FaceDatabaseHelper.TABLE_USERS, null, null);
        Log.i(TAG, "Cleared " + count + " users from local database");
        return count;
    }

    public static class FaceUser {
        public long id;
        public String userNo;
        public String name;
        public Long groupId;
        public String photoPath;
        public float[] feature;
    }
}
