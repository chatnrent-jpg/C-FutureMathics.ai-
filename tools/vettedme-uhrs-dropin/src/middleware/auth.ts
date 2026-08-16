import { NextFunction, Request, Response } from "express";
import jwt from "jsonwebtoken";
import { AppError } from "./errorHandler";

export interface AuthUser {
  id: string;
  email: string;
  role: string;
}

declare global {
  namespace Express {
    interface Request {
      user?: AuthUser;
    }
  }
}

const JWT_SECRET = process.env.JWT_SECRET || "change-this-secret";

/**
 * JWT bearer authentication middleware.
 * Attaches req.user = { id, email, role } on success.
 */
export function authenticate(req: Request, _res: Response, next: NextFunction) {
  try {
    const header = req.headers.authorization || "";
    const [scheme, token] = header.split(" ");
    if (scheme !== "Bearer" || !token) {
      throw new AppError("Missing or invalid Authorization header", 401);
    }

    const payload = jwt.verify(token, JWT_SECRET) as {
      userId?: string;
      id?: string;
      email?: string;
      role?: string;
    };

    const id = payload.userId || payload.id;
    if (!id || !payload.email || !payload.role) {
      throw new AppError("Invalid token payload", 401);
    }

    req.user = {
      id,
      email: payload.email,
      role: payload.role,
    };
    next();
  } catch (err) {
    if (err instanceof AppError) return next(err);
    return next(new AppError("Unauthorized", 401));
  }
}

/**
 * ToT / supervisor gate — ADMIN role required after authenticate.
 */
export function requireAdmin(req: Request, _res: Response, next: NextFunction) {
  if (req.user?.role !== "ADMIN") {
    return next(new AppError("Unauthorized: Admin (ToT) access required", 403));
  }
  next();
}

export default authenticate;
