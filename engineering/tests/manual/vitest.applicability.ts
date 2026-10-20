/** Coverage and transform-only mutations; never writes application sources. */
import { mergeConfig } from "vitest/config";
import base from "../../vitest.config";

const mutations = [
  [
    "!Number.isFinite(value) ||\n      (hardness ? value < 0 || value > 100 : value <= 0)",
    "false ||\n      (hardness ? value < 0 || value > 100 : value <= 0)",
  ],
  ['form.hardnessMaxInclusive === "inclusive"', "true"],
  ["limits.hardness_min_hrc > limits.hardness_max_hrc", "false"],
  [
    'else if (complete) throw new Error("请选择已核验的冷却与装夹方式。");',
    ";",
  ],
  [
    "if (!limits || !Array.isArray(limits.coolants) || !Array.isArray(limits.workholdings))",
    "if (false)",
  ],
];
const mutation = process.env.APPLICABILITY_MUTATION;
const selected =
  mutation === undefined ? undefined : mutations[Number(mutation)];
if (mutation !== undefined && !selected) throw new Error("Unknown mutation");
export default mergeConfig(base, {
  plugins: selected
    ? [
        {
          name: "applicability-mutation",
          enforce: "pre",
          transform(code, id) {
            if (
              !id
                .replaceAll("\\", "/")
                .endsWith("src/utils/processReviewForm.ts")
            )
              return;
            if (code.split(selected[0]).length !== 2)
              throw new Error("Missing or ambiguous mutation anchor");
            return code.replace(selected[0], selected[1]);
          },
        },
      ]
    : [],
  test: {
    pool: "forks",
    poolOptions: { forks: { singleFork: true } },
    coverage: {
      enabled: mutation === undefined,
      provider: "v8",
      include: [
        "src/utils/processReviewForm.ts",
        "src/api/processKnowledge.ts",
        "src/composables/useProcessReview.ts",
        "src/views/ProcessKnowledgeReview.vue",
      ],
      reportsDirectory:
        "../docs/paper_and_competition/I17-validation/frontend-coverage",
      reporter: ["text", "json-summary", "json"],
      thresholds: { lines: 80, branches: 80, functions: 80, statements: 80 },
    },
  },
});
