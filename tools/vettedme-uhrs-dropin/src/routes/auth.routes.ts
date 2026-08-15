import { Router, Request, Response } from 'express';
import { asyncHandler, AppError } from '../middleware/errorHandler';
import bcrypt from 'bcryptjs';
import jwt from 'jsonwebtoken';
import { Prisma } from '@prisma/client';
import { prisma } from '../lib/prisma';

const router = Router();
const JWT_SECRET = process.env.JWT_SECRET || 'change-this-secret';
const JWT_EXPIRY = (process.env.JWT_EXPIRY || '7d') as jwt.SignOptions['expiresIn'];

const ALLOWED_ROLES = new Set(['TALENT', 'BUSINESS', 'ADMIN']);

function mapPrismaAuthError(err: unknown): AppError | null {
  if (err instanceof Prisma.PrismaClientKnownRequestError) {
    if (err.code === 'P2002') {
      return new AppError('Email already registered', 409);
    }
    if (err.code === 'P2022') {
      return new AppError(
        'Database schema missing UHRS/User columns. Fix DATABASE_URL (use port 5433 for vetted-pg), then run: npx prisma db push && npx prisma generate - and restart the API.',
        500
      );
    }
    return new AppError(`Database error (${err.code}): ${err.message}`, 500);
  }
  if (err instanceof Prisma.PrismaClientInitializationError) {
    return new AppError(
      `Cannot reach database. Check DATABASE_URL (vetted-pg is usually 127.0.0.1:5433). ${err.message}`,
      503
    );
  }
  return null;
}

/**
 * POST /api/v1/auth/register
 * Register new user
 */
router.post(
  '/register',
  asyncHandler(async (req: Request, res: Response) => {
    const { email, password, firstName, lastName, role, phoneNumber, countryCode } = req.body;

    if (!email || !password || !firstName || !lastName || !role) {
      throw new AppError('Missing required fields', 400);
    }

    if (!ALLOWED_ROLES.has(String(role))) {
      throw new AppError('Invalid role', 400);
    }

    try {
      const existingUser = await prisma.user.findUnique({
        where: { email: String(email).trim().toLowerCase() },
      });
      if (existingUser) {
        throw new AppError('Email already registered', 409);
      }

      const passwordHash = await bcrypt.hash(password, 10);

      const user = await prisma.user.create({
        data: {
          email: String(email).trim().toLowerCase(),
          passwordHash,
          firstName: String(firstName).trim(),
          lastName: String(lastName).trim(),
          role,
          phoneNumber: phoneNumber || undefined,
          location: countryCode || undefined,
        },
      });

      const token = jwt.sign(
        { userId: user.id, email: user.email, role: user.role },
        JWT_SECRET,
        { expiresIn: JWT_EXPIRY }
      );

      res.status(201).json({
        success: true,
        message: 'User registered successfully',
        user: {
          id: user.id,
          email: user.email,
          firstName: user.firstName,
          lastName: user.lastName,
          role: user.role,
        },
        token,
      });
    } catch (err) {
      if (err instanceof AppError) throw err;
      const mapped = mapPrismaAuthError(err);
      if (mapped) throw mapped;
      throw err;
    }
  })
);

/**
 * POST /api/v1/auth/login
 * Login user
 */
router.post(
  '/login',
  asyncHandler(async (req: Request, res: Response) => {
    const { email, password } = req.body;

    if (!email || !password) {
      throw new AppError('Missing email or password', 400);
    }

    try {
      const user = await prisma.user.findUnique({
        where: { email: String(email).trim().toLowerCase() },
      });
      if (!user) {
        throw new AppError('Invalid credentials', 401);
      }

      const validPassword = await bcrypt.compare(password, user.passwordHash);
      if (!validPassword) {
        throw new AppError('Invalid credentials', 401);
      }

      if (!user.isActive) {
        throw new AppError('Account is inactive', 403);
      }

      await prisma.user.update({
        where: { id: user.id },
        data: { lastLoginAt: new Date() },
      });

      const token = jwt.sign(
        { userId: user.id, email: user.email, role: user.role },
        JWT_SECRET,
        { expiresIn: JWT_EXPIRY }
      );

      res.status(200).json({
        success: true,
        message: 'Login successful',
        user: {
          id: user.id,
          email: user.email,
          firstName: user.firstName,
          lastName: user.lastName,
          role: user.role,
          isActive: user.isActive,
        },
        token,
      });
    } catch (err) {
      if (err instanceof AppError) throw err;
      const mapped = mapPrismaAuthError(err);
      if (mapped) throw mapped;
      throw err;
    }
  })
);

export { router as authRouter };
