import { Request, Response } from "express";
import fs from "fs";
import path from "path";
import readline from "readline";
import { AIStatus, EvaluationTier, NictmDepartment } from "@prisma/client";
import { prisma } from "../../lib/prisma";
import { asyncHandler, AppError } from "../../middleware/errorHandler";
import { logger } from "../../utils/logger";
import { rlhfCoreRubricService } from "./service";
import { renderLessonPage, wantsHtml } from "./renderLessonHtml";
import {
  aggregateDimensionStruggle,
  computeDimensionDeltas,
  rlhfTelemetry,
} from "./telemetry";
import { buildCalibrationAnalytics } from "./calibration";

/** Compact admin-facing names for rubric dimensions. */
const DIMENSION_ALIAS: Record<string, string> = {
  helpfulnessDirectness: "helpfulness",
  truthfulnessFactuality: "factuality",
  harmMitigationSafety: "safety",
  toneFormatting: "tone",
  helpfulness: "helpfulness",
  factuality: "factuality",
  safety: "safety",
  tone: "tone",
};

function aliasDimension(dim: string): string {
  return DIMENSION_ALIAS[dim] || dim;
}

type CompactScores = {
  helpfulness: number;
  factuality: number;
  safety: number;
  tone: number;
};

// Interface mapping for expected incoming request payloads
interface UserSubmission {
  userId: string;
  evaluationStartedAt: string; // ISO string to catch speedrunners
  answers: {
    [pairId: string]: {
      chosenLabel: "prefer_a" | "prefer_b" | "tie" | "both_bad" | "invalid_prompt";
      scoresA: CompactScores;
      scoresB: CompactScores;
    };
  };
}

type GoldPairFile = {
  id: string;
  blindSpot?: string;
  goldLabel: "prefer_a" | "prefer_b" | string;
  goldAnchors: {
    responseA: CompactScores;
    responseB: CompactScores;
  };
};

function compactToRubric(scores: CompactScores) {
  return {
    helpfulnessDirectness: Number(scores.helpfulness),
    truthfulnessFactuality: Number(scores.factuality),
    harmMitigationSafety: Number(scores.safety),
    toneFormatting: Number(scores.tone),
  };
}

function rubricToCompact(scores: Record<string, number>): CompactScores {
  return {
    helpfulness: Number(scores.helpfulnessDirectness ?? scores.helpfulness),
    factuality: Number(scores.truthfulnessFactuality ?? scores.factuality),
    safety: Number(scores.harmMitigationSafety ?? scores.safety),
    tone: Number(scores.toneFormatting ?? scores.tone),
  };
}

/** Accept new `answers` shape or legacy `submissions[]` from the workspace UI. */
function normalizeSubmission(body: any, authUserId?: string): UserSubmission | null {
  const userId = String(authUserId || body?.userId || "");
  const evaluationStartedAt = String(body?.evaluationStartedAt || "");
  if (!userId || !evaluationStartedAt) return null;

  if (body?.answers && typeof body.answers === "object") {
    return { userId, evaluationStartedAt, answers: body.answers };
  }

  if (Array.isArray(body?.submissions)) {
    const answers: UserSubmission["answers"] = {};
    for (const sub of body.submissions) {
      const pairId = String(sub.pairId || "");
      if (!pairId) continue;
      answers[pairId] = {
        chosenLabel: sub.label,
        scoresA: rubricToCompact(sub.scores?.A || {}),
        scoresB: rubricToCompact(sub.scores?.B || {}),
      };
    }
    return { userId, evaluationStartedAt, answers };
  }

  return null;
}

/**
 * Read a lesson markdown file directly from disk by slug.
 * GET /api/v1/modules/rlhf-core-rubric/lessons/:slug
 *
 * Browser (Accept: text/html) → rendered HTML page
 * API / ?format=json → { slug, content }
 */
export const getLessonBySlug = async (
  req: Request,
  res: Response
): Promise<void> => {
  const { slug } = req.params;

  // Prevent directory traversal attacks
  const safeSlug = path.basename(String(slug || ""));
  const filePath = path.join(__dirname, "content", `${safeSlug}.md`);

  try {
    if (!safeSlug || safeSlug !== String(slug || "")) {
      res.status(400).json({ error: "Invalid lesson slug" });
      return;
    }

    if (!fs.existsSync(filePath)) {
      res.status(404).json({ error: "Lesson file not found" });
      return;
    }

    const markdownContent = fs.readFileSync(filePath, "utf-8");

    if (wantsHtml(req)) {
      res
        .status(200)
        .type("html")
        .send(renderLessonPage(safeSlug, markdownContent));
      return;
    }

    res.status(200).json({
      slug: safeSlug,
      content: markdownContent,
    });
  } catch (_error) {
    res.status(500).json({ error: "Failed to read lesson content" });
  }
};

/**
 * Grade candidate submissions against gold anchors.
 * POST /api/v1/modules/rlhf-core-rubric/validate
 *
 * - Speed-run gate (< 45s)
 * - pair-02 safety hard gate (scoresA.safety > 1)
 * - Label match (50) + MAE distance points (up to 50) per pair
 * - Prisma aiStatus / aiScore update
 */
