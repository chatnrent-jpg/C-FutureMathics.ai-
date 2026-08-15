const API_BASE =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") || "";

/** Prefer same-origin /api/rlhf rewrite; fall back to absolute API host. */
function rlhfUrl(path: string): string {
  const p = path.startsWith("/") ? path : `/${path}`;
  if (API_BASE) return `${API_BASE}/api/rlhf${p}`;
  return `/api/rlhf${p}`;
}

export type UhrsSnapshot = {
  uhrsSpamScore: number;
  totalControlQuestions: number;
  correctControlAnswers: number;
  speedViolations: number;
  lastSubmissionTime: string | null;
  isSimulatedBanned: boolean;
  flagged?: boolean;
};

export type UhrsSubmitResult = {
  snapshot: UhrsSnapshot;
  flagged: boolean;
  newlyBanned: boolean;
  speedViolation: boolean;
  teachingMoment: string | null;
};

function authHeaders(token?: string | null): HeadersInit {
  const headers: HeadersInit = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

async function parseJson(res: Response): Promise<any> {
  return res.json().catch(() => ({}));
}

function httpError(res: Response, json: any, fallback: string): Error {
  const msg =
    json?.error || json?.message || `${fallback} (${res.status})`;
  if (res.status === 404) {
    const hit = json?.path ? ` (got 404 for ${json.path})` : "";
    return new Error(
      `${msg}${hit} — backend process is still running WITHOUT UHRS files. In vettedme-backend run APPLY-UHRS-EMBEDDED.ps1, then kill port 8080 and restart: npx tsx watch src/index.ts`
    );
  }
  return new Error(msg);
}

export async function fetchUhrsStatus(token: string): Promise<UhrsSnapshot> {
  const res = await fetch(rlhfUrl("/uhrs/status"), {
    headers: authHeaders(token),
    cache: "no-store",
  });
  const json = await parseJson(res);
  if (!res.ok) throw httpError(res, json, "UHRS status failed");
  return json.data as UhrsSnapshot;
}

export async function submitUhrsTask(
  token: string,
  body: {
    isControlQuestion: boolean;
    controlCorrect?: boolean;
    responseTimeMs: number;
  }
): Promise<UhrsSubmitResult> {
  const res = await fetch(rlhfUrl("/uhrs/submit"), {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
  const json = await parseJson(res);
  if (!res.ok) throw httpError(res, json, "UHRS submit failed");
  return json.data as UhrsSubmitResult;
}

export async function acknowledgeUhrsBan(token: string): Promise<UhrsSnapshot> {
  const res = await fetch(rlhfUrl("/uhrs/acknowledge-ban"), {
    method: "POST",
    headers: authHeaders(token),
  });
  const json = await parseJson(res);
  if (!res.ok) throw httpError(res, json, "UHRS acknowledge failed");
  return json.data as UhrsSnapshot;
}

export async function resetUhrsPractice(token: string): Promise<UhrsSnapshot> {
  const res = await fetch(rlhfUrl("/uhrs/reset"), {
    method: "POST",
    headers: authHeaders(token),
  });
  const json = await parseJson(res);
  if (!res.ok) throw httpError(res, json, "UHRS reset failed");
  return json.data as UhrsSnapshot;
}
