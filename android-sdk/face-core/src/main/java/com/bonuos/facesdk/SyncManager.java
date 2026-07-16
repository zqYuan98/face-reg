package com.bonuos.facesdk;

import android.content.Context;
import android.util.Log;
import org.json.JSONArray;
import org.json.JSONObject;
import org.java_websocket.client.WebSocketClient;
import org.java_websocket.handshake.ServerHandshake;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.URL;

public class SyncManager {
    private static final String TAG = "SyncManager";
    // 请确保此 IP 与您的电脑 IP 一致，且 Android 设备能访问
    private static final String SYNC_URL = "http://<server-37>:18080/api/sync/users";
    private static final String WS_URL = "ws://<server-37>:18080/ws/sync";

    private Context context;
    private boolean isSyncing = false;
    private WebSocketClient webSocketClient;

    public interface SyncCallback {
        void onSuccess(int count);

        void onError(String msg);
    }

    public SyncManager(Context context) {
        this.context = context;
    }

    public void syncData(SyncCallback callback) {
        if (isSyncing) {
            Log.i(TAG, "Sync already in progress, skipping.");
            return;
        }
        isSyncing = true;
        new Thread(() -> {
            try {
                Log.i(TAG, "Starting sync...");
                // 强制全量同步，以确保 deleteUsersNotInSet 逻辑正确
                // 如果使用增量同步，后端返回空列表会导致本地数据被清空
                URL url = new URL(SYNC_URL); // 不带 lastSyncTime 参数
                HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                conn.setRequestMethod("GET");
                conn.setConnectTimeout(5000);

                int responseCode = conn.getResponseCode();
                if (responseCode == 200) {
                    BufferedReader reader = new BufferedReader(new InputStreamReader(conn.getInputStream()));
                    StringBuilder response = new StringBuilder();
                    String line;
                    while ((line = reader.readLine()) != null) {
                        response.append(line);
                    }
                    reader.close();

                    int count = parseAndSaveData(response.toString());
                    if (callback != null) {
                        callback.onSuccess(count);
                    }
                } else {
                    Log.e(TAG, "Sync failed with code: " + responseCode);
                    if (callback != null) {
                        callback.onError("Sync failed: " + responseCode);
                    }
                }
            } catch (Exception e) {
                Log.e(TAG, "Sync error", e);
                if (callback != null) {
                    callback.onError("Sync error: " + e.getMessage());
                }
            } finally {
                isSyncing = false;
            }
        }).start();
    }

    public void startAutoSync(long intervalSeconds, SyncCallback callback) {
        // 首次启动先同步一次
        syncData(callback);
        connectWebSocket(callback);
    }

    public void stopAutoSync() {
        if (webSocketClient != null) {
            Log.i(TAG, "Closing WebSocket...");
            webSocketClient.close();
            webSocketClient = null;
        }
    }

    private void connectWebSocket(SyncCallback callback) {
        try {
            URI uri = new URI(WS_URL);
            webSocketClient = new WebSocketClient(uri) {
                @Override
                public void onOpen(ServerHandshake handshakedata) {
                    Log.i(TAG, "WebSocket Connected");
                }

                @Override
                public void onMessage(String message) {
                    Log.i(TAG, "WebSocket Message: " + message);
                    if ("UPDATE".equals(message)) {
                        Log.i(TAG, "Received UPDATE signal, triggering sync...");
                        syncData(callback);
                    }
                }

                @Override
                public void onClose(int code, String reason, boolean remote) {
                    Log.w(TAG, "WebSocket Closed: " + reason);
                    // 简单的重连机制：5秒后重试
                    new Thread(() -> {
                        try {
                            Thread.sleep(5000);
                            if (webSocketClient != null && webSocketClient.isClosed()) {
                                Log.i(TAG, "Reconnecting WebSocket...");
                                reconnect();
                            }
                        } catch (InterruptedException e) {
                            e.printStackTrace();
                        }
                    }).start();
                }

                @Override
                public void onError(Exception ex) {
                    Log.e(TAG, "WebSocket Error", ex);
                }
            };
            webSocketClient.connect();
        } catch (Exception e) {
            Log.e(TAG, "WebSocket Connect Failed", e);
        }
    }

