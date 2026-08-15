/**
 * Tokoka / UHRS simulator — spam score, control questions, speed gates.
 * Educational only: simulated ban is a teaching moment, not a real UHRS ban.
 */

import { prisma } from "../../lib/prisma";

export const UHRS_FLAG_THRESHOLD = 0.8;
export const UHRS_MIN_RESPONSE_MS = 800;
export const UHRS_MIN_INTER_SUBMIT_MS = 500;
export const UHRS_WRONG_CONTROL_PENALTY = 0.15;
export const UHRS_SPEED_PENALTY = 0.05;

export type UhrsSnapshot = {
  uhrsSpamScore: number;
  totalControlQuestions: number;
  correctControlAnswers: number;
  speedViolations: number;
  lastSubmissionTime: Date | null;
  isSimulatedBanned: boolean;
};

export type UhrsSubmitInput = {
  isControlQuestion: boolean;
  controlCorrect?: boolean;
  responseTimeMs: number;
};

export type UhrsSubmitResult = {
  snapshot: UhrsSnapshot;
  flagged: boolean;
  newlyBanned: boolean;
  speedViolation: boolean;
  teachingMoment: string | null;
};

function clampScore(n: number): number {
  return Math.max(0, Math.min(1, Math.round(n * 1000) / 1000));
}

function teachingCopy(score: number): string {
  return (
    `SIMULATED BAN (educational): Your quality score dropped to ${(score * 100).toFixed(1)}% ` +
    `(below ${(UHRS_FLAG_THRESHOLD * 100).toFixed(0)}%). In real UHRS/Tokoka work this would ` +
    `suspend the account. Slow down, read carefully, and treat hidden control questions as sacred.`
  );
}

/** Pure scoring — unit-tested without DB (Justice: no false state). */
export function computeUhrsSubmissionUpdate(
  current: UhrsSnapshot,
  input: UhrsSubmitInput,
  now: Date = new Date()
): {
  next: UhrsSnapshot;
  flagged: boolean;
  newlyBanned: boolean;
  speedViolation: boolean;
  teachingMoment: string | null;
} {
  if (current.isSimulatedBanned) {
    return {
      next: current,
      flagged: true,
      newlyBanned: false,
      speedViolation: false,
      teachingMoment: teachingCopy(current.uhrsSpamScore),
    };
  }

  let score = current.uhrsSpamScore;
  let totalControl = current.totalControlQuestions;
  let correctControl = current.correctControlAnswers;
  let speedViolations = current.speedViolations;
  let speedViolation = false;

  const tooFastResponse = input.responseTimeMs < UHRS_MIN_RESPONSE_MS;
  const tooFastGap =
    current.lastSubmissionTime != null &&
    now.getTime() - current.lastSubmissionTime.getTime() <
      UHRS_MIN_INTER_SUBMIT_MS;

  if (tooFastResponse || tooFastGap) {
    speedViolation = true;
    speedViolations += 1;
    score = clampScore(score - UHRS_SPEED_PENALTY);
  }

  if (input.isControlQuestion) {
    totalControl += 1;
    if (input.controlCorrect === true) {
      correctControl += 1;
    } else {
      score = clampScore(score - UHRS_WRONG_CONTROL_PENALTY);
    }
  }

  const flagged = score < UHRS_FLAG_THRESHOLD;
  const newlyBanned = flagged && !current.isSimulatedBanned;
  const next: UhrsSnapshot = {
    uhrsSpamScore: score,
    totalControlQuestions: totalControl,
    correctControlAnswers: correctControl,
    speedViolations,
    lastSubmissionTime: now,
    isSimulatedBanned: newlyBanned || current.isSimulatedBanned,
  };

  return {
    next,
    flagged,
    newlyBanned,
    speedViolation,
    teachingMoment: newlyBanned || next.isSimulatedBanned ? teachingCopy(score) : null,
  };
}

