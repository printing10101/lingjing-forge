import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "@/api/processKnowledge";
import { useProcessReview } from "../useProcessReview";
import { processRecord } from "@/tests/processReviewFixture";
vi.mock("@/api/processKnowledge", () => ({
  listProcessRecords: vi.fn(),
  createProcessRecord: vi.fn(),
  reviewProcessRecord: vi.fn(),
}));
const action = {
  expected_revision: 1,
  decision: "rejected" as const,
  note: "Synthetic",
};
describe("useProcessReview", () => {
  beforeEach(() => vi.resetAllMocks());
  it("loads, saves locally confirmed versions, and creates pending records", async () => {
    const state = useProcessReview();
    vi.mocked(api.listProcessRecords).mockResolvedValue([processRecord()]);
    await state.load();
    expect(state.selected.value?.revision).toBe(1);
    vi.mocked(api.reviewProcessRecord).mockResolvedValue(
      processRecord({ revision: 2, review: { status: "rejected" } }),
    );
    expect(await state.save(action)).toBe(true);
    expect(state.selected.value?.revision).toBe(2);
    vi.mocked(api.createProcessRecord).mockResolvedValue(
      processRecord({ record_id: "new" }),
    );
    expect(await state.save(processRecord(), true)).toBe(true);
    expect(state.records.value).toHaveLength(2);
    expect(state.notice.value).toContain("待审核");
  });
  it("blocks double submissions and reloads while a save is pending", async () => {
    const state = useProcessReview();
    vi.mocked(api.listProcessRecords).mockResolvedValue([processRecord()]);
    await state.load();
    let complete!: (value: ReturnType<typeof processRecord>) => void;
    vi.mocked(api.reviewProcessRecord).mockImplementation(
      () =>
        new Promise((resolve) => {
          complete = resolve;
        }),
    );
    const pending = state.save(action);
    const blocked = state.save(action);
    expect(api.reviewProcessRecord).toHaveBeenCalledTimes(1);
    expect(await blocked).toBe(false);
    await state.load();
    expect(api.listProcessRecords).toHaveBeenCalledTimes(1);
    complete(processRecord({ revision: 2 }));
    await pending;
  });
  it.each([401, 403, 409, 503, 500])(
    "fails honestly on HTTP %i",
    async (status) => {
      const state = useProcessReview();
      vi.mocked(api.listProcessRecords).mockResolvedValue([processRecord()]);
      await state.load();
      vi.mocked(api.reviewProcessRecord).mockRejectedValue({
        response: { status },
      });
      expect(await state.save(action)).toBe(false);
      expect(state.notice.value).toBe("");
      expect(state.error.value).not.toBe("");
      expect(state.saving.value).toBe(false);
      if (status === 409) {
        expect(state.stale.value).toBe(true);
        expect(await state.save(action)).toBe(false);
      expect(api.reviewProcessRecord).toHaveBeenCalledTimes(1);
        await state.load();
        expect(state.stale.value).toBe(false);
      }
      if (status === 401 || status === 403)
        expect(state.records.value).toEqual([]);
    },
  );
  it("does not submit without a selected record or while loading", async () => {
    const state = useProcessReview();
    expect(await state.save(action)).toBe(false);
    state.loading.value = true;
    expect(await state.save(processRecord(), true)).toBe(false);
  });
  it("ignores older loads and clears obsolete data when refresh fails", async () => {
    const state = useProcessReview();
    let first!: (value: ReturnType<typeof processRecord>[]) => void;
    vi.mocked(api.listProcessRecords)
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            first = resolve;
          }),
      )
      .mockResolvedValueOnce([processRecord({ record_id: "latest" })]);
    const old = state.load();
    await state.load();
    first([processRecord({ record_id: "old" })]);
    await old;
    expect(state.selectedId.value).toBe("latest");
    vi.mocked(api.listProcessRecords).mockRejectedValue(new Error("Offline"));
    await state.load();
    expect(state.records.value).toEqual([]);
  });
  it("ignores an obsolete request failure", async () => {
    const state = useProcessReview();
    let fail!: (value: unknown) => void;
    vi.mocked(api.listProcessRecords)
      .mockImplementationOnce(
        () =>
          new Promise((_resolve, reject) => {
            fail = reject;
          }),
      )
      .mockResolvedValueOnce([]);
    const old = state.load();
    await state.load();
    fail(new Error("Old failure"));
    await old;
    expect(state.error.value).toBe("");
  });
});
