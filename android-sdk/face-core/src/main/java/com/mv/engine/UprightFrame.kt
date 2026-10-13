package com.mv.engine

import androidx.annotation.Keep
import java.io.Closeable

/**
 * One camera frame converted to upright BGR in native memory. Fill it once per frame, then pass it
 * to [FaceDetector.detect] and [Live.detect] so detection and both liveness models share a single
 * colour conversion and rotation. Not thread safe; reuse one instance per analysis thread.
 */
@Keep
class UprightFrame : Closeable {
    private var handle: Long

    init {
        Component.loadLibrary()
        handle = nativeAllocate()
    }

    var width = 0
        private set
    var height = 0
        private set

    /** [rotationDegrees] is the clockwise rotation that makes the sensor frame upright. */
    fun setNv21(nv21: ByteArray, sensorWidth: Int, sensorHeight: Int, rotationDegrees: Int) {
        check(handle != 0L) { "UprightFrame closed" }
        val ret = nativeSetNv21(handle, nv21, sensorWidth, sensorHeight, rotationDegrees)
        require(ret == 0) { "Invalid NV21 frame ($ret)" }
        val swap = ((rotationDegrees % 360) + 360) % 180 != 0
        width = if (swap) sensorHeight else sensorWidth
        height = if (swap) sensorWidth else sensorHeight
    }

    internal fun nativeHandle(): Long {
        check(handle != 0L && width > 0) { "UprightFrame closed or empty" }
        return handle
    }

    override fun close() {
        if (handle != 0L) {
            nativeRelease(handle)
            handle = 0L
        }
    }

    private companion object {
        @JvmStatic private external fun nativeAllocate(): Long
        @JvmStatic private external fun nativeRelease(handle: Long)
        @JvmStatic private external fun nativeSetNv21(handle: Long, nv21: ByteArray, width: Int, height: Int, rotation: Int): Int
    }
}
