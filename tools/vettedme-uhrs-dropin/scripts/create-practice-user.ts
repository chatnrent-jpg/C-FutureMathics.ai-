/**
 * Create a practice TALENT user from the CLI (bypasses the browser).
 *
 *   npx tsx scripts/create-practice-user.ts
 *   npx tsx scripts/create-practice-user.ts --email you@example.com --password 'YourPass123!'
 */

import dotenv from "dotenv";
dotenv.config({ override: true });

import bcrypt from "bcryptjs";
import jwt from "jsonwebtoken";
import { Pool } from "pg";
import { PrismaPg } from "@prisma/adapter-pg";
import { PrismaClient } from "@prisma/client";

function arg(name: string, fallback: string): string {
  const idx = process.argv.indexOf(`--${name}`);
  if (idx >= 0 && process.argv[idx + 1]) return process.argv[idx + 1];
  return fallback;
}

async function main() {
  const databaseUrl = process.env.DATABASE_URL;
  if (!databaseUrl) {
    console.error("FAIL: DATABASE_URL missing in .env");
    process.exit(1);
  }

  const email = arg("email", `practice.${Date.now()}@vetted.local`).trim().toLowerCase();
  const password = arg("password", "Practice123!");
  const firstName = arg("firstName", "UHRS");
  const lastName = arg("lastName", "Practice");

  console.log("DATABASE_URL host:", databaseUrl.replace(/:[^:@/]+@/, ":***@"));
  console.log("Creating:", email);

  const pool = new Pool({ connectionString: databaseUrl });
  const prisma = new PrismaClient({ adapter: new PrismaPg(pool) });

  try {
    const existing = await prisma.user.findUnique({ where: { email } });
    if (existing) {
      console.error("FAIL: email already registered — try Login instead");
      process.exit(1);
    }

    const passwordHash = await bcrypt.hash(password, 10);
    const user = await prisma.user.create({
      data: {
        email,
        passwordHash,
        firstName,
        lastName,
        role: "TALENT",
      },
    });

    const token = jwt.sign(
      { userId: user.id, email: user.email, role: user.role },
      process.env.JWT_SECRET || "change-this-secret",
      { expiresIn: (process.env.JWT_EXPIRY || "7d") as jwt.SignOptions["expiresIn"] }
    );

    console.log("OK user id:", user.id);
    console.log("OK email:", user.email);
    console.log("OK password:", password);
    console.log("");
    console.log("Paste this into browser DevTools console on http://localhost:3000:");
    console.log(
      `localStorage.setItem('vetted_token', ${JSON.stringify(token)}); localStorage.setItem('vetted_user_id', ${JSON.stringify(user.id)}); localStorage.setItem('vetted_email', ${JSON.stringify(user.email)}); location.href='/uhrs';`
    );
  } catch (err: any) {
    console.error("FAIL", err?.code || "", err?.message || err);
    if (err?.code === "P2022") {
      console.error(
        "Hint: UHRS columns missing on THIS database. Run: npx prisma db push"
      );
    }
    process.exit(1);
  } finally {
    await prisma.$disconnect();
    await pool.end();
  }
}

main();
