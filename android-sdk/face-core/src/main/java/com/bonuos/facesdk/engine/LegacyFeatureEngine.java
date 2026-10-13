package com.bonuos.facesdk.engine;

import android.graphics.Bitmap;
import com.bonuos.facesdk.FaceRecognizer;
import com.bonuos.facesdk.image.Nv21;
import java.util.concurrent.Callable;

/**
 * The prebuilt 1024-d recognizer behind {@link FaceSession.FeatureEngine}. It is fed a crop
 * around the tracked face rather than the whole frame: only the crop is colour-converted, the
 * face is larger in the recognizer's fixed 640x640 detector input, and the feature always comes
 * from the tracked person instead of whichever face the recognizer happens to pick.
 */
public final class LegacyFeatureEngine implements FaceSession.FeatureEngine {
    private final FaceRecognizer recognizer;

    public LegacyFeatureEngine(FaceRecognizer recognizer) {
        this.recognizer = recognizer;
    }

    @Override
    public Callable<float[]> capture(CameraFrame frame, FaceRect face, FaceRect crop) {
        final int left = Math.max(0, (int) Math.floor(crop.left)), top = Math.max(0, (int) Math.floor(crop.top));
        final int width = Math.min(frame.uprightWidth(), (int) Math.ceil(crop.right)) - left;
        final int height = Math.min(frame.uprightHeight(), (int) Math.ceil(crop.bottom)) - top;
        if (width <= 0 || height <= 0) throw new IllegalArgumentException("Empty crop");
        final int[] pixels = new int[width * height];
        Nv21.cropToArgb(frame.nv21, frame.width, frame.height, frame.rotationDegrees, left, top, width, height, pixels);
        return () -> {
            Bitmap bitmap = Bitmap.createBitmap(pixels, width, height, Bitmap.Config.ARGB_8888);
            try {
                return recognizer.extractFeature(bitmap);
            } finally {
                bitmap.recycle();
            }
        };
    }
}
