/**
 * Reset a local practice user's password.
 *
 *   npx tsx scripts/reset-practice-password.ts --email chatnrent@gmail.com --password Practice123!
 */
import dotenv from "dotenv";
dotenv.config({ override: true });

import bcrypt from "bcryptjs";
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
    console.error("FAIL: DATABASE_URL missing");
    process.exit(1);
  }

  const email = arg("email", "chatnrent@gmail.com").trim().toLowerCase();
  const password = arg("password", "Practice123!");

  const pool = new Pool({ connectionString: databaseUrl });
  const prisma = new PrismaClient({ adapter: new PrismaPg(pool) });

  try {
    const user = await prisma.user.findUnique({ where: { email } });
    if (!user) {
      const created = await prisma.user.create({
        data: {
          email,
          passwordHash: await bcrypt.hash(password, 10),
          firstName: "UHRS",
          lastName: "Practice",
          role: "TALENT",
        },
      });
      console.log("CREATED", created.email);
    } else {
      await prisma.user.update({
        where: { id: user.id },
        data: { passwordHash: await bcrypt.hash(password, 10), isActive: true },
      });
      console.log("RESET", user.email);
    }
    console.log("PASSWORD", password);
    console.log("Login at http://localhost:3000/login");
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error("FAIL", msg);
    process.exit(1);
  } finally {
    await prisma.$disconnect();
    await pool.end();
  }
}

main();