export const validateAssessment = async (
  req: Request,
  res: Response
): Promise<void> => {
  try {
    const submission = normalizeSubmission(req.body, req.user?.id);
    if (!submission) {
      res
        .status(400)
        .json({ error: "Missing required validation payload fields." });
      return;
    }

    const { userId, evaluationStartedAt, answers } = submission;

    const user = await prisma.user.findUnique({ where: { id: userId } });
    if (!user) {
      res.status(404).json({ error: "User not found" });
      return;
    }

    // 1. Anti-Cheat: Timing Check
    const startTime = new Date(evaluationStartedAt).getTime();
    if (Number.isNaN(startTime)) {
      res.status(400).json({ error: "evaluationStartedAt must be a valid ISO datetime" });
      return;
    }
    const endTime = Date.now();
    const elapsedSeconds = (endTime - startTime) / 1000;

    if (elapsedSeconds < 45) {
      const failedUser = await prisma.user.update({
        where: { id: userId },
        data: {
          aiStatus: AIStatus.FAILED,
          aiScore: 0,
          aiFailureReason: "INSUFFICIENT_EVALUATION_TIME",
          aiLastAttemptAt: new Date(),
          aiCompletedAt: null,
        },
        select: {
          id: true,
          aiStatus: true,
          aiScore: true,
          aiFailureReason: true,
          aiLastAttemptAt: true,
          aiCompletedAt: true,
        },
      });

      await rlhfTelemetry.write({
        eventType: "HARD_FAIL_INSUFFICIENT_TIME",
        timestamp: new Date().toISOString(),
        userId,
        elapsedSeconds: Number(elapsedSeconds.toFixed(2)),
        pass: false,
        percent: 0,
        aiStatus: AIStatus.FAILED,
        aiFailureReason: "INSUFFICIENT_EVALUATION_TIME",
      });

      res.status(422).json({
        pass: false,
        reason: "INSUFFICIENT_EVALUATION_TIME",
        message:
          "Assessment rejected. Evaluation completed too quickly to thoroughly review the material.",
        finalScore: 0,
        status: AIStatus.FAILED,
        result: {
          success: false,
          hardFail: true,
          pass: false,
          reason: "INSUFFICIENT_EVALUATION_TIME",
          scoring: { percent: 0, passThresholdPercent: 70 },
          user: failedUser,
          timing: {
            elapsedSeconds: Number(elapsedSeconds.toFixed(2)),
            insufficientTime: true,
            minRequiredSeconds: 45,
          },
        },
      });
      return;
    }

    // Load Gold Standard Data
    const datasetPath = path.join(
      __dirname,
      "dataset",
      "preference-pairs.json"
    );
    const goldPairs = JSON.parse(
      fs.readFileSync(datasetPath, "utf-8")
    ) as GoldPairFile[];

    if (!Array.isArray(goldPairs) || !goldPairs.length) {
      res.status(500).json({ error: "Gold evaluation dataset unavailable" });
      return;
    }

    let totalScore = 0;
    const maxPossibleScore = goldPairs.length * 100; // 100 points max per pair
    const pairResults: Array<{
      pairId: string;
      submittedLabel: string;
      goldLabel: string;
      labelMatch: boolean;
      dimensionDeltas: ReturnType<typeof computeDimensionDeltas>;
      meanAbsDelta: number;
      pairPoints: number;
    }> = [];

    // 2. Evaluate Each Submission Pair
    for (const gold of goldPairs) {
      const userAns = answers[gold.id];
      if (!userAns) {
        res
          .status(400)
          .json({ error: `Missing answers for pair evaluation: ${gold.id}` });
        return;
      }

      // --- CRITICAL HARM MITIGATION SAFETY GATE CHECK ---
      // If evaluating pair-02 (the jailbreak screenplay) and user scores harmful Response A above a 1 on Safety
      if (gold.id === "pair-02" && Number(userAns.scoresA.safety) > 1) {
        const failedUser = await prisma.user.update({
          where: { id: userId },
          data: {
            aiStatus: AIStatus.FAILED,
            aiScore: 0,
            aiFailureReason: "CRITICAL_SAFETY_GATE_VIOLATION",
            aiLastAttemptAt: new Date(),
            aiCompletedAt: null,
          },
          select: {
            id: true,
            aiStatus: true,
            aiScore: true,
            aiFailureReason: true,
            aiLastAttemptAt: true,
            aiCompletedAt: true,
          },
        });

        await rlhfTelemetry.write({
          eventType: "HARD_FAIL_SAFETY",
          timestamp: new Date().toISOString(),
          userId,
          elapsedSeconds: Number(elapsedSeconds.toFixed(2)),
          pass: false,
          percent: 0,
          aiStatus: AIStatus.FAILED,
          aiFailureReason: "CRITICAL_SAFETY_GATE_VIOLATION",
          meta: { unsafeASafety: userAns.scoresA.safety },
        });

        const partialPairs = goldPairs.map((g) => {
          const ans = answers[g.id];
          const deltas = ans
            ? computeDimensionDeltas(
                {
                  A: compactToRubric(ans.scoresA),
                  B: compactToRubric(ans.scoresB),
                },
                {
                  A: compactToRubric(g.goldAnchors.responseA),
                  B: compactToRubric(g.goldAnchors.responseB),
                }
              )
            : [];
          return {
            pairId: g.id,
            submittedLabel: ans?.chosenLabel || "—",
            goldLabel: g.goldLabel,
            labelMatch: ans?.chosenLabel === g.goldLabel,
            dimensionDeltas: deltas,
            meanAbsDelta: deltas.length
              ? Number(
                  (
                    deltas.reduce((a, d) => a + Math.abs(d.delta), 0) /
                    deltas.length
                  ).toFixed(4)
                )
              : 0,
          };
        });
        const struggle = aggregateDimensionStruggle(partialPairs);
        const calibration = buildCalibrationAnalytics({
          hardFail: true,
          pass: false,
          reason: "CRITICAL_SAFETY_GATE_VIOLATION",
          percent: 0,
          passThresholdPercent: 70,
          timing: {
            elapsedSeconds: Number(elapsedSeconds.toFixed(2)),
            insufficientTime: false,
            minRequiredSeconds: 45,
          },
          goldPairs: goldPairs.map((g) => ({
            pairId: g.id,
            title: g.blindSpot || g.id,
            goldLabel: g.goldLabel,
            suggestedScores: {
              A: compactToRubric(g.goldAnchors.responseA),
              B: compactToRubric(g.goldAnchors.responseB),
            },
          })),
          submissions: Object.entries(answers).map(([pairId, ans]) => ({
            pairId,
            label: ans.chosenLabel,
            scores: {
              A: compactToRubric(ans.scoresA),
              B: compactToRubric(ans.scoresB),
            },
          })),
          pairResults: partialPairs,
          dimensionStruggle: struggle,
        });

        res.status(422).json({
          pass: false,
          reason: "CRITICAL_SAFETY_GATE_VIOLATION",
          message:
            "Critical safety violation. Harmful response options must receive a strict safety rating of 1.",
          finalScore: 0,
          status: AIStatus.FAILED,
          result: {
            success: false,
            hardFail: true,
            pass: false,
            reason: "CRITICAL_SAFETY_GATE_VIOLATION",
            scoring: { percent: 0, passThresholdPercent: 70 },
            calibration,
            user: failedUser,
          },
        });
        return;
      }

      // Component A: Label Match (50 points)
      let pairPoints = 0;
      const labelMatch = userAns.chosenLabel === gold.goldLabel;
      if (labelMatch) {
        pairPoints += 50;
      }

      // Component B: Distance Matrix Penalty (Up to 50 points based on Mean Absolute Error)
      let absoluteErrorSum = 0;
      const dimensions = [
        "helpfulness",
        "factuality",
        "safety",
        "tone",
      ] as const;

      for (const dim of dimensions) {
        absoluteErrorSum += Math.abs(
          Number(userAns.scoresA[dim]) - Number(gold.goldAnchors.responseA[dim])
        );
        absoluteErrorSum += Math.abs(
          Number(userAns.scoresB[dim]) - Number(gold.goldAnchors.responseB[dim])
        );
      }

      const totalDimensionsChecked = 8; // 4 dims * 2 responses
      const meanAbsoluteError = absoluteErrorSum / totalDimensionsChecked;

      // Calculate scaling factor: Max error per dimension is 4 (5-1)
      const scoreDistancePoints = Math.max(
        0,
        50 * (1 - meanAbsoluteError / 4)
      );
      pairPoints += scoreDistancePoints;
      totalScore += pairPoints;

      const dimensionDeltas = computeDimensionDeltas(
        {
          A: compactToRubric(userAns.scoresA),
          B: compactToRubric(userAns.scoresB),
        },
        {
          A: compactToRubric(gold.goldAnchors.responseA),
          B: compactToRubric(gold.goldAnchors.responseB),
        }
      );

      pairResults.push({
        pairId: gold.id,
        submittedLabel: userAns.chosenLabel,
        goldLabel: gold.goldLabel,
        labelMatch,
        dimensionDeltas,
        meanAbsDelta: Number(meanAbsoluteError.toFixed(4)),
        pairPoints: Number(pairPoints.toFixed(2)),
      });
    }

    // 3. Determine Passing Threshold & DB Update
    const finalBlendedPercentage = Math.round(
      (totalScore / maxPossibleScore) * 100
    );
    const didPass = finalBlendedPercentage >= 70;
    const computedStatus = didPass ? AIStatus.TIER1_PASSED : AIStatus.FAILED;

    const updatedUser = await prisma.user.update({
      where: { id: userId },
      data: {
        aiStatus: computedStatus,
        aiScore: finalBlendedPercentage,
        aiTier1Score: finalBlendedPercentage,
        aiFailureReason: didPass ? null : "SCORE_BELOW_PASSING_THRESHOLD",
        aiLastAttemptAt: new Date(),
        aiCompletedAt: didPass ? new Date() : null,
        aiSessionId: "rlhf-core-rubric:validate:controller",
      },
      select: {
        id: true,
        aiStatus: true,
        aiScore: true,
        aiTier1Score: true,
        aiFailureReason: true,
        aiLastAttemptAt: true,
        aiCompletedAt: true,
      },
    });

    const dimensionStruggle = aggregateDimensionStruggle(pairResults);
    const calibration = buildCalibrationAnalytics({
      hardFail: false,
      pass: didPass,
      reason: didPass ? null : "SCORE_BELOW_PASSING_THRESHOLD",
      percent: finalBlendedPercentage,
      passThresholdPercent: 70,
      timing: {
        elapsedSeconds: Number(elapsedSeconds.toFixed(2)),
        insufficientTime: false,
        minRequiredSeconds: 45,
      },
      goldPairs: goldPairs.map((g) => ({
        pairId: g.id,
        title: g.blindSpot || g.id,
        goldLabel: g.goldLabel,
        suggestedScores: {
          A: compactToRubric(g.goldAnchors.responseA),
          B: compactToRubric(g.goldAnchors.responseB),
        },
      })),
      submissions: Object.entries(answers).map(([pairId, ans]) => ({
        pairId,
        label: ans.chosenLabel,
        scores: {
          A: compactToRubric(ans.scoresA),
          B: compactToRubric(ans.scoresB),
        },
      })),
      pairResults,
      dimensionStruggle,
    });

    await rlhfTelemetry.write({
      eventType: "VALIDATION_GRADED",
      timestamp: new Date().toISOString(),
      userId,
      elapsedSeconds: Number(elapsedSeconds.toFixed(2)),
      pass: didPass,
      percent: finalBlendedPercentage,
      aiStatus: computedStatus,
      aiFailureReason: updatedUser.aiFailureReason,
      pairDeltas: pairResults.map((p) => ({
        pairId: p.pairId,
        labelMatch: p.labelMatch,
        submittedLabel: p.submittedLabel,
        goldLabel: p.goldLabel,
        dimensionDeltas: p.dimensionDeltas,
        meanAbsDelta: p.meanAbsDelta,
      })),
      dimensionStruggle,
    });

    res.status(200).json({
      pass: didPass,
      finalScore: finalBlendedPercentage,
      status: computedStatus,
      result: {
        success: true,
        hardFail: false,
        pass: didPass,
        reason: didPass ? undefined : "SCORE_BELOW_PASSING_THRESHOLD",
        scoring: {
          percent: finalBlendedPercentage,
          passThresholdPercent: 70,
          rawTotal: Number(totalScore.toFixed(2)),
          rawMax: maxPossibleScore,
        },
        pairResults,
        calibration,
        user: updatedUser,
      },
    });
  } catch (error: any) {
    logger.error("Validation engine error", { error: error?.message });
    res
      .status(500)
      .json({ error: "Internal validation engine error occurred." });
  }
};

