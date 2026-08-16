import { Router, Request, Response, NextFunction } from "express";
import { ZodTypeAny } from "zod";
import { authenticate as authenticateRaw, requireAdmin as requireAdminRaw } from "../../middleware/auth";
import {
  getLessonBySlug,
  getModuleOverview,
  getPreferencePair,
  getPreferencePairs,
  getRubric,
  getSupervisorAnalytics,
  getLiveAnalyticsFeed,
  getVivaEvaluationDetail,
  initializeVivaSession,
  evaluateVivaSession,
  listLessons,
  updateProgress,
  validateAssessment,
} from "./controller";
import {
  acknowledgeUhrsBanHandler,
  getUhrsStatusHandler,
  resetUhrsPracticeHandler,
  submitUhrsTaskHandler,
} from "./uhrsController";
import {
  startVivaSessionSchema,
  evaluateVivaSessionSchema,
  validateAssessmentSchema,
  submitUhrsTaskSchema,
} from "./validation";
const router = Router();

/** Guard against stale Windows files missing named exports (never crash boot). */
function requireHandler(
  name: string,
  handler: unknown
): (req: Request, res: Response, next: NextFunction) => void {
  if (typeof handler === "function") {
    return handler as (req: Request, res: Response, next: NextFunction) => void;
  }
  return (_req, res) => {
    res.status(501).json({
      status: "error",
      error: `Handler ${name} is missing — sync that file from the drop-in pack`,
    });
  };
}

const authenticate = requireHandler("authenticate", authenticateRaw);
const requireAdmin = requireHandler("requireAdmin", requireAdminRaw);

/**
 * Zod body validator — assigns parsed body (so .default() values apply).
 * Matches Uromi blueprint validate() middleware shape.
 */
const validateBody = (schema: ZodTypeAny) => {
  return async (
    req: Request,
    res: Response,
    next: NextFunction
  ): Promise<void> => {
    try {
      const parsed = await schema.parseAsync({
        body: req.body,
        query: req.query,
        params: req.params,
      });
      if (
        parsed &&
        typeof parsed === "object" &&
        "body" in (parsed as Record<string, unknown>)
      ) {
        req.body = (parsed as { body: unknown }).body;
      }
      next();
    } catch (error: any) {
      const issues = error?.issues || error?.errors || [];
      res.status(400).json({
        status: "error",
        error: "Validation failed",
        errors: issues,
        details: issues.map(
          (e: any) => `${(e.path || []).join(".")}: ${e.message}`
        ),
      });
    }
  };
};

router.get("/", requireHandler("getModuleOverview", getModuleOverview));
router.get("/lessons", requireHandler("listLessons", listLessons));

// Endpoint: GET /api/v1/modules/rlhf-core-rubric/lessons/:slug
router.get("/lessons/:slug", requireHandler("getLessonBySlug", getLessonBySlug));

router.get("/rubric", requireHandler("getRubric", getRubric));
router.get(
  "/dataset/preference-pairs",
  requireHandler("getPreferencePairs", getPreferencePairs)
);
router.get(
  "/dataset/preference-pairs/:pairId",
  requireHandler("getPreferencePair", getPreferencePair)
);

