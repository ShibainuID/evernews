const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

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

export class VerificationError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

export async function submitVerification(file: File, caption: string): Promise<VerificationResult> {
  const body = new FormData();
  body.append("video", file);
  body.append("caption", caption);

  const res = await fetch(`${API_BASE}/api/v1/verifications`, { method: "POST", body });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    throw new VerificationError(detail.detail ?? "Something went wrong while tracing that clip.", res.status);
  }
  return res.json();
}
