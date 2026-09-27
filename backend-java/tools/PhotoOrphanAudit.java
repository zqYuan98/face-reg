import java.nio.file.*;
import java.sql.*;
import java.time.*;
import java.util.Properties;

/** Read-only inventory. This program never deletes or changes photos or database rows. */
public class PhotoOrphanAudit {
    public static void main(String[] args) throws Exception {
        String url=required("FACE_DB_URL"), directory=required("FACE_PHOTO_AUDIT_DIR");
        Path root=Path.of(directory).toRealPath();
        if(!Files.isDirectory(root))throw new IllegalArgumentException("Photo directory required");
        Properties properties=new Properties();properties.setProperty("user",required("FACE_DB_USER"));
        properties.setProperty("password",required("FACE_DB_PASSWORD"));
        properties.setProperty("connectTimeout","2000");properties.setProperty("socketTimeout","10000");
        long grace=Duration.ofDays(8).toMillis();
        try(Connection connection=DriverManager.getConnection(url,properties)) {
            connection.setReadOnly(true);
            try(PreparedStatement current=connection.prepareStatement("SELECT 1 FROM sys_user WHERE photo_url=? LIMIT 1");
                PreparedStatement history=connection.prepareStatement("SELECT 1 FROM user_change_log WHERE photo_url=? OR old_photo_url=? LIMIT 1");
                DirectoryStream<Path> files=Files.newDirectoryStream(root)) {
                current.setQueryTimeout(5);history.setQueryTimeout(5);
                System.out.println("filename,classification");
                for(Path file:files) {
                    if(Files.isSymbolicLink(file)||!Files.isRegularFile(file))continue;
                    String name=file.getFileName().toString(); String classification="within_grace_period";
                    if(name.length()>255 || name.startsWith(".") || !name.matches("(?i).+\\.(jpg|jpeg|png|webp|bmp|gif)")
                            || name.chars().anyMatch(c -> Character.isISOControl(c) || "%/\\:<>\"|?*#".indexOf(c)>=0)) {
                        classification="unsupported_legacy_name_reupload_or_controlled_migration_required";
                    } else if(System.currentTimeMillis()-Files.getLastModifiedTime(file).toMillis()>=grace) {
                        current.setString(1,name);history.setString(1,name);history.setString(2,name);
                        boolean referenced;
                        try(ResultSet result=current.executeQuery()){referenced=result.next();}
                        if(!referenced)try(ResultSet result=history.executeQuery()){referenced=result.next();}
                        classification=referenced?"referenced":"orphan_candidate_review_required";
                    }
                    System.out.println('"'+name.replace("\"","\"\"")+'"'+','+classification);
                }
            }
        }
    }
    private static String required(String key){String value=System.getenv(key);if(value==null||value.isBlank())throw new IllegalArgumentException(key+" required");return value;}
}
