import { describe, expect, it } from "vitest";
import {
  limitsFromForm,
  newReviewForm,
  approvalEvidence,
  describeDrillingLimits,
} from "../processReviewForm";
import { processRecord } from "@/tests/processReviewFixture";

function filled() {
  return Object.assign(newReviewForm(), {
    hardnessMin: "0",
    hardnessMax: "24",
    hardnessMaxInclusive: "exclusive",
    diameterMin: "8",
    diameterMax: "8",
    depthRatio: "4",
    coolants: ["flood"],
    workholdings: ["vise"],
  });
}

describe("drilling applicability form", () => {
  it.each([
    undefined,
    null,
    {},
    { coolants: null },
    { coolants: [], workholdings: [], hardness_min_hrc: NaN },
  ])("does not crash on incomplete legacy limits %j", (limits) => {
    expect(() => describeDrillingLimits(limits as never)).not.toThrow();
    expect(describeDrillingLimits(limits as never)).toContain("重新审核");
  });
  it("retains partial draft without inventing bounds or modes", () => {
    expect(limitsFromForm(newReviewForm())).toEqual({ schema_version: 1 });
    const partial = newReviewForm();
    partial.hardnessMax = "24";
    expect(limitsFromForm(partial)).toEqual({
      schema_version: 1,
      hardness_max_hrc: 24,
    });
    expect(() => limitsFromForm(partial, true)).toThrow("完整填写");
  });
  it("preserves inclusive and exclusive hardness boundaries and reloads evidence", () => {
    const form = filled();
    const constraints = limitsFromForm(form, true);
    expect(constraints.hardness_max_inclusive).toBe(false);
    form.hardnessMaxInclusive = "inclusive";
    expect(limitsFromForm(form, true).hardness_max_inclusive).toBe(true);
    const loaded = newReviewForm(
      processRecord({ candidate_context: { constraints } }),
    );
    expect(loaded.hardnessMaxInclusive).toBe("exclusive");
    expect(loaded.diameterMin).toBe("8");
    expect(loaded.coolants).toEqual(["flood"]);
    loaded.coolants.push("dry");
    expect(constraints.coolants).toEqual(["flood"]);
  });
  it.each([
    { hardnessMin: "-1" },
    { hardnessMax: "101" },
    { hardnessMin: "25" },
    { diameterMin: "9" },
    { diameterMax: "0" },
    { depthRatio: "Infinity" },
    { depthRatio: "NaN" },
    { depthRatio: "-1" },
    { coolants: ["unknown"] },
    { workholdings: ["unknown"] },
    { hardnessMaxInclusive: "wrong" },
  ])("rejects invalid range %j", (updates) => {
    expect(() =>
      limitsFromForm(Object.assign(filled(), updates), true),
    ).toThrow();
  });
  it.each([
    { coolants: [] },
    { workholdings: [] },
    { hardnessMaxInclusive: "" },
  ])("requires explicit mode and boundary %j", (updates) => {
    expect(() =>
      limitsFromForm(Object.assign(filled(), updates), true),
    ).toThrow();
  });
  it("rejects an empty exclusive hardness interval", () => {
    expect(() =>
      limitsFromForm(Object.assign(filled(), { hardnessMin: "24" }), true),
    ).toThrow("范围为空");
  });
  it("displays exact boundary semantics and readable modes", () => {
    const limits = limitsFromForm(
      filled(),
      true,
    ) as import("@/api/processKnowledge").DrillingLimits;
    expect(describeDrillingLimits(limits)).toContain("＜ 24 HRC");
    expect(describeDrillingLimits(limits)).toContain("浇注冷却");
    expect(describeDrillingLimits(limits)).toContain("台钳");
    limits.hardness_max_inclusive = true;
    limits.coolants = ["dry", "mist", "through_tool", "custom"];
    limits.workholdings = ["chuck", "fixture", "collet", "custom"];
    expect(describeDrillingLimits(limits)).toContain("≤ 24 HRC");
    expect(describeDrillingLimits(limits)).toContain("custom");
  });
  it("does not silently approve a legacy form without bounds", () => {
    const form = Object.assign(newReviewForm(processRecord()), {
      sourceUrl: "https://example.org/synthetic.pdf",
      documentHash: "a".repeat(64),
      page: "1",
      applicability: "Synthetic only",
      verificationMethod: "Synthetic",
      resolvedConfirmed: true,
      approvalConfirmed: true,
      note: "Synthetic",
    });
    form.parameters[0].unit = "rpm";
    expect(() => approvalEvidence(form, processRecord())).toThrow("适用范围");
  });
});
