package com.bonuos.faceapp.widget;

import android.animation.ValueAnimator;
import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.LinearGradient;
import android.graphics.Paint;
import android.graphics.Rect;
import android.graphics.RectF;
import android.graphics.Shader;
import android.util.AttributeSet;
import android.view.View;
import android.view.animation.LinearInterpolator;

import java.text.DecimalFormat;

public class RectView extends View {

    private float confidence = 0F;
    private RectF rect;
    private Paint paint;
    private Paint scanPaint;
    private int color;
    private float radius; // Not used for corners anymore, but kept for compatibility
    private float cornerLength = 50f; // Length of the corner brackets

    private int textPadding;

    private DecimalFormat decimalFormat = new DecimalFormat("0.000");

    private Rect textBackgroundRect = new Rect();
    private Rect textBoundsRect = new Rect();
    private int textWidth;
    private int textHeight;

    private ValueAnimator scanAnimator;
    private float scanLineY = 0f;
    private boolean isScanning = false;

    private static final String SAMPLE_TEXT = "0.789";
    private static final int DEFAULT_LEFT = 0;
    private static final int DEFAULT_TOP = 0;
    private static final int DEFAULT_RIGHT = 0;
    private static final int DEFAULT_BOTTOM = 0;
    private static final float DEFAULT_TEXT_SIZE = 40F;
    private static final int DEFAULT_TEXT_PADDING = 8;
    private static final int DEFAULT_COLOR = Color.GREEN; // Default to green

    public RectView(Context context) {
        this(context, null);
    }

    public RectView(Context context, AttributeSet attrs) {
        this(context, attrs, 0);
    }

    public RectView(Context context, AttributeSet attrs, int defStyleAttr) {
        super(context, attrs, defStyleAttr);

        int left = DEFAULT_LEFT;
        int top = DEFAULT_TOP;
        int right = DEFAULT_RIGHT;
        int bottom = DEFAULT_BOTTOM;
        color = DEFAULT_COLOR;
        float textSize = DEFAULT_TEXT_SIZE;
        textPadding = DEFAULT_TEXT_PADDING;

        rect = new RectF(left, top, right, bottom);

        paint = new Paint();
        paint.setAntiAlias(true);
        paint.setStyle(Paint.Style.STROKE);
        paint.setStrokeWidth(8F); // Thicker stroke for tech look
        paint.setStrokeCap(Paint.Cap.ROUND); // Rounded ends for lines
        paint.setTextSize(textSize);
        paint.setColor(color);

        scanPaint = new Paint();
        scanPaint.setAntiAlias(true);
        scanPaint.setStyle(Paint.Style.FILL);
        
        paint.getTextBounds(SAMPLE_TEXT, 0, SAMPLE_TEXT.length(), textBoundsRect);
        textWidth = textBoundsRect.width();
        textHeight = textBoundsRect.height();
    }

    public void setX1(int v) {
        rect.left = v;
    }

    public void setX2(int v) {
        rect.right = v;
    }

    public void setY1(int v) {
        rect.top = v;
    }

    public void setY2(int v) {
        rect.bottom = v;
    }

    public void setColor(int v) {
        color = v;
        paint.setColor(color);
        // Update scan paint color too
        scanPaint.setColor(color);
        // Add a gradient to the scan line
        scanPaint.setShader(new LinearGradient(0, 0, 0, 100, 
            new int[]{Color.TRANSPARENT, color, Color.TRANSPARENT}, 
            null, Shader.TileMode.CLAMP));
    }

    public void setConfidence(float v) {
        confidence = v;
        if (confidence > 0 && !isScanning) {
            startScanAnimation();
        } else if (confidence <= 0 && isScanning) {
            stopScanAnimation();
        }
    }

    private void startScanAnimation() {
        if (scanAnimator == null) {
            scanAnimator = ValueAnimator.ofFloat(0f, 1f);
            scanAnimator.setDuration(1500);
            scanAnimator.setRepeatCount(ValueAnimator.INFINITE);
            scanAnimator.setRepeatMode(ValueAnimator.REVERSE);
            scanAnimator.setInterpolator(new LinearInterpolator());
            scanAnimator.addUpdateListener(animation -> {
                float fraction = (float) animation.getAnimatedValue();
                if (!rect.isEmpty()) {
                    scanLineY = rect.top + (rect.height() * fraction);
                    invalidate();
                }
            });
        }
        if (!scanAnimator.isRunning()) {
            scanAnimator.start();
            isScanning = true;
        }
    }

    private void stopScanAnimation() {
        if (scanAnimator != null && scanAnimator.isRunning()) {
            scanAnimator.cancel();
        }
        isScanning = false;
        invalidate();
    }

    @Override
    protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);

        if (rect.isEmpty() || confidence <= 0) return;

        paint.setStyle(Paint.Style.STROKE);
        paint.setColor(color);

        // Draw 4 corners (Tech style)
        float len = Math.min(rect.width(), rect.height()) / 4;
        if (len > cornerLength) len = cornerLength;

        // Top-Left
        canvas.drawLine(rect.left, rect.top, rect.left + len, rect.top, paint);
        canvas.drawLine(rect.left, rect.top, rect.left, rect.top + len, paint);

        // Top-Right
        canvas.drawLine(rect.right, rect.top, rect.right - len, rect.top, paint);
        canvas.drawLine(rect.right, rect.top, rect.right, rect.top + len, paint);

        // Bottom-Left
        canvas.drawLine(rect.left, rect.bottom, rect.left + len, rect.bottom, paint);
        canvas.drawLine(rect.left, rect.bottom, rect.left, rect.bottom - len, paint);

        // Bottom-Right
        canvas.drawLine(rect.right, rect.bottom, rect.right - len, rect.bottom, paint);
        canvas.drawLine(rect.right, rect.bottom, rect.right, rect.bottom - len, paint);

        // Draw Scan Line
        if (isScanning) {
            // Create a gradient shader for the scan line that matches the current color
            // We create it here to ensure it matches the rect width and current color
            LinearGradient shader = new LinearGradient(
                    rect.left, scanLineY, rect.right, scanLineY,
                    new int[]{Color.TRANSPARENT, color, Color.TRANSPARENT},
                    new float[]{0f, 0.5f, 1f},
                    Shader.TileMode.CLAMP);
            scanPaint.setShader(shader);
            scanPaint.setAlpha(128); // Semi-transparent
            
            // Draw a slightly thicker line/rect for the scan effect
            canvas.drawRect(rect.left, scanLineY - 2, rect.right, scanLineY + 2, scanPaint);
        }

        // Draw Text
        String text = decimalFormat.format(confidence);
        
        // Position text above the top-left corner
        textBackgroundRect.set((int)rect.left, (int)rect.top - textHeight - 2 * textPadding, (int)rect.left + textWidth + 2 * textPadding, (int)rect.top - 10);
        
        paint.setStyle(Paint.Style.FILL);
        canvas.drawRect(textBackgroundRect, paint);

        textBoundsRect.set((int)rect.left + textPadding, (int)rect.top - textPadding - textHeight - 10, (int)rect.left + textPadding + textWidth, (int)rect.top - textPadding - 10);
        
        paint.setColor(Color.BLACK); // Text color black on colored background
        paint.setStyle(Paint.Style.FILL);
        canvas.drawText(text, textBoundsRect.left, textBoundsRect.bottom, paint);
    }
    
    @Override
    protected void onDetachedFromWindow() {
        super.onDetachedFromWindow();
        stopScanAnimation();
    }
}
