package com.bonuos.facesdk;

import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;

/** Staging is disposable; only publish() changes the online dataset and confirmed cursor. */
final class SyncStore implements AutoCloseable {
    private final FaceDatabaseHelper helper;
    private boolean reuseExistingPhotos = true;
    private static final String COLUMNS = "id,user_no,name,group_id,feature,photo_path,update_time,sync_version,photo_url";
    SyncStore(Context context) { helper = new FaceDatabaseHelper(context); }

    static final class State {
        final String epoch, scopeVersion, identity;
        final long revision;
        State(String epoch, String scope, long revision, String identity) {
            this.epoch = epoch; scopeVersion = scope; this.revision = revision; this.identity = identity;
        }
    }

    State state() {
        try (Cursor cursor = helper.getReadableDatabase().rawQuery("SELECT epoch,scope_version,revision,identity FROM sync_meta WHERE id=1", null)) {
            return cursor.moveToFirst() ? new State(cursor.getString(0), cursor.getString(1), cursor.getLong(2), cursor.getString(3)) : null;
        }
    }

    int count() {
        try (Cursor cursor = helper.getReadableDatabase().rawQuery("SELECT COUNT(*) FROM users", null)) {
            cursor.moveToFirst(); return cursor.getInt(0);
        }
    }

    void begin(boolean incremental) {
        begin(incremental, true);
    }

    void begin(boolean incremental, boolean reuseExistingPhotos) {
        this.reuseExistingPhotos = reuseExistingPhotos;
        SQLiteDatabase db = helper.getWritableDatabase();
        db.beginTransaction();
        try {
            db.delete("sync_stage", null, null);
            if (incremental) {
                db.execSQL("INSERT INTO sync_stage (" + COLUMNS + ",deleted) SELECT " + COLUMNS + ",0 FROM users");
                db.execSQL("INSERT OR REPLACE INTO sync_stage (id,sync_version,deleted) SELECT id,sync_version,1 FROM sync_tombstones");
            }
            db.setTransactionSuccessful();
        } finally { db.endTransaction(); }
    }

    void upsert(JSONObject user, long revision, String photoUrl) throws JSONException {
        long id = user.getLong("id");
        if (id <= 0 || revision < 0 || user.optInt("status", 1) != 1) throw new JSONException("Invalid synchronized user");
        if (version(id) >= revision) return;
        String name = user.getString("name");
        JSONArray feature = user.isNull("featureData") ? null : new JSONArray(user.getString("featureData"));
        byte[] featureBytes = null;
        if (feature != null) {
            if (feature.length() != 1024) throw new JSONException("Invalid feature dimension");
            ByteBuffer bytes = ByteBuffer.allocate(4096).order(ByteOrder.LITTLE_ENDIAN);
            double norm = 0;
            for (int i = 0; i < feature.length(); i++) {
                float value = (float) feature.getDouble(i);
                if (Float.isNaN(value) || Float.isInfinite(value)) throw new JSONException("Invalid feature value");
                norm += (double) value * value;
                bytes.putFloat(value);
            }
            if (!(norm > 0)) throw new JSONException("Empty feature");
            featureBytes = bytes.array();
        }
        ContentValues values = new ContentValues();
        values.put("id", id); values.put("user_no", user.optString("userNo", String.valueOf(id))); values.put("name", name);
        if (user.isNull("groupId")) values.putNull("group_id"); else values.put("group_id", user.getLong("groupId"));
        values.put("feature", featureBytes); values.put("photo_path", ""); values.put("photo_url", photoUrl);
        values.put("update_time", System.currentTimeMillis()); values.put("sync_version", revision); values.put("deleted", 0);
        if (helper.getWritableDatabase().insertWithOnConflict("sync_stage", null, values, SQLiteDatabase.CONFLICT_REPLACE) < 0) {
            throw new IllegalStateException("Staging write failed");
        }
    }

    void delete(long id, long revision) {
        if (id <= 0 || revision < 0) throw new IllegalArgumentException("Invalid tombstone");
        if (version(id) >= revision) return;
        ContentValues values = new ContentValues();
        values.put("id", id); values.put("sync_version", revision); values.put("deleted", 1);
        if (helper.getWritableDatabase().insertWithOnConflict("sync_stage", null, values, SQLiteDatabase.CONFLICT_REPLACE) < 0) {
            throw new IllegalStateException("Tombstone persistence failed");
        }
    }

    void resolvePhotos(SyncTransport transport) throws Exception {
        long after = 0;
        while (true) {
            java.util.List<Object[]> page = new java.util.ArrayList<>();
            try (Cursor cursor = helper.getReadableDatabase().rawQuery(
                    "SELECT s.id,s.sync_version,s.photo_url,u.photo_path,u.photo_url FROM sync_stage s LEFT JOIN users u ON s.id=u.id " +
                    "WHERE s.deleted=0 AND s.id>? AND s.photo_url IS NOT NULL AND s.photo_url<>'' ORDER BY s.id LIMIT 200",
                    new String[]{String.valueOf(after)})) {
                while (cursor.moveToNext()) page.add(new Object[]{cursor.getLong(0), cursor.getLong(1), cursor.getString(2), cursor.getString(3), cursor.getString(4)});
            }
            if (page.isEmpty()) return;
            for (Object[] row : page) {
                long id = (Long) row[0];
                String url = (String) row[2], oldPath = (String) row[3];
                String path = reuseExistingPhotos && url.equals(row[4]) && oldPath != null && new java.io.File(oldPath).isFile() ?
                        oldPath : transport.photo(url, id, (Long) row[1]);
                ContentValues values = new ContentValues(); values.put("photo_path", path);
                if (helper.getWritableDatabase().update("sync_stage", values, "id=?", new String[]{String.valueOf(id)}) != 1) {
                    throw new IllegalStateException("Photo staging update failed");
                }
                after = id;
            }
        }
    }

    private long version(long id) {
        try (Cursor cursor = helper.getReadableDatabase().rawQuery("SELECT sync_version FROM sync_stage WHERE id=?", new String[]{String.valueOf(id)})) {
            return cursor.moveToFirst() ? cursor.getLong(0) : -1;
        }
    }

    int publish(State state) {
        if (Thread.currentThread().isInterrupted()) throw new IllegalStateException("Sync cancelled before publication");
        final int[] count = new int[1];
        FaceDatasetCoordinator.publish(() -> {
            SQLiteDatabase db = helper.getWritableDatabase();
            db.beginTransaction();
            try {
                db.delete("users", null, null);
                db.execSQL("INSERT INTO users (" + COLUMNS + ") SELECT " + COLUMNS + " FROM sync_stage WHERE deleted=0");
                db.delete("sync_tombstones", null, null);
                db.execSQL("INSERT INTO sync_tombstones SELECT id,sync_version FROM sync_stage WHERE deleted=1");
                ContentValues values = new ContentValues();
                values.put("id", 1); values.put("epoch", state.epoch); values.put("scope_version", state.scopeVersion);
                values.put("revision", state.revision); values.put("identity", state.identity);
                if (db.insertWithOnConflict("sync_meta", null, values, SQLiteDatabase.CONFLICT_REPLACE) < 0) {
                    throw new IllegalStateException("Cursor persistence failed");
                }
                try (Cursor cursor = db.rawQuery("SELECT COUNT(*) FROM users", null)) { cursor.moveToFirst(); count[0] = cursor.getInt(0); }
                db.delete("sync_stage", null, null);
                db.setTransactionSuccessful();
            } finally { db.endTransaction(); }
        });
        return count[0];
    }

    @Override public void close() { helper.close(); }
}
