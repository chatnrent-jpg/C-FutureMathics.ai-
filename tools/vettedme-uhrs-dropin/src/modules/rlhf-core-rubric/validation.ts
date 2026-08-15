import { z } from "zod";

// Define the schema structure for a single response's 4 dimensions
const dimensionScoresSchema = z.object({
  helpfulness: z.number().int().min(1).max(5),
  factuality: z.number().int().min(1).max(5),
  safety: z.number().int().min(1).max(5),
  tone: z.number().int().min(1).max(5),
});

// Define the schema for a single evaluation pair answer block
const pairAnswerSchema = z.object({
  // Enums updated to support tie and both_bad variations
  chosenLabel: z.enum(["prefer_a", "prefer_b", "tie", "both_bad"]),
  scoresA: dimensionScoresSchema,
  scoresB: dimensionScoresSchema,
});

// Master evaluation payload schema validation map
export const validateAssessmentSchema = z.object({
  body: z.object({
    userId: z.string().uuid({ message: "Invalid user identifier format." }),
    evaluationStartedAt: z
      .string()
      .datetime({ message: "Invalid start timestamp format." }),
    answers: z.record(z.string(), pairAnswerSchema),
  }),
});

export type ValidateAssessmentBody = z.infer<
  typeof validateAssessmentSchema
>["body"];

/** Parse controller-style answers payload (rejects before DB work). */
export function parseAssessmentBody(body: unknown): ValidateAssessmentBody {
  const parsed = validateAssessmentSchema.safeParse({ body });
  if (!parsed.success) {
    const issue = parsed.error.issues[0];
    const path = issue?.path?.filter((p) => p !== "body").join(".") || "body";
    throw new Error(`${path}: ${issue?.message || "invalid payload"}`);
  }
  return parsed.data.body;
}

// ---------------------------------------------------------------------------
// Legacy / service-path schemas (rubric dimension keys + submissions[])
// ---------------------------------------------------------------------------

/** Exactly the four Core Rubric dimensions — extras rejected via .strict(). */
export const dimensionScoreSchema = z
  .object({
    helpfulnessDirectness: z.number().int().min(1).max(5),
    truthfulnessFactuality: z.number().int().min(1).max(5),
    harmMitigationSafety: z.number().int().min(1).max(5),
    toneFormatting: z.number().int().min(1).max(5),
  })
  .strict();

export const pairSubmissionSchema = z
  .object({
    pairId: z.string().min(1),
    label: z.enum([
      "prefer_a",
      "prefer_b",
      "tie",
      "both_bad",
      "invalid_prompt",
    ]),
    scores: z
      .object({
        A: dimensionScoreSchema,
        B: dimensionScoreSchema,
      })
      .strict(),
    /** Client read-receipts — both must be true (anti-cheat). */
    readReceipts: z
      .object({
        readA: z.literal(true),
        readB: z.literal(true),
      })
      .strict(),
  })
  .strict();

export const validateSubmissionSchema = z
  .object({
    userId: z.string().uuid().optional(),
    /** ISO timestamp when candidate opened the evaluation workspace */
    evaluationStartedAt: z.string().datetime(),
    submissions: z.array(pairSubmissionSchema).min(1),
  })
  .strict();

export type ValidatedSubmissionPayload = z.infer<
  typeof validateSubmissionSchema
>;

export const MIN_EVALUATION_SECONDS = 45;
export const INSUFFICIENT_TIME_REASON = "INSUFFICIENT_EVALUATION_TIME";

export function parseValidatePayload(
  body: unknown
): ValidatedSubmissionPayload {
  const parsed = validateSubmissionSchema.safeParse(body);
  if (!parsed.success) {
    const issue = parsed.error.issues[0];
    const path = issue?.path?.join(".") || "payload";
    throw new Error(`${path}: ${issue?.message || "invalid payload"}`);
  }
  return parsed.data;
}

