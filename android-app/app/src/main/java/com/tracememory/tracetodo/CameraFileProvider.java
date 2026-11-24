package com.tracememory.tracetodo;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;

import java.io.File;
import java.io.FileNotFoundException;

/**
 * Zero-dependency ContentProvider serving temporary camera captures under getCacheDir()/camera_captures
 * so MediaStore.ACTION_IMAGE_CAPTURE works cleanly on Android 7.0 - 15 (targetSdk 34) without FileUriExposedException.
 */
public class CameraFileProvider extends ContentProvider {

    private static final String DIR_NAME = "camera_captures";

    public static File prepareCaptureFile(Context context) {
        File dir = new File(context.getCacheDir(), DIR_NAME);
        if (!dir.exists()) {
            //noinspection ResultOfMethodCallIgnored
            dir.mkdirs();
        }
        cleanOldCaptures(dir);
        return new File(dir, "capture_" + System.currentTimeMillis() + ".jpg");
    }

    public static Uri getUriForFile(Context context, File file) {
        return new Uri.Builder()
                .scheme("content")
                .authority(context.getPackageName() + ".fileprovider")
                .appendPath("camera")
                .appendPath(file.getName())
                .build();
    }

    private static void cleanOldCaptures(File dir) {
        try {
            File[] files = dir.listFiles();
            if (files == null) return;
            long cutoff = System.currentTimeMillis() - 3600_000L;
            for (int i = 0; i < files.length; i++) {
                if (files[i] != null && files[i].lastModified() < cutoff) {
                    //noinspection ResultOfMethodCallIgnored
                    files[i].delete();
                }
            }
        } catch (Throwable ignored) {
        }
    }

    private File resolveFile(Uri uri) throws FileNotFoundException {
        Context ctx = getContext();
        if (ctx == null || uri == null) {
            throw new FileNotFoundException("Invalid context or URI");
        }
        String last = uri.getLastPathSegment();
        if (last == null || last.trim().isEmpty()) {
            throw new FileNotFoundException("Missing file name");
        }
        String safeName = new File(last).getName();
        File dir = new File(ctx.getCacheDir(), DIR_NAME);
        return new File(dir, safeName);
    }

    @Override
    public boolean onCreate() {
        return true;
    }

    @Override
    public Cursor query(Uri uri, String[] projection, String selection, String[] selectionArgs, String sortOrder) {
        try {
            File file = resolveFile(uri);
            String[] cols = projection != null
                    ? projection
                    : new String[]{OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE};
            String[] finalCols = new String[cols.length];
            Object[] values = new Object[cols.length];
            int count = 0;
            for (int i = 0; i < cols.length; i++) {
                String col = cols[i];
                if (OpenableColumns.DISPLAY_NAME.equals(col)) {
                    finalCols[count] = OpenableColumns.DISPLAY_NAME;
                    values[count++] = file.getName();
                } else if (OpenableColumns.SIZE.equals(col)) {
                    finalCols[count] = OpenableColumns.SIZE;
                    values[count++] = Long.valueOf(file.exists() ? file.length() : 0L);
                }
            }
            String[] trimmedCols = new String[count];
            Object[] trimmedVals = new Object[count];
            System.arraycopy(finalCols, 0, trimmedCols, 0, count);
            System.arraycopy(values, 0, trimmedVals, 0, count);
            MatrixCursor cursor = new MatrixCursor(trimmedCols, 1);
            cursor.addRow(trimmedVals);
            return cursor;
        } catch (Throwable e) {
            return null;
        }
    }

    @Override
    public String getType(Uri uri) {
        return "image/jpeg";
    }

    @Override
    public Uri insert(Uri uri, ContentValues values) {
        throw new UnsupportedOperationException("No external inserts");
    }

    @Override
    public int delete(Uri uri, String selection, String[] selectionArgs) {
        try {
            File file = resolveFile(uri);
            return file.delete() ? 1 : 0;
        } catch (Throwable e) {
            return 0;
        }
    }

    @Override
    public int update(Uri uri, ContentValues values, String selection, String[] selectionArgs) {
        throw new UnsupportedOperationException("No external updates");
    }

    @Override
    public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        File file = resolveFile(uri);
        File parent = file.getParentFile();
        if (parent != null && !parent.exists()) {
            //noinspection ResultOfMethodCallIgnored
            parent.mkdirs();
        }
        int pfdMode;
        if ("r".equals(mode)) {
            pfdMode = ParcelFileDescriptor.MODE_READ_ONLY;
        } else if ("w".equals(mode) || "wt".equals(mode)) {
            pfdMode = ParcelFileDescriptor.MODE_WRITE_ONLY
                    | ParcelFileDescriptor.MODE_CREATE
                    | ParcelFileDescriptor.MODE_TRUNCATE;
        } else if ("wa".equals(mode)) {
            pfdMode = ParcelFileDescriptor.MODE_WRITE_ONLY
                    | ParcelFileDescriptor.MODE_CREATE
                    | ParcelFileDescriptor.MODE_APPEND;
        } else if ("rw".equals(mode)) {
            pfdMode = ParcelFileDescriptor.MODE_READ_WRITE
                    | ParcelFileDescriptor.MODE_CREATE;
        } else if ("rwt".equals(mode)) {
            pfdMode = ParcelFileDescriptor.MODE_READ_WRITE
                    | ParcelFileDescriptor.MODE_CREATE
                    | ParcelFileDescriptor.MODE_TRUNCATE;
        } else {
            throw new IllegalArgumentException("Invalid mode: " + mode);
        }
        return ParcelFileDescriptor.open(file, pfdMode);
    }
}