    private int parseAndSaveData(String jsonResponse) {
        int count = 0;
        try {
            JSONObject json = new JSONObject(jsonResponse);
            if (json.getInt("code") == 200) {
                JSONArray users = json.getJSONArray("data");
                Log.i(TAG, "Received " + users.length() + " users to sync");

                FaceRepository repository = new FaceRepository(context);

                // 确保图片目录存在
                java.io.File faceDir = new java.io.File(context.getFilesDir(), "faces");
                if (!faceDir.exists()) {
                    faceDir.mkdirs();
                }

                // 收集后端所有用户的ID
                java.util.Set<Long> backendUserIds = new java.util.HashSet<>();

                for (int i = 0; i < users.length(); i++) {
                    JSONObject user = users.getJSONObject(i);
                    long id = user.getLong("id");
                    String userNo = user.optString("userNo", String.valueOf(id));
                    String name = user.getString("name");
                    String featureData = user.getString("featureData");
                    Log.d(TAG, "User: " + name + ", FeatureData length: " + (featureData != null ? featureData.length() : "null"));
                    if (featureData != null && featureData.length() > 20) {
                        Log.d(TAG, "FeatureData preview: " + featureData.substring(0, 20) + "...");
                    }
                    String photoUrl = user.optString("photoUrl", "");
                    Long groupId = user.has("groupId") && !user.isNull("groupId") ? user.getLong("groupId") : null;
                    long updateTime = System.currentTimeMillis();

                    // 添加到后端用户ID集合
                    backendUserIds.add(id);

                    // 下载图片到本地
                    String localPhotoPath = "";
                    if (!photoUrl.isEmpty()) {
                        localPhotoPath = downloadImage(photoUrl, faceDir, id);
                    }

                    // 添加详细日志
                    Log.d(TAG, "Syncing user: " + name + " (ID: " + id + ", GroupId: " + groupId + ")");
                    // Log.d(TAG, "Feature data length: " + featureData.length());

                    // 保存本地路径而不是网络 URL
                    repository.saveUser(id, userNo, name, groupId, featureData, localPhotoPath, updateTime);
                    count++;
                }

                // 删除本地存在但后端已删除的用户
                int deletedCount = repository.deleteUsersNotInSet(backendUserIds);
                if (deletedCount > 0) {
                    Log.i(TAG, "Deleted " + deletedCount + " users that no longer exist on backend");
                }

                repository.close(); // 记得关闭
            }
        } catch (Exception e) {
            Log.e(TAG, "Parse error", e);
        }
        return count;
    }

    private String downloadImage(String urlString, java.io.File dir, long userId) {
        try {
            // 处理相对路径，假设后端在同一服务器
            if (!urlString.startsWith("http")) {
                // 提取 SYNC_URL 的 base URL
                String baseUrl = SYNC_URL.substring(0, SYNC_URL.indexOf("/api"));
                if (urlString.startsWith("/")) {
                    urlString = baseUrl + urlString;
                } else {
                    urlString = baseUrl + "/" + urlString;
                }
            }

            Log.d(TAG, "Attempting to download image from: " + urlString);
            URL url = new URL(urlString);
            HttpURLConnection conn = (HttpURLConnection) url.openConnection();
            conn.setConnectTimeout(5000);
            conn.setReadTimeout(5000);

            int responseCode = conn.getResponseCode();
            Log.d(TAG, "Download response code: " + responseCode);

            if (responseCode == 200) {
                java.io.File file = new java.io.File(dir, userId + ".jpg");
                java.io.InputStream is = conn.getInputStream();
                java.io.FileOutputStream fos = new java.io.FileOutputStream(file);

                byte[] buffer = new byte[1024];
                int len;
                while ((len = is.read(buffer)) != -1) {
                    fos.write(buffer, 0, len);
                }

                fos.close();
                is.close();
                Log.i(TAG, "Image downloaded successfully to: " + file.getAbsolutePath());
                return file.getAbsolutePath();
            } else {
                Log.e(TAG, "Failed to download image, response code: " + responseCode);
            }
        } catch (Exception e) {
            Log.e(TAG, "Failed to download image: " + urlString, e);
        }
        return "";
    }
}
