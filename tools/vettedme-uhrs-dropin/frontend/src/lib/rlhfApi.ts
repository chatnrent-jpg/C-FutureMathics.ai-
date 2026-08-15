const API_BASE =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") || "http://localhost:8080";

export type RubricDimensionKey =
  | "helpfulnessDirectness"
  | "truthfulnessFactuality"
  | "harmMitigationSafety"
  | "toneFormatting";

export type DimensionScoreMap = Record<RubricDimensionKey, number | null>;

export type PreferenceLabel =
  | "prefer_a"
  | "prefer_b"
  | "tie"
  | "both_bad"
  | "invalid_prompt";

export interface CandidatePair {
  pairId: string;
  title: string;
  difficulty: string;
  prompt: { text: string };
  responseA: { label: string; text: string };
  responseB: { label: string; text: string };
}

export interface CandidateDataset {
  datasetId: string;
  moduleId: string;
  version: string;
  title: string;
  description: string;
  rubricDimensions: string[];
  pairs: CandidatePair[];
}

export const DIMENSIONS: Array<{
  key: RubricDimensionKey;
  label: string;
  short: string;
}> = [
  {
    key: "helpfulnessDirectness",
    label: "Helpfulness & Directness",
    short: "Helpfulness",
  },
  {
    key: "truthfulnessFactuality",
    label: "Truthfulness & Factuality",
    short: "Truthfulness",
  },
  {
    key: "harmMitigationSafety",
    label: "Harm Mitigation & Safety",
    short: "Safety",
  },
  {
    key: "toneFormatting",
    label: "Tone & Formatting",
    short: "Tone",
  },
];

function authHeaders(token?: string | null): HeadersInit {
  const headers: HeadersInit = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

export async function fetchCandidateDataset(): Promise<CandidateDataset> {
  const res = await fetch(
    `${API_BASE}/api/v1/modules/rlhf-core-rubric/dataset/preference-pairs?mode=candidate`,
    { cache: "no-store" }
  );
  if (!res.ok) {
    throw new Error(`Failed to load dataset (${res.status})`);
  }
  const json = await res.json();
  return json.dataset as CandidateDataset;
}

export async function ensureAssessmentSession(): Promise<{
  token: string;
  userId: string;
}> {
  if (typeof window === "undefined") {
    throw new Error("Session bootstrap requires browser");
  }

  const existingToken = window.localStorage.getItem("vetted_token");
  const existingUserId = window.localStorage.getItem("vetted_user_id");
  if (existingToken && existingUserId) {
    return { token: existingToken, userId: existingUserId };
  }

  const email = `rlhf.candidate.${Date.now()}@vetted.local`;
  const password = `Rlhf-${Math.random().toString(36).slice(2)}-9!`;

  // Prefer same-origin rewrite (/api/auth/* -> backend /api/v1/auth/*)
  const registerRes = await fetch(`/api/auth/register`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({
      email,
      password,
      firstName: "RLHF",
      lastName: "Candidate",
      role: "TALENT",
    }),
  });

  if (!registerRes.ok) {
    throw new Error(`Unable to create assessment session (${registerRes.status})`);
  }

  const data = await registerRes.json();
  const token = String(data.token || "");
  const userId = String(data.user?.id || "");
  if (!token || !userId) {
    throw new Error("Assessment session missing token/user id");
  }

  window.localStorage.setItem("vetted_token", token);
  window.localStorage.setItem("vetted_user_id", userId);
  window.localStorage.setItem("vetted_email", email);
  return { token, userId };
}

export interface DimensionDelta {
  side: "A" | "B";
  dimension: RubricDimensionKey;
  userScore: number;
  goldScore: number;
  delta: number;
}

export interface MisalignmentFlag {
  code:
    | "CONFIDENCE_OVER_FACTUALITY"
    | "SAFETY_HARD_GATE_MISSED"
    | "LABEL_MISMATCH"
    | "INSUFFICIENT_EVALUATION_TIME"
    | "HIGH_DIMENSION_DRIFT";
  severity: "critical" | "warning" | "info";
  title: string;
  detail: string;
  pairId?: string;
}

