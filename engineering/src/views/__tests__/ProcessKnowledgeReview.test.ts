import { beforeEach, describe, expect, it, vi } from "vitest";
import { mount, flushPromises, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import ProcessKnowledgeReview from "../ProcessKnowledgeReview.vue";
import { useAuthStore } from "@/stores/auth";
import * as api from "@/api/processKnowledge";
import { processRecord } from "@/tests/processReviewFixture";
vi.mock("@/api/processKnowledge", () => ({
  listProcessRecords: vi.fn(),
  createProcessRecord: vi.fn(),
  reviewProcessRecord: vi.fn(),
}));

async function render(role: "engineer" | "operator" | "guest" = "engineer") {
  const pinia = createPinia();
  setActivePinia(pinia);
  const auth = useAuthStore();
  auth.setToken("synthetic-test-only");
  auth.setUser({
    id: "fixture",
    username: "fixture",
    role,
    permissions: [],
    is_guest: role === "guest",
  });
  const wrapper = mount(ProcessKnowledgeReview, {
    global: { plugins: [pinia] },
  });
  await flushPromises();
  return wrapper;
}
async function fill(
  wrapper: VueWrapper,
  field: string,
  value: string | boolean,
) {
  await wrapper.get(`[data-testid="${field}"]`).setValue(value);
}
async function submit(wrapper: VueWrapper) {
  await wrapper.get("form").trigger("submit");
  await flushPromises();
}
describe("ProcessKnowledgeReview", () => {
  it("shows boundary semantics, modes and ranges in current evidence and history", async () => {
    const record = processRecord();
    const evidence = {
      source_url: "https://example.org/synthetic.pdf",
      document_sha256: "a".repeat(64),
      page: 1,
      material: record.material,
      tool: record.tool,
      parameter_units: { spindle_rpm: "rpm" },
      applicability: "Synthetic",
      verification_method: "Synthetic",
      unresolved: [],
      constraints: {
        schema_version: 1 as const,
        hardness_min_hrc: 0,
        hardness_max_hrc: 24,
        hardness_max_inclusive: false,
        diameter_min_mm: 8,
        diameter_max_mm: 8,
        max_depth_to_diameter: 4,
        coolants: ["flood"],
        workholdings: ["vise"],
      },
    };
    const stamp = {
      status: "approved" as const,
      evidence,
      evidence_digest: "b".repeat(64),
    };
    const before = processRecord({ review: stamp });
    vi.mocked(api.listProcessRecords).mockResolvedValue([
      processRecord({
        revision: 2,
        review: stamp,
        review_history: [{ before, after_review: stamp }],
      }),
    ]);
    const wrapper = await render();
    expect(wrapper.text()).toContain("＜ 24 HRC");
    expect(wrapper.text()).toContain("浇注冷却");
    expect(wrapper.text()).toContain("台钳");
    expect(wrapper.text()).toContain("变更前审核范围");
    expect(wrapper.text()).toContain("当次审核范围");
    expect(wrapper.text()).not.toContain("此旧记录缺少");
    wrapper.unmount();
  });
  beforeEach(() => {
    vi.resetAllMocks();
    sessionStorage.clear();
    vi.mocked(api.listProcessRecords).mockResolvedValue([processRecord()]);
  });
  it.each(["operator", "guest"] as const)(
    "does not read or mutate review records for %s",
    async (role) => {
      const wrapper = await render(role);
      expect(wrapper.text()).toContain("工程师或管理员");
      expect(api.listProcessRecords).not.toHaveBeenCalled();
      expect(wrapper.find("form").exists()).toBe(false);
      wrapper.unmount();
    },
  );
  it("shows empty and filtered states and saves new candidates with pending provenance", async () => {
    vi.mocked(api.listProcessRecords).mockResolvedValue([]);
    const wrapper = await render();
    expect(wrapper.text()).toContain("尚无工艺知识记录");
    await wrapper
      .findAll("button")
      .find((button) => button.text() === "新增待审资料")!
      .trigger("click");
    await submit(wrapper);
    expect(wrapper.text()).toContain("本次说明");
    for (const [name, value] of Object.entries({
      feature: "hole",
      process: "drill",
      tool: "fixture_d8",
      material: "fixture_steel",
      source: "synthetic_test",
      "parameter-name-0": "spindle_rpm",
      "parameter-value-0": "1200",
      "parameter-unit-0": "rpm",
      "hardness-min": "0",
      "hardness-max": "24",
      "hardness-max-inclusive": "exclusive",
      "diameter-min": "8",
      "diameter-max": "8",
      "depth-ratio": "4",
      "source-url": "https://example.org/synthetic.pdf",
      unresolved: "grade mapping unknown",
      note: "Synthetic pending candidate",
    }))
      await fill(wrapper, name, value);
    vi.mocked(api.createProcessRecord).mockResolvedValue(processRecord());
    await submit(wrapper);
    expect(api.createProcessRecord).toHaveBeenCalledWith(
      expect.objectContaining({
        candidate_context: expect.objectContaining({
          unresolved: ["grade mapping unknown"],
        }),
      }),
    );
    expect(wrapper.text()).toContain("已保存为待审核");
    await wrapper
      .find('input[placeholder="材料、刀具、特征或来源"]')
      .setValue("missing");
    expect(wrapper.text()).toContain("没有符合条件");
    wrapper.unmount();
  });
  it("blocks incomplete approval, submits confirmed evidence and shows history", async () => {
    const wrapper = await render();
    await fill(wrapper, "note", "Synthetic review");
    await submit(wrapper);
    expect(api.reviewProcessRecord).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain("尚未确认");
    for (const [name, value] of Object.entries({
      "source-url": "https://example.org/synthetic.pdf",
      "document-hash": "a".repeat(64),
      page: "2",
      applicability: "Synthetic only",
      "verification-method": "Synthetic check",
      "parameter-unit-0": "rpm",
    }))
      await fill(wrapper, name, value);
    await fill(wrapper, "resolved-confirmed", true);
    await wrapper.get('[data-testid="coolants"]').setValue(["flood"]);
    await wrapper.get('[data-testid="workholdings"]').setValue(["vise"]);
    await fill(wrapper, "approval-confirmed", true);
    await submit(wrapper);
    expect(api.reviewProcessRecord).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain("适用范围");
    for (const [name, value] of Object.entries({
      "hardness-min": "0",
      "hardness-max": "24",
      "hardness-max-inclusive": "exclusive",
      "diameter-min": "8",
      "diameter-max": "8",
      "depth-ratio": "4",
    }))
      await fill(wrapper, name, value);
    const saved = processRecord({
      revision: 2,
      review: {
        status: "approved",
        actor: "fixture_engineer",
        at: "2026-10-16",
        note: "Synthetic",
      },
      review_history: [
        {
          before: processRecord(),
          after_review: {
            status: "approved",
            actor: "fixture_engineer",
            note: "Synthetic",
          },
        },
      ],
    });
    vi.mocked(api.reviewProcessRecord).mockResolvedValue(saved);
    await submit(wrapper);
    expect(api.reviewProcessRecord).toHaveBeenCalledWith(
      "synthetic-record",
      expect.objectContaining({
        expected_revision: 1,
        decision: "approved",
        evidence: expect.objectContaining({ page: 2, unresolved: [] }),
      }),
    );
    expect(wrapper.text()).toContain("版本 2");
    expect(wrapper.text()).toContain("fixture_engineer");
    expect(wrapper.get('[data-testid="decision"]').element).toHaveProperty(
      "value",
      "revoked",
    );
    wrapper.unmount();
  });
  it("revises parameters without retaining approval and can add/remove parameter rows", async () => {
    vi.mocked(api.listProcessRecords).mockResolvedValue([
      processRecord({ review: { status: "rejected" } }),
    ]);
    const wrapper = await render();
    await wrapper
      .findAll("button")
      .find((button) => button.text() === "添加参数")!
      .trigger("click");
    expect(wrapper.find('[data-testid="parameter-name-1"]').exists()).toBe(
      true,
    );
    await wrapper
      .findAll("button")
      .filter((button) => button.text() === "删除参数")[1]
      .trigger("click");
    await fill(wrapper, "parameter-value-0", "900");
    await fill(wrapper, "note", "Correct synthetic value");
    vi.mocked(api.reviewProcessRecord).mockResolvedValue(
      processRecord({ revision: 2, parameters: { spindle_rpm: 900 } }),
    );
    await submit(wrapper);
    expect(api.reviewProcessRecord).toHaveBeenCalledWith(
      "synthetic-record",
      expect.objectContaining({
        decision: "revise",
        replacement: expect.objectContaining({
          parameters: { spindle_rpm: 900 },
        }),
      }),
    );
    expect(wrapper.text()).toContain("待审核");
    wrapper.unmount();
  });
  it("requires refresh after conflict and keeps the displayed version unchanged on failure", async () => {
    const wrapper = await render();
    await fill(wrapper, "decision", "rejected");
    await fill(wrapper, "note", "Synthetic rejection");
    vi.mocked(api.reviewProcessRecord).mockRejectedValue({
      response: { status: 409 },
    });
    await submit(wrapper);
    expect(wrapper.text()).toContain("记录已被更新");
    expect(
      wrapper.get('[data-testid="submit"]').attributes("disabled"),
    ).toBeDefined();
    vi.mocked(api.listProcessRecords).mockResolvedValue([
      processRecord({ revision: 3 }),
    ]);
    await wrapper
      .findAll("button")
      .find((button) => button.text() === "重新加载")!
      .trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("版本 3");
    expect(
      wrapper.get('[data-testid="submit"]').attributes("disabled"),
    ).toBeUndefined();
    wrapper.unmount();
  });
  it("renders source evidence and excludes unsafe source links", async () => {
    const evidence = {
      source_url: "https://example.org/synthetic.pdf",
      document_sha256: "a".repeat(64),
      page: 1,
      material: "fixture_steel",
      tool: "fixture_d8",
      parameter_units: { spindle_rpm: "rpm" },
      applicability: "Synthetic",
      verification_method: "Synthetic",
      unresolved: [],
    };
    vi.mocked(api.listProcessRecords).mockResolvedValue([
      processRecord({
        review: { status: "approved", actor: "fixture_engineer", evidence },
      }),
    ]);
    const wrapper = await render();
    expect(wrapper.get("a").attributes("rel")).toBe("noopener noreferrer");
    vi.mocked(api.listProcessRecords).mockResolvedValue([
      processRecord({
        review: {
          status: "approved",
          actor: "fixture",
          evidence: { ...evidence, source_url: "javascript:alert(1)" },
        },
      }),
    ]);
    await wrapper
      .findAll("button")
      .find((button) => button.text() === "重新加载")!
      .trigger("click");
    await flushPromises();
    expect(wrapper.find("a").exists()).toBe(false);
    wrapper.unmount();
  });
});
