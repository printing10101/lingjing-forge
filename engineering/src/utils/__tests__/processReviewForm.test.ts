import { describe, it, expect } from "vitest";
import {
  approvalEvidence,
  formContent,
  newReviewForm,
} from "../processReviewForm";
import { processRecord } from "@/tests/processReviewFixture";

function completeForm() {
  const form = newReviewForm(processRecord());
  Object.assign(form, {
    sourceUrl: "https://example.org/synthetic.pdf",
    documentHash: "a".repeat(64),
    page: "2",
    applicability: "Synthetic test only",
    verificationMethod: "Synthetic test, no physical approval",
    resolvedConfirmed: true,
    approvalConfirmed: true,
    note: "Fixture",
    hardnessMin: "0",
    hardnessMax: "24",
    hardnessMaxInclusive: "exclusive",
    diameterMin: "8",
    diameterMax: "8",
    depthRatio: "4",
    coolants: ["flood"],
    workholdings: ["vise"],
  });
  form.parameters[0].unit = "rpm";
  return form;
}
describe("process review form", () => {
  it("starts with no implied approval and preserves submitted context separately", () => {
    expect(newReviewForm().approvalConfirmed).toBe(false);
    const form = completeForm();
    form.unresolved = "material mapping unknown\n\nspeed unit unknown";
    const content = formContent(form, processRecord());
    expect(content.chunk_ids).toEqual(["synthetic:page1"]);
    expect(content.candidate_context?.unresolved).toEqual([
      "material mapping unknown",
      "speed unit unknown",
    ]);
    expect(content.candidate_context?.parameter_units).toEqual({
      spindle_rpm: "rpm",
    });
    expect(content).not.toHaveProperty("review");
    expect(
      newReviewForm(
        processRecord({ candidate_context: content.candidate_context }),
      ).sourceUrl,
    ).toContain("synthetic.pdf");
    const approved = processRecord({
      review: {
        status: "approved",
        evidence: approvalEvidence(completeForm(), processRecord()),
      },
    });
    expect(newReviewForm(approved).parameters[0].unit).toBe("rpm");
    expect(newReviewForm(approved).resolvedConfirmed).toBe(false);
    expect(formContent(completeForm()).confidence).toBe(0.5);
  });
  it.each(["", "0", "-1", "NaN", "Infinity"])(
    "rejects invalid parameter %s",
    (value) => {
      const form = completeForm();
      form.parameters[0].value = value;
      expect(() => formContent(form)).toThrow("有限正数");
    },
  );
  it("rejects duplicate names, empty fields and invalid pending pages", () => {
    const form = completeForm();
    form.parameters.push({ ...form.parameters[0] });
    expect(() => formContent(form)).toThrow("重复");
    form.parameters = [];
    expect(() => formContent(form)).toThrow("至少");
    form.feature = "";
    expect(() => formContent(form)).toThrow("特征");
    const invalid = completeForm();
    invalid.page = "1.5";
    expect(() => formContent(invalid)).toThrow("页码");
    invalid.page = "";
    expect(formContent(invalid).candidate_context?.page).toBeUndefined();
  });
  it.each([
    { note: "" },
    { resolvedConfirmed: false },
    { approvalConfirmed: false },
    { unresolved: "unknown grade" },
    { sourceUrl: "not-a-url" },
    { sourceUrl: "http://example.org" },
    { sourceUrl: "https://user:secret@example.org" },
    { documentHash: "wrong" },
    { page: "0" },
    { page: "2.1" },
    { applicability: "" },
    { verificationMethod: "" },
  ])("rejects incomplete approval %j", (updates) => {
    const form = Object.assign(completeForm(), updates);
    expect(() => approvalEvidence(form, processRecord())).toThrow();
  });
  it("requires exact units and a specific material", () => {
    const form = completeForm();
    form.parameters[0].unit = "";
    expect(() => approvalEvidence(form, processRecord())).toThrow("单位");
    form.parameters[0].unit = "rpm";
    form.parameters[0].name = "other";
    expect(() => approvalEvidence(form, processRecord())).toThrow("变更");
    expect(() =>
      approvalEvidence(completeForm(), processRecord({ material: "general" })),
    ).toThrow("明确材料");
  });
  it("constructs approval evidence for the current record, without self-reported actor", () => {
    const evidence = approvalEvidence(completeForm(), processRecord());
    expect(evidence.material).toBe("fixture_steel");
    expect(evidence.unresolved).toEqual([]);
    expect(evidence).not.toHaveProperty("actor");
  });
  it("never approves a displayed but unsaved value or changed material", () => {
    const changed = completeForm();
    changed.parameters[0].value = "900";
    expect(() => approvalEvidence(changed, processRecord())).toThrow(
      "先保存修订",
    );
    const material = completeForm();
    material.material = "other";
    expect(() => approvalEvidence(material, processRecord())).toThrow(
      "先保存修订",
    );
  });
});
