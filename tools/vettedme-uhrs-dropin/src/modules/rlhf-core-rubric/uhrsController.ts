import { Request, Response } from "express";
import { asyncHandler, AppError } from "../../middleware/errorHandler";
import {
  acknowledgeUhrsBan,
  getUhrsStatus,
  resetUhrsPractice,
  submitUhrsTask,
} from "./uhrsService";

function requireUserId(req: Request): string {
  const id = req.user?.id;
  if (!id) {
    throw new AppError("Authentication required", 401);
  }
  return id;
}

export const getUhrsStatusHandler = asyncHandler(
  async (req: Request, res: Response) => {
    const userId = requireUserId(req);
    try {
      const snapshot = await getUhrsStatus(userId);
      res.status(200).json({
        status: "success",
        data: {
          ...snapshot,
          flagged: snapshot.uhrsSpamScore < 0.8,
        },
      });
    } catch (err: any) {
      throw new AppError(err?.message || "UHRS status failed", err?.statusCode || 500);
    }
  }
);

export const submitUhrsTaskHandler = asyncHandler(
  async (req: Request, res: Response) => {
    const userId = requireUserId(req);
    const body = req.body as {
      isControlQuestion: boolean;
      controlCorrect?: boolean;
      responseTimeMs: number;
    };

    try {
      const result = await submitUhrsTask(userId, body);
      const statusCode = result.snapshot.isSimulatedBanned && result.newlyBanned ? 200 : 200;
      res.status(statusCode).json({
        status: result.snapshot.isSimulatedBanned ? "simulated_ban" : "success",
        data: result,
      });
    } catch (err: any) {
      throw new AppError(err?.message || "UHRS submit failed", err?.statusCode || 500);
    }
  }
);

export const acknowledgeUhrsBanHandler = asyncHandler(
  async (req: Request, res: Response) => {
    const userId = requireUserId(req);
    try {
      const snapshot = await acknowledgeUhrsBan(userId);
      res.status(200).json({
        status: "success",
        message: "Simulated ban cleared — continue practice carefully.",
        data: snapshot,
      });
    } catch (err: any) {
      throw new AppError(
        err?.message || "UHRS acknowledge failed",
        err?.statusCode || 500
      );
    }
  }
);

export const resetUhrsPracticeHandler = asyncHandler(
  async (req: Request, res: Response) => {
    const userId = requireUserId(req);
    try {
      const snapshot = await resetUhrsPractice(userId);
      res.status(200).json({
        status: "success",
        message: "UHRS practice metrics reset to defaults.",
        data: snapshot,
      });
    } catch (err: any) {
      throw new AppError(err?.message || "UHRS reset failed", err?.statusCode || 500);
    }
  }
);
