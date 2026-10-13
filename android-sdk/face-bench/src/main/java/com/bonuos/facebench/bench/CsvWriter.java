package com.bonuos.facebench.bench;

import java.io.BufferedWriter;
import java.io.Closeable;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.OutputStreamWriter;
import java.nio.charset.StandardCharsets;
import java.util.Locale;

/** Minimal RFC 4180 writer. NaN becomes an empty cell so analysis never mistakes "not run" for zero. */
public final class CsvWriter implements Closeable {
    private final BufferedWriter out;
    private final int columns;

    public CsvWriter(File file, String... header) throws IOException {
        out = new BufferedWriter(new OutputStreamWriter(new FileOutputStream(file), StandardCharsets.UTF_8), 1 << 16);
        columns = header.length;
        row((Object[]) header);
    }

    public void row(Object... values) throws IOException {
        if (values.length != columns) throw new IllegalArgumentException("Expected " + columns + " columns, got " + values.length);
        StringBuilder line = new StringBuilder();
        for (int i = 0; i < values.length; i++) {
            if (i > 0) line.append(',');
            line.append(cell(values[i]));
        }
        out.write(line.toString());
        out.write("\r\n");
    }

    static String cell(Object value) {
        if (value == null) return "";
        if (value instanceof Double || value instanceof Float) {
            double number = ((Number) value).doubleValue();
            if (Double.isNaN(number) || Double.isInfinite(number)) return "";
            return String.format(Locale.ROOT, "%.6f", number);
        }
        String text = value.toString();
        if (text.indexOf(',') < 0 && text.indexOf('"') < 0 && text.indexOf('\n') < 0 && text.indexOf('\r') < 0) return text;
        return '"' + text.replace("\"", "\"\"") + '"';
    }

    public void flush() throws IOException { out.flush(); }

    @Override public void close() throws IOException { out.close(); }
}
