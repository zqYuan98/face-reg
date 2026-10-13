package com.bonuos.faceapp;

import android.content.Intent;
import android.os.Bundle;
import android.widget.Toast;

import androidx.appcompat.app.AppCompatActivity;

import com.bonuos.facesdk.SyncConfig;
import com.bonuos.facesdk.SyncManager;

public class MainActivity extends AppCompatActivity {

    private SyncManager syncManager;
    private android.widget.TextView tvSyncStatus;
    private java.text.SimpleDateFormat dateFormat = new java.text.SimpleDateFormat("HH:mm:ss", java.util.Locale.getDefault());

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        configureSyncFromBuild();
        syncManager = new SyncManager(this);

        tvSyncStatus = findViewById(R.id.tv_sync_status);

        findViewById(R.id.btn_enter).setOnClickListener(v -> {
            Intent intent = new Intent(MainActivity.this, FaceRecognitionActivity.class);
            startActivity(intent);
        });
    }
    /**
     * Test builds take the server and device credential from local.properties (face.sync.*).
     * Production devices must be provisioned by a secure enrolment flow instead.
     */
    private void configureSyncFromBuild() {
        if (BuildConfig.SYNC_URL.isEmpty()) return;
        try {
            SyncManager.configure(new SyncConfig(BuildConfig.SYNC_URL, BuildConfig.SYNC_DEVICE_SN,
                    BuildConfig.SYNC_DEVICE_SECRET, SyncConfig.Protocol.V2, BuildConfig.SYNC_ALLOW_CLEARTEXT));
        } catch (IllegalArgumentException error) {
            Toast.makeText(this, "同步配置无效: " + error.getMessage(), Toast.LENGTH_LONG).show();
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (syncManager != null) {
            // 每30秒自动同步一次
            syncManager.startAutoSync(30, new SyncManager.SyncCallback() {
                @Override
                public void onSuccess(int count) {
                    runOnUiThread(() -> {
                        String time = dateFormat.format(new java.util.Date());
                        if (count > 0) {
                            tvSyncStatus.setText("最近同步: " + time + " (更新 " + count + " 条)");
                        } else {
                            tvSyncStatus.setText("最近同步: " + time + " (无更新)");
                        }
                    });
                }

                @Override
                public void onError(String msg) {
                    runOnUiThread(() -> {
                        String time = dateFormat.format(new java.util.Date());
                        tvSyncStatus.setText("同步失败: " + time);
                    });
                }
            });
        }
    }

    @Override
    protected void onPause() {
        super.onPause();
        if (syncManager != null) {
            syncManager.stopAutoSync();
        }
    }

    @Override protected void onDestroy() {
        if (syncManager != null) syncManager.close();
        super.onDestroy();
    }
}
