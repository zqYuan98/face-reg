package com.bonuos.face.handler;

import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

import java.util.HashMap;
import java.util.Map;

@RestControllerAdvice
public class GlobalExceptionHandler {

    @ExceptionHandler(RuntimeException.class)
    public Map<String, Object> handleRuntimeException(RuntimeException e) {
        Map<String, Object> result = new HashMap<>();
        result.put("code", 500);

        // 提取核心错误信息，去除 "java.lang.RuntimeException: " 前缀
        String msg = e.getMessage();
        if (msg != null && msg.contains(": ")) {
            // 如果是包装过的异常，尝试提取更有意义的部分，但这里简单返回 message 即可，
            // 因为我们在 Service 层已经精心构造了 message
        }

        result.put("msg", msg != null ? msg : "系统内部错误");
        return result;
    }

    @ExceptionHandler(Exception.class)
    public Map<String, Object> handleException(Exception e) {
        Map<String, Object> result = new HashMap<>();
        result.put("code", 500);
        result.put("msg", "系统未知错误: " + e.getMessage());
        return result;
    }
}