function toSnapshot(row: {
  uhrsSpamScore: number;
  totalControlQuestions: number;
  correctControlAnswers: number;
  speedViolations: number;
  lastSubmissionTime: Date | null;
  isSimulatedBanned: boolean;
}): UhrsSnapshot {
  return {
    uhrsSpamScore: row.uhrsSpamScore,
    totalControlQuestions: row.totalControlQuestions,
    correctControlAnswers: row.correctControlAnswers,
    speedViolations: row.speedViolations,
    lastSubmissionTime: row.lastSubmissionTime,
    isSimulatedBanned: row.isSimulatedBanned,
  };
}

const UHRS_SELECT = {
  uhrsSpamScore: true,
  totalControlQuestions: true,
  correctControlAnswers: true,
  speedViolations: true,
  lastSubmissionTime: true,
  isSimulatedBanned: true,
} as const;

export async function getUhrsStatus(userId: string): Promise<UhrsSnapshot> {
  try {
    const row = await prisma.user.findUnique({
      where: { id: userId },
      select: UHRS_SELECT,
    });
    if (!row) {
      throw Object.assign(new Error("User not found"), { statusCode: 404 });
    }
    return toSnapshot(row);
  } catch (err: any) {
    if (err?.statusCode) throw err;
    throw Object.assign(new Error(`UHRS status failed: ${err?.message || err}`), {
      statusCode: 500,
    });
  }
}

export async function submitUhrsTask(
  userId: string,
  input: UhrsSubmitInput
): Promise<UhrsSubmitResult> {
  try {
    const row = await prisma.user.findUnique({
      where: { id: userId },
      select: UHRS_SELECT,
    });
    if (!row) {
      throw Object.assign(new Error("User not found"), { statusCode: 404 });
    }

    const current = toSnapshot(row);
    if (current.isSimulatedBanned) {
      return {
        snapshot: current,
        flagged: true,
        newlyBanned: false,
        speedViolation: false,
        teachingMoment: teachingCopy(current.uhrsSpamScore),
      };
    }

    const computed = computeUhrsSubmissionUpdate(current, input, new Date());

    const updated = await prisma.user.update({
      where: { id: userId },
      data: {
        uhrsSpamScore: computed.next.uhrsSpamScore,
        totalControlQuestions: computed.next.totalControlQuestions,
        correctControlAnswers: computed.next.correctControlAnswers,
        speedViolations: computed.next.speedViolations,
        lastSubmissionTime: computed.next.lastSubmissionTime,
        isSimulatedBanned: computed.next.isSimulatedBanned,
      },
      select: UHRS_SELECT,
    });

    return {
      snapshot: toSnapshot(updated),
      flagged: computed.flagged,
      newlyBanned: computed.newlyBanned,
      speedViolation: computed.speedViolation,
      teachingMoment: computed.teachingMoment,
    };
  } catch (err: any) {
    if (err?.statusCode) throw err;
    throw Object.assign(new Error(`UHRS submit failed: ${err?.message || err}`), {
      statusCode: 500,
    });
  }
}

/** Clear simulated ban after the teaching moment (practice loop). Scores retained. */
export async function acknowledgeUhrsBan(userId: string): Promise<UhrsSnapshot> {
  try {
    const updated = await prisma.user.update({
      where: { id: userId },
      data: { isSimulatedBanned: false },
      select: UHRS_SELECT,
    });
    return toSnapshot(updated);
  } catch (err: any) {
    throw Object.assign(
      new Error(`UHRS acknowledge failed: ${err?.message || err}`),
      { statusCode: 500 }
    );
  }
}

/** Full practice reset (scores back to defaults). */
export async function resetUhrsPractice(userId: string): Promise<UhrsSnapshot> {
  try {
    const updated = await prisma.user.update({
      where: { id: userId },
      data: {
        uhrsSpamScore: 1.0,
        totalControlQuestions: 0,
        correctControlAnswers: 0,
        speedViolations: 0,
        lastSubmissionTime: null,
        isSimulatedBanned: false,
      },
      select: UHRS_SELECT,
    });
    return toSnapshot(updated);
  } catch (err: any) {
    throw Object.assign(new Error(`UHRS reset failed: ${err?.message || err}`), {
      statusCode: 500,
    });
  }
}
