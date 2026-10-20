/** Initialize Elasticsearch's codec dependencies before Lucene's read-only check. */
public class VerifyEsIndex {
    public static void main(String[] args) throws Exception {
        org.elasticsearch.common.logging.LogConfigurator.configureESLogging();
        org.elasticsearch.common.logging.LogConfigurator.configureWithoutConfig(
            org.elasticsearch.common.settings.Settings.EMPTY);
        org.apache.lucene.index.CheckIndex.main(args);
    }
}
