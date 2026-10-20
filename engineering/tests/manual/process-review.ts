/** Synthetic local browser fixture. Not included in production build entries. */
import { createApp } from "vue";
import { createPinia } from "pinia";
import ProcessKnowledgeReview from "../../src/views/ProcessKnowledgeReview.vue";
import { useAuthStore } from "../../src/stores/auth";
import { setBackendPort, setHttpReady } from "../../src/utils/http";

setBackendPort(18765);
setHttpReady();
const pinia = createPinia();
const app = createApp(ProcessKnowledgeReview).use(pinia);
const auth = useAuthStore(pinia);
auth.setToken("synthetic-browser-fixture-only");
auth.setUser({
  id: "synthetic",
  username: "synthetic_review_engineer",
  role: "engineer",
  permissions: [],
});
app.mount("#app");
