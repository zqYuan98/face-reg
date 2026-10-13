package com.mv.engine


abstract class Component {

    init {
        loadLibrary()
    }

    abstract fun createInstance(): Long

    abstract fun destroy()

    companion object {
        var libraryFound: Boolean = false

        const val tag = "Component"

        @JvmStatic
        fun loadLibrary() {
            if (libraryFound) return
            try {
                System.loadLibrary("engine")
                libraryFound = true
            } catch (e: UnsatisfiedLinkError) {
                e.printStackTrace()
            }
        }
    }
}
