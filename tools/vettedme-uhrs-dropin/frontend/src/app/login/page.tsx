"use client";

import { FormEvent, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";

type Mode = "login" | "register";

function AuthForm() {
  const router = useRouter();
  const search = useSearchParams();
  const nextPath = search.get("next") || "/uhrs";

  const [mode, setMode] = useState<Mode>("register");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [firstName, setFirstName] = useState("UHRS");
  const [lastName, setLastName] = useState("Practice");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const title = useMemo(
    () => (mode === "login" ? "Sign in" : "Create practice account"),
    [mode]
  );

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const path = mode === "login" ? "/api/auth/login" : "/api/auth/register";
      const body =
        mode === "login"
          ? { email: email.trim(), password }
          : {
              email: email.trim(),
              password,
              firstName: firstName.trim() || "UHRS",
              lastName: lastName.trim() || "Practice",
              role: "TALENT",
            };

      const res = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const json = await res.json().catch(() => ({}));
      if (!res.ok || !json.token) {
        throw new Error(
          json.error ||
            json.message ||
            `${mode} failed (${res.status}). Is API on :8080?`
        );
      }

      window.localStorage.setItem("vetted_token", String(json.token));
      window.localStorage.setItem(
        "vetted_user_id",
        String(json.user?.id || "")
      );
      window.localStorage.setItem(
        "vetted_email",
        String(json.user?.email || email.trim())
      );

      router.push(nextPath);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto w-full max-w-md space-y-6">
      <div>
        <p className="text-xs uppercase tracking-[0.2em] text-blue-600">
          VettedME
        </p>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight text-slate-900">
          {title}
        </h1>
        <p className="mt-2 text-sm text-slate-600">
          Needed for UHRS / viva practice. Tokens stay in this browser only.
        </p>
      </div>

      <div className="flex gap-2 text-sm">
        <button
          type="button"
          className={`rounded px-3 py-1.5 ${
            mode === "register"
              ? "bg-slate-900 text-white"
              : "bg-slate-200 text-slate-700"
          }`}
          onClick={() => setMode("register")}
        >
          Register
        </button>
        <button
          type="button"
          className={`rounded px-3 py-1.5 ${
            mode === "login"
              ? "bg-slate-900 text-white"
              : "bg-slate-200 text-slate-700"
          }`}
          onClick={() => setMode("login")}
        >
          Login
        </button>
      </div>

      <form onSubmit={onSubmit} className="space-y-3">
        {mode === "register" && (
          <div className="grid grid-cols-2 gap-2">
            <label className="block text-sm">
              <span className="text-slate-600">First name</span>
              <input
                className="mt-1 w-full rounded border border-slate-300 px-3 py-2"
                value={firstName}
                onChange={(e) => setFirstName(e.target.value)}
                required
              />
            </label>
            <label className="block text-sm">
              <span className="text-slate-600">Last name</span>
              <input
                className="mt-1 w-full rounded border border-slate-300 px-3 py-2"
                value={lastName}
                onChange={(e) => setLastName(e.target.value)}
                required
              />
            </label>
          </div>
        )}

        <label className="block text-sm">
          <span className="text-slate-600">Email</span>
          <input
            type="email"
            className="mt-1 w-full rounded border border-slate-300 px-3 py-2"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@example.com"
            required
          />
        </label>

        <label className="block text-sm">
          <span className="text-slate-600">Password</span>
          <input
            type="password"
            className="mt-1 w-full rounded border border-slate-300 px-3 py-2"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            minLength={8}
            required
          />
        </label>

        {error && (
          <p className="rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800">
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={busy}
          className="w-full rounded bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50"
        >
          {busy ? "Working..." : mode === "login" ? "Sign in" : "Create account"}
        </button>
      </form>

      <p className="text-xs text-slate-500">
        After sign-in you go to{" "}
        <Link className="underline" href={nextPath}>
          {nextPath}
        </Link>
        . Admin Command Center:{" "}
        <Link className="underline" href="/analytics">
          /analytics
        </Link>
        .
      </p>
    </div>
  );
}

export default function LoginPage() {
  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-slate-200">
      <header className="border-b border-slate-200 bg-white/80">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-4 py-4">
          <Link href="/" className="font-semibold text-slate-900">
            VETTED
          </Link>
          <Link href="/uhrs" className="text-sm text-slate-600 hover:text-slate-900">
            UHRS Sim
          </Link>
        </div>
      </header>
      <main className="mx-auto flex max-w-5xl justify-center px-4 py-16">
        <Suspense fallback={<p className="text-sm text-slate-600">Loading...</p>}>
          <AuthForm />
        </Suspense>
      </main>
    </div>
  );
}