export interface DimensionErrorBar {
  dimension: RubricDimensionKey;
  label: string;
  meanAbsDelta: number;
  meanSignedDelta: number;
  maxAbsDelta: number;
  barRatio: number;
}

export interface PairCalibrationReveal {
  pairId: string;
  title: string;
  submittedLabel: string;
  goldLabel: string;
  labelMatch: boolean;
  calibrationTarget?: string;
  rationale: {
    summary: string;
    dimensionNotes: Record<string, string>;
    raterBlindSpot?: string;
  } | null;
  goldSuggestedScores: {
    A: Record<RubricDimensionKey, number>;
    B: Record<RubricDimensionKey, number>;
  } | null;
  dimensionDeltas: DimensionDelta[];
  meanAbsDelta: number;
}

export interface CalibrationAnalytics {
  available: boolean;
  outcome: "passed" | "failed" | "hard_fail";
  pass: boolean;
  hardFail: boolean;
  reason: string | null;
  percent: number;
  passThresholdPercent: number;
  timing?: {
    elapsedSeconds: number;
    insufficientTime: boolean;
    minRequiredSeconds: number;
  };
  errorDeltaChart: DimensionErrorBar[];
  misalignmentFlags: MisalignmentFlag[];
  pairReveals: PairCalibrationReveal[];
  dimensionStruggle: Partial<Record<RubricDimensionKey, number>>;
}

export interface ValidateResult {
  success: boolean;
  hardFail: boolean;
  pass: boolean;
  reason?: string;
  scoring?: { percent?: number; passThresholdPercent?: number };
  calibration?: CalibrationAnalytics;
  user?: Record<string, unknown>;
}

export async function submitValidation(input: {
  token: string;
  userId: string;
  evaluationStartedAt: string;
  submissions: Array<{
    pairId: string;
    label: PreferenceLabel;
    scores: {
      A: Record<RubricDimensionKey, number>;
      B: Record<RubricDimensionKey, number>;
    };
    readReceipts: { readA: true; readB: true };
  }>;
}): Promise<{
  pass?: boolean;
  finalScore?: number;
  status?: string;
  reason?: string;
  message?: string;
  result?: ValidateResult;
}> {
  // Compact answers payload expected by controller validateAssessment
  const answers: Record<
    string,
    {
      chosenLabel: PreferenceLabel;
      scoresA: {
        helpfulness: number;
        factuality: number;
        safety: number;
        tone: number;
      };
      scoresB: {
        helpfulness: number;
        factuality: number;
        safety: number;
        tone: number;
      };
    }
  > = {};

  for (const sub of input.submissions) {
    answers[sub.pairId] = {
      chosenLabel: sub.label,
      scoresA: {
        helpfulness: sub.scores.A.helpfulnessDirectness,
        factuality: sub.scores.A.truthfulnessFactuality,
        safety: sub.scores.A.harmMitigationSafety,
        tone: sub.scores.A.toneFormatting,
      },
      scoresB: {
        helpfulness: sub.scores.B.helpfulnessDirectness,
        factuality: sub.scores.B.truthfulnessFactuality,
        safety: sub.scores.B.harmMitigationSafety,
        tone: sub.scores.B.toneFormatting,
      },
    };
  }

  const res = await fetch(
    `${API_BASE}/api/v1/modules/rlhf-core-rubric/validate`,
    {
      method: "POST",
      headers: authHeaders(input.token),
      body: JSON.stringify({
        userId: input.userId,
        evaluationStartedAt: input.evaluationStartedAt,
        answers,
      }),
    }
  );

  const json = await res.json().catch(() => ({}));
  if (!res.ok && res.status !== 422) {
    throw new Error(json?.error || json?.message || `Submit failed (${res.status})`);
  }
  return json;
}
