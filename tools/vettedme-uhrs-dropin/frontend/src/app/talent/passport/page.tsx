"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

export default function TalentPassportIndexPage() {
  const [userId, setUserId] = useState<string | null>(null);
  const [email, setEmail] = useState<string | null>(null);

  useEffect(() => {
    setUserId(window.localStorage.getItem("vetted_user_id"));
    setEmail(window.localStorage.getItem("vetted_email"));
  }, []);

  return (
    <div className="container mx-auto max-w-3xl px-4 py-12 space-y-6">
      <div>
        <p className="text-xs uppercase tracking-[0.2em] text-blue-600">Talent</p>
        <h1 className="mt-1 text-3xl font-bold">My Passport</h1>
        <p className="mt-2 text-slate-600">
          {email ? `Account: ${email}` : "No session email in this browser."}
        </p>
      </div>

      <div className="rounded-xl border border-slate-200 bg-white p-6 space-y-4">
        <p className="text-sm text-slate-600">
          Practice passport. Full biometric / GitHub audit pages are not wired
          on slim-api. UHRS quality score lives on the simulator.
        </p>
        {userId ? (
          <Link
            href={`/talent/passport/${userId}`}
            className="inline-block rounded bg-blue-600 px-4 py-2 text-sm font-semibold text-white"
          >
            Open passport record
          </Link>
        ) : (
          <p className="text-sm text-amber-700">
            No user id in localStorage.{" "}
            <Link href="/login?next=/talent/passport" className="underline">
              Sign in again
            </Link>
            .
          </p>
        )}
        <div className="flex gap-4 text-sm">
          <Link href="/uhrs" className="text-blue-600 underline">
            UHRS simulator
          </Link>
          <Link href="/talent/dashboard" className="text-blue-600 underline">
            Dashboard
          </Link>
        </div>
      </div>
    </div>
  );
}