export const getModuleOverview = asyncHandler(
  async (_req: Request, res: Response) => {
    const overview = await rlhfCoreRubricService.getOverview();
    res.status(200).json({ success: true, module: overview });
  }
);

export const listLessons = asyncHandler(async (_req: Request, res: Response) => {
  const lessons = await rlhfCoreRubricService.listLessons();
  res.status(200).json({
    success: true,
    moduleId: "rlhf-core-rubric",
    lessons,
  });
});

/** Manifest-aware lesson fetch (enriched metadata + markdown). */
export const getLesson = asyncHandler(async (req: Request, res: Response) => {
  const slug = String(req.params.slug || "");
  const lesson = await rlhfCoreRubricService.getLesson(slug);
  res.status(200).json({
    success: true,
    moduleId: "rlhf-core-rubric",
    lesson: {
      ...lesson.meta,
      contentMarkdown: lesson.markdown,
    },
  });
});

export const getRubric = asyncHandler(async (_req: Request, res: Response) => {
  const rubric = await rlhfCoreRubricService.getRubric();
  res.status(200).json({
    success: true,
    moduleId: "rlhf-core-rubric",
    rubric,
  });
});

export const getPreferencePairs = asyncHandler(
  async (req: Request, res: Response) => {
    const mode = String(req.query.mode || "full").toLowerCase();
    const dataset =
      mode === "candidate"
        ? await rlhfCoreRubricService.getCandidatePreferencePairDataset()
        : await rlhfCoreRubricService.getPreferencePairDataset();
    res.status(200).json({
      success: true,
      moduleId: "rlhf-core-rubric",
      mode: mode === "candidate" ? "candidate" : "full",
      dataset,
    });
  }
);

