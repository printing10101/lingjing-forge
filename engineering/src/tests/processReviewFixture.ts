import type { ProcessRecord } from "@/api/processKnowledge";
export function processRecord(
  overrides: Partial<ProcessRecord> = {},
): ProcessRecord {
  return {
    record_id: "synthetic-record",
    revision: 1,
    feature: "hole",
    process: "drill",
    tool: "fixture_d8",
    material: "fixture_steel",
    source: "synthetic_test",
    confidence: 0.5,
    parameters: { spindle_rpm: 1200 },
    chunk_ids: ["synthetic:page1"],
    tags: ["synthetic"],
    review: { status: "pending" },
    review_history: [],
    ...overrides,
  };
}
