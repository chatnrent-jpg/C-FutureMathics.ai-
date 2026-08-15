"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ensureAssessmentSession } from "@/lib/rlhfApi";
import {
  acknowledgeUhrsBan,
  fetchUhrsStatus,
  resetUhrsPractice,
  submitUhrsTask,
  type UhrsSnapshot,
} from "@/lib/uhrsApi";

type TaskChoice = "safe" | "unsafe" | "unclear";

type SimTask = {
  id: string;
  prompt: string;
  isControl: boolean;
  /** Known-correct choice for control questions only */
  correctChoice?: TaskChoice;
};

const TASK_BANK: SimTask[] = [
  {
    id: "t1",
    prompt: "User asks how to reset a forgotten Gmail password through official Google account recovery.",
    isControl: false,
  },
  {
    id: "t2",
    prompt: "User asks for step-by-step instructions to build a pipe bomb from household chemicals.",
    isControl: true,
    correctChoice: "unsafe",
  },
  {
    id: "t3",
    prompt: "User asks whether Lagos traffic apps work offline and wants a short factual answer.",
    isControl: false,
  },
  {
    id: "t4",
    prompt: "User asks you to invent medical dosage advice for a child with fever, claiming it is urgent.",
    isControl: true,
    correctChoice: "unsafe",
  },
  {
    id: "t5",
    prompt: "User wants a polite email declining a meeting due to a scheduling conflict.",
    isControl: false,
  },
  {
    id: "t6",
    prompt: "User asks for the capital of Canada.",
    isControl: true,
    correctChoice: "safe",
  },
  {
    id: "t7",
    prompt: "User asks how to socially engineer a bank teller into revealing another customer’s balance.",
    isControl: true,
    correctChoice: "unsafe",
  },
  {
    id: "t8",
    prompt: "User asks for a comparison of TypeScript vs JavaScript for a junior engineer.",
    isControl: false,
  },
];

function shuffle<T>(arr: T[]): T[] {
  const copy = [...arr];
  for (let i = copy.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    [copy[i], copy[j]] = [copy[j], copy[i]];
  }
  return copy;
}

function scorePct(score: number): string {
  return `${(score * 100).toFixed(1)}%`;
}

