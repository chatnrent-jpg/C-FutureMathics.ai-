/**
 * DB diagnostics for local VettedME / UHRS development.
 *
 * Usage (from repo root):
 *   npx tsx scripts/check-db.ts
 *   npm run check:db
 *
 * Justice: never prints secrets; rejects missing/malformed DATABASE_URL;
 * every IO path uses explicit try/catch with a clear fail reason.
 */

import dotenv from "dotenv";
import { Pool } from "pg";
import { PrismaPg } from "@prisma/adapter-pg";
import { PrismaClient } from "@prisma/client";

dotenv.config({ override: true });

const UHRS_COLUMNS = [
  "uhrsSpamScore",
  "totalControlQuestions",
  "correctControlAnswers",
  "speedViolations",
  "lastSubmissionTime",
  "isSimulatedBanned",
] as const;

type CheckResult = { name: string; ok: boolean; detail: string };

function redactDatabaseUrl(raw: string): string {
  try {
    const u = new URL(raw);
    const db = (u.pathname || "/").replace(/^\//, "") || "(none)";
    return `${u.protocol}//${u.hostname}:${u.port || "5432"}/${db}`;
  } catch {
    return "(unparseable DATABASE_URL)";
  }
}

function printResult(r: CheckResult): void {
  const mark = r.ok ? "OK  " : "FAIL";
  console.log(`[${mark}] ${r.name}: ${r.detail}`);
}

async function main(): Promise<number> {
  const results: CheckResult[] = [];
  const databaseUrl = process.env.DATABASE_URL?.trim();

  console.log("=== VettedME DB diagnostics ===");
  console.log(`NODE_ENV: ${process.env.NODE_ENV || "(unset)"}`);
  console.log(`PORT:     ${process.env.PORT || "(unset)"}`);

  if (!databaseUrl) {
    printResult({
      name: "DATABASE_URL",
      ok: false,
      detail: "missing — set in .env (e.g. postgresql://...@127.0.0.1:5433/vetted_db)",
    });
    return 1;
  }

  printResult({
    name: "DATABASE_URL",
    ok: true,
    detail: redactDatabaseUrl(databaseUrl),
  });

  if (!/5433/.test(databaseUrl) && /localhost|127\.0\.0\.1/.test(databaseUrl)) {
    console.log(
      "[HINT] Local Docker Postgres is usually on port 5433 (vetted-pg). Confirm: docker ps"
    );
  }

  const pool = new Pool({ connectionString: databaseUrl, connectionTimeoutMillis: 5000 });
  let prisma: PrismaClient | null = null;

  try {
    // 1) Raw TCP / auth / ready
    try {
      const client = await pool.connect();
      try {
        const r = await client.query<{ now: Date; version: string }>(
          "SELECT NOW() AS now, version() AS version"
        );
        const row = r.rows[0];
        results.push({
          name: "postgres.connect",
          ok: true,
          detail: `connected; server time ${row?.now?.toISOString?.() || row?.now}`,
        });
        const ver = String(row?.version || "").split(",")[0] || "unknown";
        results.push({ name: "postgres.version", ok: true, detail: ver });
      } finally {
        client.release();
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      results.push({
        name: "postgres.connect",
        ok: false,
        detail: msg,
      });
      for (const r of results) printResult(r);
      console.log("\nResult: FAIL (cannot reach Postgres)");
      return 1;
    }

    // 2) Prisma client via same pool (Prisma v7 adapter)
    try {
      const adapter = new PrismaPg(pool);
      prisma = new PrismaClient({ adapter, log: ["error"] });
      await prisma.$queryRaw`SELECT 1 AS ok`;
      results.push({
        name: "prisma.$queryRaw",
        ok: true,
        detail: "SELECT 1 succeeded",
      });
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      results.push({
        name: "prisma.$queryRaw",
        ok: false,
        detail: msg,
      });
      for (const r of results) printResult(r);
      console.log("\nResult: FAIL (Prisma client / generate mismatch?)");
      console.log("Try: npx prisma generate && npx prisma db push");
      return 1;
    }

    // 3) users table + UHRS columns (Justice: refuse silent schema drift)
    try {
      const cols = await prisma.$queryRaw<Array<{ column_name: string }>>`
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'users'
        ORDER BY column_name
      `;
      const names = new Set(cols.map((c) => c.column_name));
      if (names.size === 0) {
        results.push({
          name: "schema.users",
          ok: false,
          detail: "table public.users not found — run: npx prisma db push",
        });
      } else {
        results.push({
          name: "schema.users",
          ok: true,
          detail: `${names.size} columns present`,
        });
        const missing = UHRS_COLUMNS.filter((c) => !names.has(c));
        if (missing.length > 0) {
          results.push({
            name: "schema.uhrs",
            ok: false,
            detail: `missing columns: ${missing.join(", ")} — run: npx prisma db push`,
          });
        } else {
          results.push({
            name: "schema.uhrs",
            ok: true,
            detail: `all UHRS fields present (${UHRS_COLUMNS.join(", ")})`,
          });
        }
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      results.push({ name: "schema.users", ok: false, detail: msg });
    }

    // 4) Light read via Prisma model (catches client/schema mismatch on UHRS fields)
    try {
      const userCount = await prisma.user.count();
      results.push({
        name: "prisma.user.count",
        ok: true,
        detail: `${userCount} row(s)`,
      });

      const sample = await prisma.user.findFirst({
        select: {
          id: true,
          email: true,
          uhrsSpamScore: true,
          isSimulatedBanned: true,
          speedViolations: true,
        },
        orderBy: { email: "asc" },
      });
      if (sample) {
        results.push({
          name: "prisma.user.uhrsSample",
          ok: true,
          detail: `email=${sample.email} spam=${sample.uhrsSpamScore} banned=${sample.isSimulatedBanned} speed=${sample.speedViolations}`,
        });
      } else {
        results.push({
          name: "prisma.user.uhrsSample",
          ok: true,
          detail: "no users yet (schema OK; seed or register to practice UHRS)",
        });
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      results.push({
        name: "prisma.user.count",
        ok: false,
        detail: msg.includes("uhrs") || msg.includes("Unknown field")
          ? `${msg} — run: npx prisma generate`
          : msg,
      });
    }
  } finally {
    try {
      if (prisma) await prisma.$disconnect();
    } catch {
      /* ignore */
    }
    try {
      await pool.end();
    } catch {
      /* ignore */
    }
  }

  console.log("");
  for (const r of results) printResult(r);

  const failed = results.filter((r) => !r.ok);
  if (failed.length > 0) {
    console.log(`\nResult: FAIL (${failed.length} check(s))`);
    return 1;
  }

  console.log("\nResult: PASS — database ready for UHRS / API");
  return 0;
}

main()
  .then((code) => {
    process.exit(code);
  })
  .catch((err: unknown) => {
    const msg = err instanceof Error ? err.message : String(err);
    console.error("[FAIL] unexpected:", msg);
    process.exit(1);
  });