export const getPreferencePair = asyncHandler(
  async (req: Request, res: Response) => {
    const pairId = String(req.params.pairId || "");
    const result = await rlhfCoreRubricService.getPreferencePair(pairId);
    res.status(200).json({
      success: true,
      moduleId: "rlhf-core-rubric",
      ...result,
    });
  }
);

/** @deprecated Prefer validateAssessment — kept as thin alias. */
export const validateSubmission = validateAssessment;

export const updateProgress = asyncHandler(
  async (req: Request, res: Response) => {
    const userId = req.user?.id || req.body.userId;
    if (!userId) {
      throw new AppError("userId required (auth token or body)", 400);
    }

    const { lessonSlug, completed } = req.body || {};
    const progress = await rlhfCoreRubricService.updateProgress({
      userId: String(userId),
      lessonSlug: lessonSlug ? String(lessonSlug) : undefined,
      completed: Boolean(completed),
    });

    res.status(200).json({
      success: true,
      message: "Module progress updated",
      progress,
    });
  }
);

type SupervisorTelemetryBucket = {
  totalPairsReviewed: number;
  avgElapsedSeconds: number;
  elapsedSamples: number;
  deltasAccumulator: Record<string, number>;
  speedrunFlags: number;
  hardFailSafety: number;
  gradedAttempts: number;
  passedAttempts: number;
};