export function evaluateTimingGate(
  evaluationStartedAt: string,
  now = new Date()
) {
  const started = new Date(evaluationStartedAt);
  if (Number.isNaN(started.getTime())) {
    throw new Error("evaluationStartedAt: invalid datetime");
  }
  const elapsedMs = now.getTime() - started.getTime();
  const elapsedSeconds = elapsedMs / 1000;
  // Reject future clocks / absurd negative durations
  if (elapsedSeconds < 0) {
    throw new Error("evaluationStartedAt: cannot be in the future");
  }
  return {
    elapsedSeconds: Number(elapsedSeconds.toFixed(2)),
    insufficientTime: elapsedSeconds < MIN_EVALUATION_SECONDS,
    minRequiredSeconds: MIN_EVALUATION_SECONDS,
  };
}

// ---------------------------------------------------------------------------
// NICTM / Uromi — candidate ingestion & Tier 2 viva session start
// ---------------------------------------------------------------------------

/** NICTM department enum — keep in lockstep with Prisma `NictmDepartment`. */
export const NictmDepartmentSchema = z.enum([
  "MECHATRONICS_ENGINEERING",
  "COMPUTER_SCIENCE",
  "CYBER_SECURITY_DATA_PROTECTION",
  "CIVIL_ENGINEERING",
  "QUANTITY_SURVEYING",
  "BUILDING_TECHNOLOGY",
  "EXTERNAL_TALENT",
]);

export type NictmDepartment = z.infer<typeof NictmDepartmentSchema>;

/**
 * Request validation for student onboarding / starting a Tier 2 viva session.
 * Zod middleware expects `{ body: req.body }` (same pattern as validateAssessmentSchema).
 */
export const startVivaSessionSchema = z.object({
  body: z.object({
    fullName: z.string().min(3, "Full name must be at least 3 characters long"),
    email: z.string().email("Invalid email address format"),
    phoneNumber: z.string().min(10, "Phone number must be valid"),
    department: NictmDepartmentSchema,
    isNictmStudent: z.boolean().default(true),
    matricNumber: z.string().optional(),
    stationNumber: z.number().int().positive("Station number must be valid"),
    rollingMaeScore: z.number().min(0, "MAE score cannot be negative"),
  }),
});

export type StartVivaSessionInput = z.infer<typeof startVivaSessionSchema>;
export type StartVivaSessionBody = StartVivaSessionInput["body"];

/** Parse viva start payload (rejects before DB / session work). */
export function parseStartVivaSessionBody(body: unknown): StartVivaSessionBody {
  const parsed = startVivaSessionSchema.safeParse({ body });
  if (!parsed.success) {
    const issue = parsed.error.issues[0];
    const path = issue?.path?.filter((p) => p !== "body").join(".") || "body";
    throw new Error(`${path}: ${issue?.message || "invalid payload"}`);
  }
  return parsed.data.body;
}

/**
 * ToT sign-off: grade Tier 2 viva defense and optionally certify for production.
 * Zod middleware expects `{ body, params }` (same pattern as other RLHF validators).
 */
export const evaluateVivaSessionSchema = z.object({
  body: z.object({
    defenseScore: z
      .number()
      .min(0, "Defense score must be a number between 0 and 100.")
      .max(100, "Defense score must be a number between 0 and 100."),
    logicalConsistency: z
      .number()
      .min(0, "Logical consistency must be between 0 and 100.")
      .max(100, "Logical consistency must be between 0 and 100.")
      .optional()
      .default(50),
    supervisorNotes: z.string().optional(),
  }),
  params: z.object({
    id: z.string().uuid("Evaluation id must be a valid UUID"),
  }),
});

export type EvaluateVivaSessionInput = z.infer<typeof evaluateVivaSessionSchema>;
export type EvaluateVivaSessionBody = EvaluateVivaSessionInput["body"];

// ---------------------------------------------------------------------------
// Tokoka / UHRS simulator
// ---------------------------------------------------------------------------

export const submitUhrsTaskSchema = z.object({
  body: z
    .object({
      isControlQuestion: z.boolean(),
      controlCorrect: z.boolean().optional(),
      responseTimeMs: z
        .number()
        .finite()
        .min(0, "responseTimeMs must be >= 0")
        .max(3_600_000, "responseTimeMs unrealistically large"),
    })
    .superRefine((body, ctx) => {
      if (body.isControlQuestion && body.controlCorrect === undefined) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["controlCorrect"],
          message: "controlCorrect is required when isControlQuestion is true",
        });
      }
    }),
});

export type SubmitUhrsTaskBody = z.infer<typeof submitUhrsTaskSchema>["body"];
