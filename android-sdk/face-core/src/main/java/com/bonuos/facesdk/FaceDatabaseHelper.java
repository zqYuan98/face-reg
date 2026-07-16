package com.bonuos.facesdk;

import android.content.Context;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;

public class FaceDatabaseHelper extends SQLiteOpenHelper {
    private static final String DATABASE_NAME = "face_sdk.db";
    private static final int DATABASE_VERSION = 2; // Incremented version

    public static final String TABLE_USERS = "users";
    public static final String COLUMN_ID = "id";
    public static final String COLUMN_USER_NO = "user_no";
    public static final String COLUMN_NAME = "name";
    public static final String COLUMN_GROUP_ID = "group_id"; // New column
    public static final String COLUMN_FEATURE = "feature";
    public static final String COLUMN_PHOTO_PATH = "photo_path";
    public static final String COLUMN_UPDATE_TIME = "update_time";

    private static final String TABLE_CREATE = "CREATE TABLE " + TABLE_USERS + " (" +
            COLUMN_ID + " INTEGER PRIMARY KEY, " +
            COLUMN_USER_NO + " TEXT, " +
            COLUMN_NAME + " TEXT, " +
            COLUMN_GROUP_ID + " INTEGER, " + // Add to create statement
            COLUMN_FEATURE + " BLOB, " +
            COLUMN_PHOTO_PATH + " TEXT, " +
            COLUMN_UPDATE_TIME + " INTEGER" +
            ")";

    public FaceDatabaseHelper(Context context) {
        super(context, DATABASE_NAME, null, DATABASE_VERSION);
    }

    @Override
    public void onConfigure(SQLiteDatabase db) {
        super.onConfigure(db);
        db.enableWriteAheadLogging(); // 开启 WAL 模式，支持并发读写
    }

    @Override
    public void onCreate(SQLiteDatabase db) {
        db.execSQL(TABLE_CREATE);
    }

    @Override
    public void onUpgrade(SQLiteDatabase db, int oldVersion, int newVersion) {
        if (oldVersion < 2) {
            // Add group_id column for version 2
            db.execSQL("ALTER TABLE " + TABLE_USERS + " ADD COLUMN " + COLUMN_GROUP_ID + " INTEGER");
        }
    }
}