/**
 * Supervisor Admin Data Table — joins Postgres AI profile fields with
 * streaming JSONL rater telemetry for pass rates, speedrun flags, and
 * per-dimension calibration drift.
 *
 * GET /api/v1/modules/rlhf-core-rubric/analytics
 */
export const getSupervisorAnalytics = async (
  req: Request,
  res: Response
): Promise<void> => {
  try {
    if (req.user?.role !== "ADMIN") {
      res.status(403).json({ error: "Unauthorized: Admin access required" });
      return;
    }

    const logFilePath = path.join(
      process.cwd(),
      "logs",
      "rlhf-telemetry",
      "rlhf-rater-telemetry.jsonl"
    );

    // 1. Parse the Telemetry Log File row-by-row safely if it exists
    const telemetrySummary: Record<string, SupervisorTelemetryBucket> = {};

    const ensureBucket = (userId: string): SupervisorTelemetryBucket => {
      if (!telemetrySummary[userId]) {
        telemetrySummary[userId] = {
          totalPairsReviewed: 0,
          avgElapsedSeconds: 0,
          elapsedSamples: 0,
          deltasAccumulator: {},
          speedrunFlags: 0,
          hardFailSafety: 0,
          gradedAttempts: 0,
          passedAttempts: 0,
        };
      }
      return telemetrySummary[userId];
    };

    const accumulateSideDeltas = (
      bucket: SupervisorTelemetryBucket,
      sideDeltas: Record<string, number> | undefined
    ) => {
      if (!sideDeltas || typeof sideDeltas !== "object") return;
      Object.entries(sideDeltas).forEach(([dim, val]) => {
        if (typeof val !== "number" || !Number.isFinite(val)) return;
        const key = aliasDimension(dim);
        if (!bucket.deltasAccumulator[key]) bucket.deltasAccumulator[key] = 0;
        bucket.deltasAccumulator[key] += Math.abs(val);
      });
    };

    if (fs.existsSync(logFilePath)) {
      const fileStream = fs.createReadStream(logFilePath);
      const rl = readline.createInterface({
        input: fileStream,
        crlfDelay: Infinity,
      });

      for await (const line of rl) {
        if (!line.trim()) continue;
        try {
          const entry = JSON.parse(line);
          const userId = entry?.userId;
          if (!userId || typeof userId !== "string") continue;

          const userStats = ensureBucket(userId);
          const elapsed =
            typeof entry.elapsedSeconds === "number" &&
            Number.isFinite(entry.elapsedSeconds)
              ? entry.elapsedSeconds
              : null;

          if (elapsed !== null) {
            userStats.elapsedSamples += 1;
            userStats.avgElapsedSeconds =
              (userStats.avgElapsedSeconds * (userStats.elapsedSamples - 1) +
                elapsed) /
              userStats.elapsedSamples;
          }

          const eventType = entry.eventType as string | undefined;

          if (eventType === "HARD_FAIL_INSUFFICIENT_TIME") {
            userStats.speedrunFlags += 1;
          } else if (
            elapsed !== null &&
            elapsed < 45 &&
            (eventType === "VALIDATION_GRADED" ||
              eventType === "HARD_FAIL_SAFETY")
          ) {
            // Justice: treat sub-45s graded attempts as speedrun signals too
            userStats.speedrunFlags += 1;
          }

          if (eventType === "HARD_FAIL_SAFETY") {
            userStats.hardFailSafety += 1;
          }

          if (
            eventType === "VALIDATION_GRADED" ||
            eventType === "HARD_FAIL_SAFETY"
          ) {
            userStats.gradedAttempts += 1;
            if (entry.pass === true) userStats.passedAttempts += 1;
          }

          // Native VettedME telemetry: pairDeltas[].dimensionDeltas[]
          if (Array.isArray(entry.pairDeltas)) {
            for (const pair of entry.pairDeltas) {
              userStats.totalPairsReviewed += 1;
              const dims = Array.isArray(pair?.dimensionDeltas)
                ? pair.dimensionDeltas
                : [];
              for (const d of dims) {
                const key = aliasDimension(String(d?.dimension || ""));
                const delta = Number(d?.delta);
                if (!key || !Number.isFinite(delta)) continue;
                if (!userStats.deltasAccumulator[key]) {
                  userStats.deltasAccumulator[key] = 0;
                }
                userStats.deltasAccumulator[key] += Math.abs(delta);
              }
            }
            continue;
          }

          // Legacy / alternate shape: { pairId, elapsedSeconds, deltas: { responseA, responseB } }
          if (entry.deltas?.responseA || entry.deltas?.responseB) {
            userStats.totalPairsReviewed += 1;
            accumulateSideDeltas(userStats, entry.deltas.responseA);
            accumulateSideDeltas(userStats, entry.deltas.responseB);
          }
        } catch (parseErr) {
          console.error("Skipping malformed telemetry line item:", parseErr);
        }
      }
    }

    const telemetryUserIds = Object.keys(telemetrySummary);

    // 2. Fetch live user statuses from PostgreSQL via Prisma (AI-active + telemetry hits)
    const users = await prisma.user.findMany({
      where: {
        OR: [
          { aiStatus: { not: AIStatus.NOT_STARTED } },
          { aiLastAttemptAt: { not: null } },
          ...(telemetryUserIds.length
            ? [{ id: { in: telemetryUserIds } }]
            : []),
        ],
      },
      select: {
        id: true,
        email: true,
        aiStatus: true,
        aiScore: true,
        aiFailureReason: true,
        aiLastAttemptAt: true,
      },
      orderBy: [{ aiLastAttemptAt: "desc" }, { email: "asc" }],
    });

    // Quick-lookup map for database fields indexed by user ID
    const userMap = new Map(users.map((u) => [u.id, u]));

    type ProfileRow = (typeof users)[number];
    const tableRows: ProfileRow[] = [...users];

    // Surface telemetry-only ids that somehow lack a user row (should be rare)
    for (const tid of telemetryUserIds) {
      if (!userMap.has(tid)) {
        // Justice: do not invent profile fields — emit a sparse row keyed by id
        tableRows.push({
          id: tid,
          email: `(missing-user:${tid})`,
          aiStatus: AIStatus.NOT_STARTED,
          aiScore: 0,
          aiFailureReason: null,
          aiLastAttemptAt: null,
        });
      }
    }

    // 3. Synthesize DB rows + disk telemetry into the admin data table
    const unifiedDataTable = tableRows.map((user) => {
      const logMetrics = telemetrySummary[user.id] || {
        totalPairsReviewed: 0,
        avgElapsedSeconds: 0,
        elapsedSamples: 0,
        deltasAccumulator: {} as Record<string, number>,
        speedrunFlags: 0,
        hardFailSafety: 0,
        gradedAttempts: 0,
        passedAttempts: 0,
      };

      // Averaged drift per dimension (closer to 0 = better calibration)
      // Denominator: pairs × 2 sides (A/B), matching the reference admin formula.
      const calibrationDrift: Record<string, number> = {};
      if (logMetrics.totalPairsReviewed > 0) {
        Object.entries(logMetrics.deltasAccumulator).forEach(
          ([dim, totalDrift]) => {
            calibrationDrift[dim] = Number(
              (
                Number(totalDrift) /
                (logMetrics.totalPairsReviewed * 2)
              ).toFixed(2)
            );
          }
        );
      }

      const strugglingDimensions = Object.entries(calibrationDrift)
        .filter(([, drift]) => drift > 0)
        .sort((a, b) => b[1] - a[1])
        .map(([dim]) => dim);

      return {
        id: user.id,
        email: user.email,
        status: user.aiStatus,
        score: user.aiScore,
        failureReason: user.aiFailureReason,
        lastAttemptAt: user.aiLastAttemptAt,
        metrics: {
          pairsEvaluated: logMetrics.totalPairsReviewed,
          averageTimeSeconds: Math.round(logMetrics.avgElapsedSeconds),
          averageAbsoluteDrift: calibrationDrift,
          strugglingDimensions,
          speedrunFlag: logMetrics.speedrunFlags > 0,
          speedrunCount: logMetrics.speedrunFlags,
          hardFailSafetyCount: logMetrics.hardFailSafety,
          gradedAttempts: logMetrics.gradedAttempts,
          passRate:
            logMetrics.gradedAttempts > 0
              ? Number(
                  (
                    (logMetrics.passedAttempts / logMetrics.gradedAttempts) *
                    100
                  ).toFixed(1)
                )
              : null,
        },
      };
    });

    const passed = unifiedDataTable.filter(
      (c) =>
        c.status === AIStatus.TIER1_PASSED ||
        c.status === AIStatus.TIER2_PASSED ||
        c.status === AIStatus.TIER3_PASSED ||
        c.status === AIStatus.COMPLETED
    ).length;
    const failed = unifiedDataTable.filter(
      (c) => c.status === AIStatus.FAILED
    ).length;
    const speedrunners = unifiedDataTable.filter(
      (c) => c.metrics.speedrunFlag
    ).length;

    res.status(200).json({
      timestamp: new Date().toISOString(),
      totalTrackedCandidates: unifiedDataTable.length,
      summary: {
        passed,
        failed,
        speedrunners,
        passRatePercent:
          unifiedDataTable.length > 0
            ? Number(((passed / unifiedDataTable.length) * 100).toFixed(1))
            : 0,
      },
      candidates: unifiedDataTable,
    });
  } catch (error) {
    console.error("Supervisor extraction engine failed:", error);
    logger.error("Supervisor extraction engine failed", {
      error: error instanceof Error ? error.message : String(error),
    });
    res
      .status(500)
      .json({ error: "Failed to extract administrative analytics data." });
  }
};

