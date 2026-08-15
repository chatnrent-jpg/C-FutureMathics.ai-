/**
 * Unit tests for Tokoka / UHRS scoring (no DB).
 * Usage: npx tsx tests/unit/uhrs-scoring.test.ts
 */
import {
  UHRS_FLAG_THRESHOLD,
  UHRS_MIN_RESPONSE_MS,
  computeUhrsSubmissionUpdate,
  type UhrsSnapshot,
} from "../../src/modules/rlhf-core-rubric/uhrsService";

function assert(cond: unknown, msg: string): asserts cond {
  if (!cond) throw new Error(msg);
}

const fresh = (): UhrsSnapshot => ({
  uhrsSpamScore: 1.0,
  totalControlQuestions: 0,
  correctControlAnswers: 0,
  speedViolations: 0,
  lastSubmissionTime: null,
  isSimulatedBanned: false,
});

function main() {
  // Correct control, slow enough — no penalty
  {
    const r = computeUhrsSubmissionUpdate(fresh(), {
      isControlQuestion: true,
      controlCorrect: true,
      responseTimeMs: 2000,
    });
    assert(r.next.uhrsSpamScore === 1.0, "correct control should not penalize");
    assert(r.next.totalControlQuestions === 1, "control count");
    assert(r.next.correctControlAnswers === 1, "correct count");
    assert(r.newlyBanned === false, "should not ban");
    console.log("✓ correct control preserves score");
  }

  // Wrong control penalizes
  {
    const r = computeUhrsSubmissionUpdate(fresh(), {
      isControlQuestion: true,
      controlCorrect: false,
      responseTimeMs: 2000,
    });
    assert(r.next.uhrsSpamScore === 0.85, `expected 0.85 got ${r.next.uhrsSpamScore}`);
    assert(r.flagged === false, "0.85 still above flag threshold");
    console.log("✓ wrong control applies penalty");
  }

  // Speed violation
  {
    const r = computeUhrsSubmissionUpdate(fresh(), {
      isControlQuestion: false,
      responseTimeMs: UHRS_MIN_RESPONSE_MS - 1,
    });
    assert(r.speedViolation === true, "expected speed violation");
    assert(r.next.speedViolations === 1, "speed count");
    assert(r.next.uhrsSpamScore === 0.95, `expected 0.95 got ${r.next.uhrsSpamScore}`);
    console.log("✓ hyper-fast response flagged");
  }

  // Cascading wrong controls → simulated ban
  {
    let state = fresh();
    const t0 = new Date("2026-08-15T12:00:00.000Z");
    let last = computeUhrsSubmissionUpdate(
      state,
      {
        isControlQuestion: true,
        controlCorrect: false,
        responseTimeMs: 2000,
      },
      t0
    );
    // 1.0 -> 0.85 -> 0.70 (banned)
    state = last.next;
    last = computeUhrsSubmissionUpdate(
      state,
      {
        isControlQuestion: true,
        controlCorrect: false,
        responseTimeMs: 2000,
      },
      new Date(t0.getTime() + 10_000)
    );
    assert(last.next.uhrsSpamScore < UHRS_FLAG_THRESHOLD, "score under threshold");
    assert(last.newlyBanned === true, "should newly ban");
    assert(last.next.isSimulatedBanned === true, "ban latch");
    assert(typeof last.teachingMoment === "string" && last.teachingMoment.length > 20, "teaching copy");
    console.log("✓ cascading fails trigger simulated ban + teaching moment");
  }

  // Already banned is sticky / no further mutation path for counts from compute when banned at start
  {
    const banned: UhrsSnapshot = {
      ...fresh(),
      uhrsSpamScore: 0.7,
      isSimulatedBanned: true,
    };
    const r = computeUhrsSubmissionUpdate(banned, {
      isControlQuestion: true,
      controlCorrect: false,
      responseTimeMs: 100,
    });
    assert(r.next.totalControlQuestions === 0, "banned state should not mutate counts");
    assert(r.teachingMoment, "teaching moment while banned");
    console.log("✓ banned state returns teaching moment without further damage");
  }

  console.log("ALL UHRS SCORING TESTS PASSED");
}

main();
