import http from "@/utils/http";

const BASE = "/api/rag/process";
export interface DrillingLimits {
  schema_version: 1;
  hardness_min_hrc: number;
  hardness_max_hrc: number;
  hardness_max_inclusive: boolean;
  diameter_min_mm: number;
  diameter_max_mm: number;
  max_depth_to_diameter: number;
  coolants: string[];
  workholdings: string[];
}
export type ReviewStatus = "pending" | "approved" | "rejected" | "revoked";
export interface ProcessContent {
  feature: string;
  process: string;
  tool: string;
  material: string;
  parameters: Record<string, number>;
  source: string;
  confidence: number;
  chunk_ids: string[];
  tags: string[];
  candidate_context?: Omit<Partial<ReviewEvidence>, "constraints"> & {
    note?: string;
    constraints?: Partial<DrillingLimits>;
  };
}
export interface ReviewEvidence {
  source_url: string;
  document_sha256: string;
  page: number;
  material: string;
  tool: string;
  parameter_units: Record<string, string>;
  applicability: string;
  verification_method: string;
  unresolved: string[];
  constraints?: DrillingLimits;
}
export interface ReviewStamp {
  status: ReviewStatus;
  actor?: string;
  at?: string;
  note?: string;
  evidence?: ReviewEvidence;
  evidence_digest?: string;
}
export interface ProcessRecord extends ProcessContent {
  record_id: string;
  revision: number;
  review: ReviewStamp;
  review_history: {
    before: Omit<ProcessRecord, "review_history">;
    after_review: ReviewStamp;
  }[];
}
export interface ReviewAction {
  expected_revision: number;
  decision: ReviewStatus | "revise";
  note: string;
  evidence?: ReviewEvidence;
  replacement?: ProcessContent;
}

export async function listProcessRecords(): Promise<ProcessRecord[]> {
  const response = await http.get<{ records: ProcessRecord[] }>(
    `${BASE}/review/records`,
  );
  return response.data.records;
}
export async function createProcessRecord(
  content: ProcessContent,
): Promise<ProcessRecord> {
  const response = await http.post<{ quadruple: ProcessRecord }>(
    `${BASE}/add`,
    content,
  );
  return response.data.quadruple;
}
export async function reviewProcessRecord(
  id: string,
  action: ReviewAction,
): Promise<ProcessRecord> {
  const response = await http.post<ProcessRecord>(
    `${BASE}/review/${encodeURIComponent(id)}`,
    action,
  );
  return response.data;
}