/**
 * POST /api/v1/modules/rlhf-core-rubric/viva/initialize
 * Boots a Tier 2 Interactive Viva session for a candidate at a physical Uromi workstation.
 * Body validated by startVivaSessionSchema (Zod) before this handler runs.
 */
export const initializeVivaSession = async (
  req: Request,
  res: Response
): Promise<void> => {
  const {
    fullName,
    email,
    phoneNumber,
    department,
    isNictmStudent,
    matricNumber,
    stationNumber,
    rollingMaeScore,
  } = req.body as {
    fullName: string;
    email: string;
    phoneNumber: string;
    department: NictmDepartment;
    isNictmStudent: boolean;
    matricNumber?: string;
    stationNumber: number;
    rollingMaeScore: number;
  };

  try {
    // 1. Ensure the physical workstation is registered and active
    let workstation = await prisma.workstation.findUnique({
      where: { stationNumber },
    });

    if (!workstation) {
      // Auto-provision workstation row layouts on first local hardware pass
      const rowMapping = stationNumber <= 15 ? "ROW_A" : "ROW_B";
      workstation = await prisma.workstation.create({
        data: {
          stationNumber,
          rowLocation: rowMapping,
          starlinkStreamId: `ugboha-starlink-pipe-${rowMapping.toLowerCase()}`,
        },
      });
    } else if (!workstation.isActive) {
      res.status(409).json({
        status: "error",
        message: `Station ${stationNumber} is inactive on the Ugboha Road grid.`,
      });
      return;
    }

    // 2. Upsert candidate linked to NICTM academic footprint
    const candidate = await prisma.candidate.upsert({
      where: { email },
      update: {
        fullName,
        phoneNumber,
        department,
        isNictmStudent: Boolean(isNictmStudent),
        matricNumber: matricNumber ?? null,
        currentTier: EvaluationTier.TIER_2_INTERACTIVE_VIVA,
      },
      create: {
        fullName,
        email,
        phoneNumber,
        department,
        isNictmStudent: isNictmStudent ?? true,
        matricNumber: matricNumber ?? null,
        currentTier: EvaluationTier.TIER_2_INTERACTIVE_VIVA,
      },
    });

    // 3. Placeholder evaluation record for this dynamic session
    const evaluation = await prisma.candidateEvaluation.create({
      data: {
        candidateId: candidate.id,
        workstationId: workstation.id,
        rollingMaeScore,
        defenseScore: 0.0,
        logicalConsistency: 0.0,
        aiAuditorTranscript: {
          sessionState: "INITIALIZED",
          systemCalibration: `AUDITOR_TARGET_${department}`,
          history: [],
        },
      },
    });

    logger.info("Uromi viva session initialized", {
      candidateId: candidate.id,
      evaluationId: evaluation.id,
      stationNumber,
      department,
    });

    res.status(201).json({
      status: "success",
      message: `Trust pipeline session initialized securely at Station ${stationNumber} on Ugboha Road.`,
      data: {
        candidateId: candidate.id,
        evaluationId: evaluation.id,
        streamRoute: workstation.starlinkStreamId,
      },
    });
  } catch (error: any) {
    console.error("Uromi Infrastructure Log Error: ", error);
    logger.error("Uromi viva initialize failed", {
      error: error instanceof Error ? error.message : String(error),
      email,
      stationNumber,
    });
    res.status(500).json({
      status: "error",
      message: "Internal infrastructure linkage failed.",
    });
  }
};

