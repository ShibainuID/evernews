const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const VERIFICATION_PATH = `${API_BASE}/api/v1/verification`;

export type ComparisonStatus = "consistent" | "mismatch" | "unknown";
export type VisualMatchLabel = "high" | "medium" | "low" | "unknown";
export type ResultClassification =
  | "possible_false_context"
  | "context_consistent_with_source"
  | "claim_conflict_found"
  | "source_match_with_incomplete_context"
  | "insufficient_evidence";

export interface DimensionComparison {
  current: string | null;
  source: string | null;
  status: ComparisonStatus;
  confidence: number;
  evidence_ids: string[];
  explanation: string;
}

export interface ContextComparison {
  event: DimensionComparison;
  location: DimensionComparison;
  date: DimensionComparison;
}

export interface SourceContext {
  event: string | null;
  location: string | null;
  date: string | null;
  publisher: string | null;
  source_url: string | null;
  title: string | null;
}

export interface SourceCandidate {
  source_id: string;
  url: string;
  publisher: string | null;
  title: string | null;
  published_at: string | null;
  event: string | null;
  location: string | null;
}

export interface VerificationResult {
  verification_id: string;
  classification: ResultClassification;
  evidence_confidence: "low" | "medium" | "high";
  confidence_score: number | null;
  source_context: SourceContext | null;
  comparison: ContextComparison;
  visual_match: VisualMatchLabel;
  headline: string;
  summary: string;
  manipulation_types: string[];
  sources: SourceCandidate[];
  unresolved: string[];
  warnings: string[];
}

// Mirrors backend/state.py's STAGES tuple, real pipeline stages, not a
// fixed animation timer.
export type VerificationStage =
  | "queued"
  | "preprocessing"
  | "extracting_context"
  | "planning_investigation"
  | "fact_check_search"
  | "web_research"
  | "visual_source_search"
  | "synthesizing_evidence"
  | "comparing_context"
  | "completed"
  | "failed";

export interface VerificationStatus {
  verification_id: string;
  status: "processing" | "completed" | "failed";
  stage: VerificationStage;
  progress: number;
  error: string | null;
}

export class VerificationError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

async function readErrorDetail(res: Response, fallback: string): Promise<string> {
  const body = await res.json().catch(() => null);
  const detail = body?.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail.message === "string") return detail.message;
  return fallback;
}

/** Kicks off a verification job; returns immediately with its id (202). */
export async function startVerification(file: File, caption: string): Promise<{ verification_id: string }> {
  const body = new FormData();
  body.append("video", file);
  body.append("caption", caption);

  const res = await fetch(VERIFICATION_PATH, { method: "POST", body });
  if (!res.ok) {
    throw new VerificationError(await readErrorDetail(res, "That clip didn't make it through, try again."), res.status);
  }
  return res.json();
}

export async function getVerificationStatus(id: string): Promise<VerificationStatus> {
  const res = await fetch(`${VERIFICATION_PATH}/${id}`);
  if (!res.ok) {
    throw new VerificationError(await readErrorDetail(res, "Lost track of that verification, try again."), res.status);
  }
  return res.json();
}

export async function getVerificationResult(id: string): Promise<VerificationResult> {
  const res = await fetch(`${VERIFICATION_PATH}/${id}/result`);
  if (!res.ok) {
    throw new VerificationError(await readErrorDetail(res, "That clip didn't make it through, try again."), res.status);
  }
  return res.json();
}

const POLL_INTERVAL_MS = 1200;
const POLL_TIMEOUT_MS = 120_000;

/** Starts a verification job and polls until it completes or fails. */
export async function submitVerification(
  file: File,
  caption: string,
  onStage?: (stage: VerificationStage) => void,
): Promise<VerificationResult> {
  const { verification_id } = await startVerification(file, caption);
  const deadline = Date.now() + POLL_TIMEOUT_MS;

  while (Date.now() < deadline) {
    const status = await getVerificationStatus(verification_id);
    onStage?.(status.stage);
    if (status.status === "completed") {
      return getVerificationResult(verification_id);
    }
    if (status.status === "failed") {
      throw new VerificationError(status.error ?? "That clip didn't make it through, try again.", 500);
    }
    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
  }
  throw new VerificationError("This is taking longer than expected, try again in a bit.", 504);
}
