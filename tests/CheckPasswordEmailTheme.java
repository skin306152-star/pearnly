/* Run with the production FreeMarker JAR; no SMTP, account or token operations. */
import freemarker.template.*;
import freemarker.cache.*;
import java.nio.file.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.text.MessageFormat;
import java.util.*;

class CheckPasswordEmailTheme {
    public static void main(String[] args) throws Exception {
        Path theme = Path.of(args[0]);
        Configuration cfg = new Configuration(Configuration.VERSION_2_3_32);
        cfg.setDefaultEncoding("UTF-8");
        cfg.setTemplateExceptionHandler(TemplateExceptionHandler.RETHROW_HANDLER);
        cfg.setTemplateLoader(new MultiTemplateLoader(new TemplateLoader[]{
            new FileTemplateLoader(theme.toFile()), new FileTemplateLoader(Path.of(args[1]).toFile())}));
        int count = 0;
        for (String language : List.of("th", "zh_CN", "en", "ja")) {
            Properties messages = new Properties();
            try (Reader r = Files.newBufferedReader(theme.resolve("messages/messages_" + language + ".properties"))) {messages.load(r);}
            messages.setProperty("requiredAction.VERIFY_EMAIL", "VERIFY_EMAIL");
            messages.setProperty("executeActionsBodyHtml", "GENERIC {3} {0}");
            messages.setProperty("executeActionsBody", "GENERIC {3} {0}");
            Map<String,Object> data = new HashMap<>();
            data.put("locale", Map.of("language", language)); data.put("ltr", true);
            data.put("user", Map.of("email", "a<danger>&@example.com", "username", "demo"));
            data.put("url", Map.of("resourcesUrl", "https://auth.pearnly.com/resources/test/email/pearnly"));
            data.put("link", "https://auth.pearnly.com/reset?token=sample&client=demo");
            data.put("linkExpiration", 17); data.put("realmName", "Pearnly");
            data.put("linkExpirationFormatter", (TemplateMethodModelEx) a -> a.get(0).toString());
            data.put("kcSanitize", (TemplateMethodModelEx) a -> a.get(0).toString());
            data.put("msg", (TemplateMethodModelEx) a -> {
                String key=a.get(0).toString();
                if (!messages.containsKey(key)) throw new TemplateModelException("Missing translation: " + key);
                Object[] vals=a.subList(1,a.size()).stream().map(Object::toString).toArray();
                return new MessageFormat(messages.getProperty(key), Locale.forLanguageTag(language.replace('_','-'))).format(vals);
            });
            for (String format: List.of("html", "text")) {
                for (String file: List.of("password-reset", "executeActions")) {
                    data.put("requiredActions", List.of("UPDATE_PASSWORD"));
                    StringWriter out = new StringWriter();
                    cfg.getTemplate(format+"/"+file+".ftl").process(data,out);
                    String body=out.toString();
                    if (!body.contains(messages.getProperty("mailResetTitle")) || !body.contains("17") || !body.contains("hello@pearnly.com") || body.contains("{{")) throw new AssertionError("Content " + language);
                    if (format.equals("html") && (!body.contains("a&lt;danger&gt;&amp;@example.com") || !body.contains("width=\"190\" height=\"54\"") || !body.contains("/email/pearnly/img/logo.png") || !body.contains("sample&amp;client"))) throw new AssertionError("HTML " + language);
                    count++;
                }
                data.put("requiredActions", List.of("VERIFY_EMAIL"));
                StringWriter generic = new StringWriter();
                cfg.getTemplate(format+"/executeActions.ftl").process(data,generic);
                if (!generic.toString().contains("GENERIC VERIFY_EMAIL") || generic.toString().contains(messages.getProperty("mailResetTitle"))) throw new AssertionError("Generic actions changed");
                count++;
            }
            data.remove("url");
            cfg.getTemplate("html/password-reset.ftl").process(data, new StringWriter()); count++;
        }
        System.out.println("PASS: " + count + " native FreeMarker render cases; no mail sent");
    }
}