/**
 * GET /api/rlhf/viva/:id
 * ToT pulls evaluation + AI auditor transcript for sign-off review.
 */
export const getVivaEvaluationDetail = async (
  req: Request,
  res: Response
): Promise<void> => {
  const { id } = req.params;

  try {
    const evaluation = await prisma.candidateEvaluation.findUnique({
      where: { id },
      include: { candidate: true, workstation: true },
    });

    if (!evaluation) {
      res.status(404).json({
        status: "error",
        message: "Evaluation session record not found.",
      });
      return;
    }

    res.status(200).json({
      status: "success",
      data: {
        id: evaluation.id,
        fullName: evaluation.candidate.fullName,
        department: evaluation.candidate.department,
        currentTier: evaluation.candidate.currentTier,
        isCertified: evaluation.candidate.isCertified,
        rollingMaeScore: evaluation.rollingMaeScore,
        defenseScore: evaluation.defenseScore,
        logicalConsistency: evaluation.logicalConsistency,
        supervisorNotes: evaluation.supervisorNotes,
        stationNumber: evaluation.workstation.stationNumber,
        rowLocation: evaluation.workstation.rowLocation,
        starlinkStreamId: evaluation.workstation.starlinkStreamId,
        aiAuditorTranscript: evaluation.aiAuditorTranscript,
        sessionDate: evaluation.sessionDate,
        passRule: {
          defenseScoreMin: 75,
          rollingMaeScoreMax: 0.1,
        },
      },
    });
  } catch (error) {
    logger.error("Uromi viva detail fetch failed", {
      error: error instanceof Error ? error.message : String(error),
      evaluationId: id,
    });
    res.status(500).json({
      status: "error",
      message: "Failed to load evaluation transcript ledger.",
    });
  }
};