export default function UhrsSimulatorPage() {
  const [token, setToken] = useState<string | null>(null);
  const [snapshot, setSnapshot] = useState<UhrsSnapshot | null>(null);
  const [queue, setQueue] = useState<SimTask[]>([]);
  const [index, setIndex] = useState(0);
  const [shownAt, setShownAt] = useState(() => Date.now());
  const [busy, setBusy] = useState(false);
  const [bootError, setBootError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [teachingMoment, setTeachingMoment] = useState<string | null>(null);

  const task = queue[index] || null;

  const boot = useCallback(async () => {
    setBootError(null);
    try {
      const session = await ensureAssessmentSession();
      setToken(session.token);
      const status = await fetchUhrsStatus(session.token);
      setSnapshot(status);
      if (status.isSimulatedBanned) {
        setTeachingMoment(
          `SIMULATED BAN active — score ${scorePct(status.uhrsSpamScore)}. Acknowledge to continue practice.`
        );
      }
      setQueue(shuffle(TASK_BANK));
      setIndex(0);
      setShownAt(Date.now());
    } catch (err: any) {
      setBootError(err?.message || "Failed to start UHRS session");
    }
  }, []);

  useEffect(() => {
    void boot();
  }, [boot]);

  const metrics = useMemo(() => {
    if (!snapshot) return null;
    return {
      score: scorePct(snapshot.uhrsSpamScore),
      flagged: snapshot.uhrsSpamScore < 0.8 || Boolean(snapshot.flagged),
      controls: `${snapshot.correctControlAnswers}/${snapshot.totalControlQuestions}`,
      speed: snapshot.speedViolations,
      banned: snapshot.isSimulatedBanned,
    };
  }, [snapshot]);

  async function onChoose(choice: TaskChoice) {
    if (!token || !task || busy || snapshot?.isSimulatedBanned) return;
    setBusy(true);
    setFlash(null);
    const responseTimeMs = Math.max(0, Date.now() - shownAt);
    try {
      const payload = {
        isControlQuestion: task.isControl,
        responseTimeMs,
        ...(task.isControl
          ? { controlCorrect: choice === task.correctChoice }
          : {}),
      };
      const result = await submitUhrsTask(token, payload);
      setSnapshot(result.snapshot);
      if (result.speedViolation) {
        setFlash("Speed strike — slow down. Real UHRS flags hyper-fast clicks.");
      } else if (task.isControl && payload.controlCorrect === false) {
        setFlash("Control miss — hidden quality check failed.");
      } else if (task.isControl) {
        setFlash("Control passed.");
      } else {
        setFlash("Submitted.");
      }
      if (result.teachingMoment) {
        setTeachingMoment(result.teachingMoment);
      }
      if (!result.snapshot.isSimulatedBanned) {
        const next = index + 1;
        if (next >= queue.length) {
          setQueue(shuffle(TASK_BANK));
          setIndex(0);
        } else {
          setIndex(next);
        }
        setShownAt(Date.now());
      }
    } catch (err: any) {
      setFlash(err?.message || "Submit failed");
    } finally {
      setBusy(false);
    }
  }

  async function onAcknowledge() {
    if (!token || busy) return;
    setBusy(true);
    try {
      const next = await acknowledgeUhrsBan(token);
      setSnapshot(next);
      setTeachingMoment(null);
      setFlash("Ban cleared — continue carefully.");
      setShownAt(Date.now());
    } catch (err: any) {
      setFlash(err?.message || "Acknowledge failed");
    } finally {
      setBusy(false);
    }
  }

  async function onReset() {
    if (!token || busy) return;
    setBusy(true);
    try {
      const next = await resetUhrsPractice(token);
      setSnapshot(next);
      setTeachingMoment(null);
      setQueue(shuffle(TASK_BANK));
      setIndex(0);
      setShownAt(Date.now());
      setFlash("Practice reset to 100%.");
    } catch (err: any) {
      setFlash(err?.message || "Reset failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="border-b border-slate-800 bg-slate-900/80">
        <div className="mx-auto flex max-w-4xl items-center justify-between px-4 py-4">
          <div>
            <p className="text-xs uppercase tracking-[0.2em] text-amber-500/90">
              Tokoka / UHRS
            </p>
            <h1 className="text-xl font-semibold tracking-tight">
              Quality Simulator
            </h1>
          </div>
          <nav className="flex gap-4 text-sm text-slate-400">
            <Link href="/login?next=/uhrs" className="hover:text-white">
              Login
            </Link>
            <Link href="/viva" className="hover:text-white">
              Viva
            </Link>
            <Link href="/analytics" className="hover:text-white">
              Command Center
            </Link>
            <Link href="/talent/assessment/rlhf" className="hover:text-white">
              RLHF
            </Link>
          </nav>
        </div>
      </header>

      <main className="mx-auto max-w-4xl space-y-6 px-4 py-8">
        <p className="max-w-2xl text-sm leading-relaxed text-slate-400">
          Practice UHRS-style judgment. Hidden control questions and speed
          checks affect your spam score. Drop below 80% and you hit an
          educational simulated ban — not a real account suspension.
        </p>

        {bootError && (
          <div className="space-y-2 rounded border border-red-500/40 bg-red-950/40 px-4 py-3 text-sm text-red-200">
            <p>{bootError}</p>
            <div className="flex flex-wrap gap-3">
              <button
                type="button"
                className="underline"
                onClick={() => void boot()}
              >
                Retry
              </button>
              <Link href="/login?next=/uhrs" className="underline">
                Login / Register
              </Link>
            </div>
          </div>
        )}

        {!bootError && !snapshot && (
          <p className="text-sm text-slate-400">
            Starting practice session... If this hangs,{" "}
            <Link href="/login?next=/uhrs" className="underline text-slate-200">
              create an account
            </Link>
            .
          </p>
        )}

        {metrics && (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Metric label="Spam score" value={metrics.score} warn={metrics.flagged} />
            <Metric label="Controls" value={metrics.controls} />
            <Metric label="Speed strikes" value={String(metrics.speed)} warn={metrics.speed > 0} />
            <Metric
              label="Status"
              value={metrics.banned ? "SIM BAN" : metrics.flagged ? "FLAGGED" : "CLEAR"}
              warn={metrics.banned || metrics.flagged}
            />
          </div>
        )}

        {teachingMoment && (
          <div className="space-y-3 rounded border border-amber-500/50 bg-amber-950/30 px-4 py-4">
            <p className="text-sm font-medium text-amber-200">Teaching moment</p>
            <p className="text-sm leading-relaxed text-amber-100/90">{teachingMoment}</p>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                disabled={busy}
                onClick={() => void onAcknowledge()}
                className="rounded bg-amber-500 px-3 py-2 text-sm font-semibold text-slate-950 disabled:opacity-50"
              >
                I understand — continue
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => void onReset()}
                className="rounded border border-slate-600 px-3 py-2 text-sm text-slate-200 disabled:opacity-50"
              >
                Full reset
              </button>
            </div>
          </div>
        )}

        {flash && (
          <p className="text-sm text-slate-300" role="status">
            {flash}
          </p>
        )}

        {task && !snapshot?.isSimulatedBanned && (
          <section className="space-y-5 rounded-lg border border-slate-800 bg-slate-900 p-5">
            <div className="flex items-center justify-between gap-3 text-xs text-slate-500">
              <span>
                Task {index + 1} / {queue.length || 1}
              </span>
              <span>Classify the request</span>
            </div>
            <p className="text-base leading-relaxed text-slate-100">{task.prompt}</p>
            <div className="grid gap-2 sm:grid-cols-3">
              {(
                [
                  ["safe", "Safe to answer"],
                  ["unsafe", "Unsafe / refuse"],
                  ["unclear", "Needs clarification"],
                ] as const
              ).map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  disabled={busy}
                  onClick={() => void onChoose(value)}
                  className="rounded border border-slate-700 bg-slate-950 px-3 py-3 text-sm font-medium text-slate-100 transition hover:border-amber-500/60 hover:bg-slate-900 disabled:opacity-50"
                >
                  {label}
                </button>
              ))}
            </div>
            <p className="text-xs text-slate-500">
              Tip: rushing (&lt;800ms) or machine-gun clicks adds speed strikes.
            </p>
          </section>
        )}

        {!task && !bootError && (
          <p className="text-sm text-slate-400">Loading task queue…</p>
        )}
      </main>
    </div>
  );
}

function Metric({
  label,
  value,
  warn,
}: {
  label: string;
  value: string;
  warn?: boolean;
}) {
  return (
    <div className="rounded border border-slate-800 bg-slate-900 px-3 py-3">
      <p className="text-[11px] uppercase tracking-wide text-slate-500">{label}</p>
      <p className={`mt-1 text-lg font-semibold ${warn ? "text-amber-400" : "text-slate-100"}`}>
        {value}
      </p>
    </div>
  );
}
