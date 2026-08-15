/**
 * Tokoka / UHRS simulator routes — mounted independently from the main RLHF
 * router so a stale routes.ts on Windows cannot hide these endpoints.
 */
import { Router, Request, Response, NextFunction } from "express";
import { ZodTypeAny } from "zod";
import { authenticate } from "../../middleware/auth";
import {
  acknowledgeUhrsBanHandler,
  getUhrsStatusHandler,
  resetUhrsPracticeHandler,
  submitUhrsTaskHandler,
} from "./uhrsController";
import { submitUhrsTaskSchema } from "./validation";

const validateBody = (schema: ZodTypeAny) => {
  return async (
    req: Request,
    res: Response,
    next: NextFunction
  ): Promise<void> => {
    try {
      const parsed = await schema.parseAsync({
        body: req.body,
        query: req.query,
        params: req.params,
      });
      if (
        parsed &&
        typeof parsed === "object" &&
        "body" in (parsed as Record<string, unknown>)
      ) {
        req.body = (parsed as { body: unknown }).body;
      }
      next();
    } catch (error: any) {
      const issues = error?.issues || error?.errors || [];
      res.status(400).json({
        status: "error",
        error: "Validation failed",
        errors: issues,
        details: issues.map(
          (e: any) => `${(e.path || []).join(".")}: ${e.message}`
        ),
      });
    }
  };
};

const uhrsRouter = Router();

/** Unauthenticated liveness check — expect 200 { uhrs: true } */
uhrsRouter.get("/uhrs/ping", (_req, res) => {
  res.status(200).json({
    status: "success",
    uhrs: true,
    message: "UHRS routes mounted",
  });
});

uhrsRouter.get("/uhrs/status", authenticate, getUhrsStatusHandler);
uhrsRouter.post(
  "/uhrs/submit",
  authenticate,
  validateBody(submitUhrsTaskSchema),
  submitUhrsTaskHandler
);
uhrsRouter.post("/uhrs/acknowledge-ban", authenticate, acknowledgeUhrsBanHandler);
uhrsRouter.post("/uhrs/reset", authenticate, resetUhrsPracticeHandler);

export default uhrsRouter;
