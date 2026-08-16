"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

export default function TalentDashboardPage() {
  const [email, setEmail] = useState<string | null>(null);

  useEffect(() => {
    setEmail(window.localStorage.getItem("vetted_email"));
  }, []);

  return (
    <div className="container mx-auto max-w-4xl px-4 py-12 space-y-8">
      <div>
        <p className="text-xs uppercase tracking-[0.2em] text-blue-600">Talent</p>
        <h1 className="mt-1 text-3xl font-bold">Dashboard</h1>
        <p className="mt-2 text-slate-600">
          {email ? `Signed in as ${email}` : "Practice workspace"}
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <HubCard
          href="/uhrs"
          title="UHRS / Tokoka simulator"
          body="Quality score, hidden controls, speed strikes, teaching ban."
        />
        <HubCard
          href="/talent/assessment"
          title="Assessments"
          body="RLHF rubric, code lab, and viva terminals."
        />
        <HubCard
          href="/talent/passport"
          title="My Passport"
          body="Trust passport snapshot for this practice account."
        />
        <HubCard
          href="/viva"
          title="Viva terminal"
          body="Tier 2 viva session (needs full API, not slim-api)."
        />
      </div>
    </div>
  );
}

function HubCard({
  href,
  title,
  body,
}: {
  href: string;
  title: string;
  body: string;
}) {
  return (
    <Link
      href={href}
      className="rounded-xl border border-slate-200 bg-white p-6 hover:border-blue-500 hover:shadow-md transition"
    >
      <h2 className="text-lg font-semibold text-slate-900">{title}</h2>
      <p className="mt-2 text-sm text-slate-600">{body}</p>
    </Link>
  );
}
