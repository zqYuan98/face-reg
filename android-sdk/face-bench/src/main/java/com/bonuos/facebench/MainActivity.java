package com.bonuos.facebench;

import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Bundle;
import android.text.InputType;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import androidx.appcompat.app.AppCompatActivity;
import com.bonuos.facebench.backend.BackendRegistry;
import com.bonuos.facebench.backend.BenchConfig;
import com.bonuos.facebench.bench.BenchRunner;
import java.io.File;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;

/** Entry screen: storage paths, ArcSoft activation, backend selection and replay. */
public class MainActivity extends AppCompatActivity {
    private static final String PREFS = "bench";

    private TextView summary, log;
    private final List<CheckBox> backendBoxes = new ArrayList<>();
    private EditText featureEveryNth, appId, sdkKey, activeKey;
    private CheckBox idCardModel;
    private Button runButton, cancelButton;
    private final AtomicBoolean cancel = new AtomicBoolean();
    private Thread worker;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setTitle("人脸对标台架");
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(32, 24, 32, 24);
        summary = new TextView(this);
        root.addView(summary);

        Button record = new Button(this);
        record.setText("录制样本");
        record.setOnClickListener(v -> startActivity(new Intent(this, RecordActivity.class)));
        root.addView(record);

        SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        if (BuildConfig.ARCSOFT_AVAILABLE) {
            root.addView(heading("虹软激活（只需联网一次）"));
            appId = edit(root, "APP_ID", prefs.getString("appId", ""));
            sdkKey = edit(root, "SDK_KEY", prefs.getString("sdkKey", ""));
            activeKey = edit(root, "ACTIVE_KEY（仅 Pro 版，XXXX-XXXX-XXXX-XXXX）", prefs.getString("activeKey", ""));
            Button activate = new Button(this);
            activate.setText("激活虹软");
            activate.setOnClickListener(v -> activateArcSoft());
            root.addView(activate);
        } else {
            root.addView(heading("此版本未编入虹软 SDK，构建时加 -Parcsoft.sdk.libs=<虹软 libs 目录>"));
        }

        root.addView(heading("参与对比的后端"));
        for (String name : BackendRegistry.available()) {
            CheckBox box = new CheckBox(this);
            box.setText(name);
            box.setChecked(true);
            backendBoxes.add(box);
            root.addView(box);
        }
        featureEveryNth = edit(root, "每 N 帧提一次特征（1 = 每帧）", "3");
        featureEveryNth.setInputType(InputType.TYPE_CLASS_NUMBER);
        idCardModel = new CheckBox(this);
        idCardModel.setText("虹软使用证件照比对模型（底图为身份证芯片照时勾选）");
        root.addView(idCardModel);

        LinearLayout buttons = new LinearLayout(this);
        runButton = new Button(this);
        runButton.setText("运行对标");
        runButton.setOnClickListener(v -> runBench());
        cancelButton = new Button(this);
        cancelButton.setText("取消");
        cancelButton.setEnabled(false);
        cancelButton.setOnClickListener(v -> cancel.set(true));
        buttons.addView(runButton);
        buttons.addView(cancelButton);
        root.addView(buttons);
        log = new TextView(this);
        root.addView(log);

        ScrollView scroll = new ScrollView(this);
        scroll.addView(root);
        setContentView(scroll);
    }

    @Override
    protected void onResume() {
        super.onResume();
        File base = RecordActivity.storageRoot(this);
        String[] clips = BenchRunner.clipsDir(base).list((d, n) -> n.endsWith(".fclip"));
        String[] photos = BenchRunner.enrollDir(base).list((d, n) -> n.matches("(?i).+\\.(jpe?g|png)"));
        summary.setText("数据目录：" + base
                + "\n录制片段：" + (clips == null ? 0 : clips.length)
                + "\n注册照（enroll/<人员编号>.jpg）：" + (photos == null ? 0 : photos.length)
                + "\n结果目录：" + BenchRunner.runsDir(base));
    }

    private TextView heading(String text) {
        TextView view = new TextView(this);
        view.setText(text);
        view.setPadding(0, 24, 0, 8);
        return view;
    }

    private EditText edit(LinearLayout parent, String hint, String value) {
        EditText edit = new EditText(this);
        edit.setHint(hint);
        edit.setSingleLine(true);
        edit.setText(value);
        parent.addView(edit);
        return edit;
    }

    private void activateArcSoft() {
        String id = appId.getText().toString().trim(), key = sdkKey.getText().toString().trim();
        String active = activeKey.getText().toString().trim();
        // Credentials stay in this app's private storage; they are never written to logs or result files.
        getSharedPreferences(PREFS, MODE_PRIVATE).edit().putString("appId", id).putString("sdkKey", key)
                .putString("activeKey", active).apply();
        append("正在激活……");
        new Thread(() -> {
            String message;
            try {
                int code = BackendRegistry.activateArcSoft(this, id, key, active);
                message = BackendRegistry.isArcSoftActivationSuccess(code) ? "虹软已激活（返回码 " + code + "）" : "激活失败，返回码 " + code;
            } catch (Exception error) {
                message = "激活失败：" + error.getMessage();
            }
            String text = message;
            runOnUiThread(() -> append(text));
        }, "arcsoft-activation").start();
    }

    private void runBench() {
        if (worker != null && worker.isAlive()) return;
        BenchConfig config = new BenchConfig();
        for (CheckBox box : backendBoxes) if (box.isChecked()) config.backends.add(box.getText().toString());
        config.arcSoftIdCardModel = idCardModel.isChecked();
        try {
            config.featureEveryNth = Integer.parseInt(featureEveryNth.getText().toString().trim());
            config.validate();
        } catch (RuntimeException error) {
            append("配置有误：" + error.getMessage());
            return;
        }
        cancel.set(false);
        runButton.setEnabled(false);
        cancelButton.setEnabled(true);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        File base = RecordActivity.storageRoot(this);
        worker = new Thread(() -> {
            String result;
            try {
                File out = new BenchRunner().run(this, base, config, new BenchRunner.Progress() {
                    @Override public void update(String message) { runOnUiThread(() -> append(message)); }
                    @Override public boolean cancelled() { return cancel.get(); }
                });
                result = (cancel.get() ? "已取消，部分结果在 " : "完成，结果在 ") + out;
            } catch (Exception error) {
                result = "运行失败：" + error.getMessage();
            }
            String text = result;
            runOnUiThread(() -> {
                append(text);
                runButton.setEnabled(true);
                cancelButton.setEnabled(false);
                getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
            });
        }, "bench-runner");
        worker.start();
    }

    private void append(String line) {
        log.append(line + "\n");
    }
}