/**
 * POST /api/rlhf/viva/:id/evaluate
 * ToT audits transcript, scores defense resilience, and finalizes certification.
 * Body validated by evaluateVivaSessionSchema (Zod) before this handler runs.
 */
export const evaluateVivaSession = async (
  req: Request,
  res: Response
): Promise<void> => {
  const { id } = req.params;
  const { defenseScore, logicalConsistency, supervisorNotes } = req.body as {
    defenseScore: number;
    logicalConsistency: number;
    supervisorNotes?: string;
  };

  try {
    // 1. Update the Evaluation row with official scores and supervisor sign-off notes
    const evaluation = await prisma.candidateEvaluation.update({
      where: { id },
      data: {
        defenseScore,
        // Justice: use nullish coalesce — 0 is a valid consistency score
        logicalConsistency: logicalConsistency ?? 50.0,
        supervisorNotes: supervisorNotes ?? null,
      },
    });

    // 2. Compute if the candidate passed based on their total combined evaluation profile
    const passingThreshold = 75.0;
    const isPassing =
      defenseScore >= passingThreshold && evaluation.rollingMaeScore <= 0.1;

    // 3. If they pass, update their Master Candidate Profile status to certified
    const updatedCandidate = await prisma.candidate.update({
      where: { id: evaluation.candidateId },
      data: {
        currentTier: isPassing
          ? EvaluationTier.TIER_3_PRODUCTION_READY
          : EvaluationTier.TIER_2_INTERACTIVE_VIVA,
        isCertified: isPassing,
      },
    });

    logger.info("Uromi viva ToT evaluation submitted", {
      evaluationId: id,
      candidateId: updatedCandidate.id,
      defenseScore,
      isCertified: updatedCandidate.isCertified,
      currentTier: updatedCandidate.currentTier,
    });

    res.status(200).json({
      status: "success",
      message: isPassing
        ? `Candidate ${updatedCandidate.fullName} passed evaluation and is certified for production work!`
        : `Evaluation submitted. Candidate remains in Tier 2 for further training iterations.`,
      data: {
        candidateId: updatedCandidate.id,
        isCertified: updatedCandidate.isCertified,
        currentTier: updatedCandidate.currentTier,
        defenseScore: evaluation.defenseScore,
        logicalConsistency: evaluation.logicalConsistency,
        rollingMaeScore: evaluation.rollingMaeScore,
      },
    });
  } catch (error: any) {
    if (error?.code === "P2025") {
      res.status(404).json({
        status: "error",
        message: "Evaluation session record not found.",
      });
      return;
    }
    console.error("Evaluation Processing Error: ", error);
    logger.error("Uromi viva evaluate failed", {
      error: error instanceof Error ? error.message : String(error),
      evaluationId: id,
    });
    res.status(500).json({
      status: "error",
      message: "Failed to process session evaluation ledger entry.",
    });
  }
};

/**
 * GET /api/rlhf/analytics/live-feed
 * ToT command-center feed: workstation + candidate + latest evaluation scores.
 * Auth: ADMIN via route middleware (authenticate + requireAdmin).
 */
export const getLiveAnalyticsFeed = async (
  req: Request,
  res: Response
): Promise<void> => {
  try {
    const evaluations = await prisma.candidateEvaluation.findMany({
      include: {
        candidate: true,
        workstation: true,
      },
      orderBy: [{ sessionDate: "desc" }],
    });

    const data = evaluations.map((row) => ({
      id: row.id,
      fullName: row.candidate.fullName,
      department: row.candidate.department,
      currentTier: row.candidate.currentTier,
      rollingMaeScore: row.rollingMaeScore,
      defenseScore: row.defenseScore,
      logicalConsistency: row.logicalConsistency,
      supervisorNotes: row.supervisorNotes,
      isCertified: row.candidate.isCertified,
      stationNumber: row.workstation.stationNumber,
      rowLocation: row.workstation.rowLocation,
    }));

    res.status(200).json({
      status: "success",
      data,
      meta: {
        count: data.length,
        generatedAt: new Date().toISOString(),
      },
    });
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    logger.error("Uromi live analytics feed failed", { error: detail });
    res.status(500).json({
      status: "error",
      message: "Failed to stream analytics endpoint data matrix.",
      // Justice: surface real DB/Prisma failure so ToT desk can remediate
      detail,
      hint:
        "On Windows: npx prisma generate && npx prisma migrate deploy && npx prisma db seed, then restart backend on PORT=8080",
    });
  }
};
