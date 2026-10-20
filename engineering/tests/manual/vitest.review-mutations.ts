/** Transform-only fault injection. This config never changes source files. */
import { mergeConfig } from "vitest/config";
import base from "../../vitest.config";

const mutations = [
  ["src/utils/processReviewForm.ts", "!form.resolvedConfirmed ||", "false ||"],
  ["src/utils/processReviewForm.ts", 'url.protocol !== "https:"', "false"],
  ["src/utils/processReviewForm.ts", "Number(row.value) !== record.parameters[row.name]", "false"],
  ["src/composables/useProcessReview.ts", "stale.value ||", "false ||"],
  ["src/composables/useProcessReview.ts", "saving.value ||", "false ||"],
  ["src/stores/auth.ts", "['admin', 'engineer', 'operator', 'viewer', 'guest']", "['admin', 'operator', 'viewer', 'guest']"],
];
const selected = mutations[Number(process.env.REVIEW_MUTATION)];
if (!selected) throw new Error("Explicit REVIEW_MUTATION is required");
export default mergeConfig(base, {
  plugins: [{ name: "synthetic-review-mutation", enforce: "pre", transform(code, id) {
    if (!id.replaceAll("\\", "/").endsWith(selected[0])) return;
    if (code.split(selected[1]).length !== 2) throw new Error("Mutation anchor is missing or ambiguous");
    return code.replace(selected[1], selected[2]);
  } }],
  test: { coverage: { enabled: false } },
});
