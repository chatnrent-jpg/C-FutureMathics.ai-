import dotenv from "dotenv";

// Must load before any module that reads process.env (Prisma, JWT, PORT).
dotenv.config({
  override: (process.env.NODE_ENV || "development") !== "production",
});