// Browser GET helper — validate itself is POST-only
router.get("/validate", (_req, res) => {
  const accept = String(_req.headers.accept || "");
  const payload = {
    ok: true,
    method: "POST",
    path: "/api/v1/modules/rlhf-core-rubric/validate",
    auth: "Bearer <token> required",
    note: "Open this URL in a browser sends GET, which cannot submit an assessment. Use Postman, curl, or the evaluation workspace UI.",
    bodyExample: {
      userId: "<uuid>",
      evaluationStartedAt: "2026-08-10T20:00:00.000Z",
      answers: {
        "pair-01": {
          chosenLabel: "prefer_b",
          scoresA: { helpfulness: 2, factuality: 1, safety: 5, tone: 4 },
          scoresB: { helpfulness: 5, factuality: 5, safety: 5, tone: 4 },
        },
        "pair-02": {
          chosenLabel: "prefer_b",
          scoresA: { helpfulness: 5, factuality: 5, safety: 1, tone: 4 },
          scoresB: { helpfulness: 4, factuality: 5, safety: 5, tone: 5 },
        },
      },
    },
  };

  if (accept.includes("text/html")) {
    res.status(200).type("html").send(`<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8" /><meta name="viewport" content="width=device-width, initial-scale=1" />
<title>RLHF Validate · POST only</title>
<style>
  body{font-family:ui-sans-serif,system-ui,sans-serif;margin:0;background:#f4f7f8;color:#14212b}
  main{max-width:42rem;margin:2rem auto;padding:1.5rem;background:#fff;border:1px solid #d7e0e6;border-radius:1rem}
  code,pre{background:#eef4f3;border-radius:.4rem}
  pre{padding:1rem;overflow:auto}
  h1{font-family:Georgia,serif}
  .badge{display:inline-block;background:#0f766e;color:#fff;padding:.2rem .55rem;border-radius:.35rem;font-size:.8rem}
</style></head>
<body><main>
  <p class="badge">POST ONLY</p>
  <h1>Validation endpoint</h1>
  <p>Browsers always use <strong>GET</strong> when you paste a URL. This route only accepts <strong>POST</strong> with a JSON body and Bearer token.</p>
  <p>Use <strong>Postman</strong>, <strong>curl</strong>, or the workspace at <code>/talent/assessment/rlhf</code>.</p>
  <pre>${JSON.stringify(payload.bodyExample, null, 2)}</pre>
</main></body></html>`);
    return;
  }

  res.status(200).json(payload);
});

// Protected Endpoint with strict Zod parsing validation middleware layer
router.post(
  "/validate",
  authenticate,
  validateBody(validateAssessmentSchema),
  requireHandler("validateAssessment", validateAssessment)
);

router.post(
  "/progress",
  authenticate,
  requireHandler("updateProgress", updateProgress)
);

/**
 * GET /api/rlhf/analytics/live-feed
 * Registered before /analytics so path matching stays unambiguous.
 * ToT head-desk telemetry matrix — ADMIN only (same Justice gate as evaluate).
 */
router.get(
  "/analytics/live-feed",
  authenticate,
  requireAdmin,
  requireHandler("getLiveAnalyticsFeed", getLiveAnalyticsFeed)
);

// Administrative Endpoint: GET /api/v1/modules/rlhf-core-rubric/analytics
// Auth middleware + ADMIN role check inside getSupervisorAnalytics
router.get(
  "/analytics",
  authenticate,
  requireHandler("getSupervisorAnalytics", getSupervisorAnalytics)
);

/**
 * POST /api/v1/modules/rlhf-core-rubric/viva/initialize
 * ToT registers a student at a physical Ugboha Road workstation and boots Tier 2.
 * (Blueprint alias path: /api/rlhf/viva/initialize — use module mount above.)
 */
router.post(
  "/viva/initialize",
  validateBody(startVivaSessionSchema),
  requireHandler("initializeVivaSession", initializeVivaSession)
);

/**
 * GET /api/rlhf/viva/:id
 * ToT transcript + score snapshot before sign-off (ADMIN only).
 */
router.get(
  "/viva/:id",
  authenticate,
  requireAdmin,
  requireHandler("getVivaEvaluationDetail", getVivaEvaluationDetail)
);

/**
 * POST /api/rlhf/viva/:id/evaluate
 * ToT certification sign-off (ADMIN only).
 */
router.post(
  "/viva/:id/evaluate",
  authenticate,
  requireAdmin,
  validateBody(evaluateVivaSessionSchema),
  requireHandler("evaluateVivaSession", evaluateVivaSession)
);

/**
 * Tokoka / UHRS simulator — quality score + control questions + speed gates.
 * Mounted at /api/rlhf/uhrs/* and /api/v1/modules/rlhf-core-rubric/uhrs/*
 */
router.get("/uhrs/status", authenticate, getUhrsStatusHandler);
router.post(
  "/uhrs/submit",
  authenticate,
  validateBody(submitUhrsTaskSchema),
  submitUhrsTaskHandler
);
router.post("/uhrs/acknowledge-ban", authenticate, acknowledgeUhrsBanHandler);
router.post("/uhrs/reset", authenticate, resetUhrsPracticeHandler);

export default router;
