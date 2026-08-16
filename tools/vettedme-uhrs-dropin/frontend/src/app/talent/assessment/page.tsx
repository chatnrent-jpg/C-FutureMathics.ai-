import Link from "next/link";

const TRACKS = [
  {
    href: "/talent/assessment/rlhf",
    title: "RLHF core rubric",
    body: "Preference pairs and four-dimension scoring.",
  },
  {
    href: "/uhrs",
    title: "UHRS / Tokoka quality sim",
    body: "Spam score, control questions, speed gates.",
  },
  {
    href: "/talent/assessment/code-lab",
    title: "Sandboxed code lab",
    body: "Tier 2 coding challenge (placeholder lab).",
  },
  {
    href: "/talent/assessment/viva",
    title: "AI viva",
    body: "Tier 3 oral defense terminal.",
  },
];

export default function TalentAssessmentIndexPage() {
  return (
    <div className="container mx-auto max-w-4xl px-4 py-12 space-y-8">
      <div>
        <p className="text-xs uppercase tracking-[0.2em] text-blue-600">Talent</p>
        <h1 className="mt-1 text-3xl font-bold">Assessment</h1>
        <p className="mt-2 text-slate-600">
          Pick a track. UHRS works on slim-api; viva needs the full backend.
        </p>
      </div>
      <div className="grid gap-4">
        {TRACKS.map((t) => (
          <Link
            key={t.href}
            href={t.href}
            className="rounded-xl border border-slate-200 bg-white p-6 hover:border-blue-500 hover:shadow-md transition"
          >
            <h2 className="text-lg font-semibold">{t.title}</h2>
            <p className="mt-1 text-sm text-slate-600">{t.body}</p>
          </Link>
        ))}
      </div>
    </div>
  );
}
