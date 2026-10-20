import { describe, it, expect, vi } from "vitest";
import http from "@/utils/http";
import {
  createProcessRecord,
  listProcessRecords,
  reviewProcessRecord,
} from "../processKnowledge";
import { processRecord } from "@/tests/processReviewFixture";
vi.mock("@/utils/http", () => ({ default: { get: vi.fn(), post: vi.fn() } }));
describe("process knowledge API", () => {
  it("uses actual RAG routes and their unwrapped envelopes", async () => {
    const record = processRecord();
    vi.mocked(http.get).mockResolvedValue({ data: { records: [record] } });
    expect(await listProcessRecords()).toEqual([record]);
    expect(http.get).toHaveBeenCalledWith("/api/rag/process/review/records");
    vi.mocked(http.post)
      .mockResolvedValueOnce({ data: { quadruple: record } })
      .mockResolvedValueOnce({ data: record });
    expect(await createProcessRecord(record)).toEqual(record);
    const action = {
      expected_revision: 1,
      decision: "rejected" as const,
      note: "Synthetic",
    };
    expect(await reviewProcessRecord("record/id", action)).toEqual(record);
    expect(http.post).toHaveBeenLastCalledWith(
      "/api/rag/process/review/record%2Fid",
      action,
    );
  });
});
