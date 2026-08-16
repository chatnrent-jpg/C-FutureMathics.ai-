/**
 * Slim local API — auth + UHRS only.
 * Use this when src/index.ts crashes on stale RLHF routes.
 *
 *   npx tsx watch src/slim-api.ts
 */
import "./env";
import express from "express";
import cors from "cors";
import { authRouter } from "./routes/auth.routes";
import uhrsRouter from "./modules/rlhf-core-rubric/uhrsRoutes";
import { errorHandler } from "./middleware/errorHandler";

const app = express();
const PORT = Number(process.env.PORT || 8080);

app.use(
  cors({
    origin: String(process.env.CORS_ORIGIN || "http://localhost:3000")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean),
    credentials: true,
  })
);
app.use(express.json({ limit: "2mb" }));

app.get("/health", (_req, res) => {
  res.status(200).json({ status: "healthy", mode: "slim-api" });
});

app.get("/", (_req, res) => {
  res.status(200).json({
    service: "VETTED slim-api",
    mode: "auth+uhrs",
    health: "/health",
    login: "POST /api/v1/auth/login",
    register: "POST /api/v1/auth/register",
    uhrsPing: "GET /api/rlhf/uhrs/ping",
  });
});

app.use("/api/v1/auth", authRouter);
app.use("/api/rlhf", uhrsRouter);
app.use("/api/v1/modules/rlhf-core-rubric", uhrsRouter);

app.use(errorHandler);
app.use((req, res) => {
  res.status(404).json({ error: "Route not found", path: req.path, mode: "slim-api" });
});

function dbHint(): string {
  try {
    const raw = process.env.DATABASE_URL || "";
    if (!raw) return "(DATABASE_URL missing)";
    const u = new URL(raw);
    return `${u.hostname}:${u.port || "5432"}${u.pathname}`;
  } catch {
    return "(DATABASE_URL unparseable)";
  }
}

app.listen(PORT, () => {
  console.log(`slim-api listening on ${PORT}`);
  console.log(`Database: ${dbHint()}`);
  console.log("UHRS simulator MOUNTED — GET /api/rlhf/uhrs/ping");
  console.log("Auth: POST /api/v1/auth/login  POST /api/v1/auth/register");
});
