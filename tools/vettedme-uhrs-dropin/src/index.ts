import express, { Express } from 'express';
import http from 'http';
import cors from 'cors';
import helmet from 'helmet';
import dotenv from 'dotenv';
import { rateLimit } from 'express-rate-limit';
import { logger } from './utils/logger';
import { errorHandler } from './middleware/errorHandler';
import { vettedMERouter } from './routes/vettedme.routes';
import { vettedPayRouter } from './routes/vettedpay.routes';
import { webhookRouter } from './routes/webhook.routes';
import { authRouter } from './routes/auth.routes';
import { assessmentRouter } from './routes/assessment.routes';
import { gtmRouter } from './routes/gtm.routes';
import milestoneRouter from './routes/milestone.routes';
import disputeRouter from './routes/dispute.routes';
import complianceRouter from './routes/compliance.routes';
import auditRouter from './routes/audit.routes';
import leadRouter from './routes/lead.routes';
import rlhfRouter from './modules/rlhf-core-rubric/router';
import uhrsRouter from './modules/rlhf-core-rubric/uhrsRoutes';
import { initVivaSocketServer } from './modules/rlhf-core-rubric/vivaStreamController';

dotenv.config();

const app: Express = express();
const PORT = process.env.PORT || 3000;

// ============================================================================
// SECURITY MIDDLEWARE
// ============================================================================

app.use(helmet({
  contentSecurityPolicy: {
    directives: {
      defaultSrc: ["'self'"],
      styleSrc: ["'self'", "'unsafe-inline'"],
      scriptSrc: ["'self'"],
      imgSrc: ["'self'", "data:", "https:"],
    },
  },
  // Allow browser clients on a different origin (Cloudflare tunnel frontend → API)
  crossOriginResourcePolicy: { policy: "cross-origin" },
  hsts: {
    maxAge: 31536000,
    includeSubDomains: true,
    preload: true,
  },
}));

const corsOrigins = String(process.env.CORS_ORIGIN || "http://localhost:3000")
  .split(",")
  .map((o) => o.trim())
  .filter(Boolean);

app.use(cors({
  origin(origin, callback) {
    // Non-browser / same-origin tools (curl, Postman) send no Origin
    if (!origin) {
      callback(null, true);
      return;
    }
    const allowed =
      corsOrigins.includes(origin) ||
      corsOrigins.includes("*") ||
      // Dev tunnels rotate hostnames; allow trycloudflare frontends in non-prod
      (process.env.NODE_ENV !== "production" &&
        /\.trycloudflare\.com$/i.test(new URL(origin).hostname));
    callback(null, allowed ? origin : false);
  },
  credentials: true,
  methods: ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
  allowedHeaders: ["Content-Type", "Authorization"],
}));

// Rate Limiting — generous in development so UHRS / viva local practice is not blocked.
const isDev = (process.env.NODE_ENV || 'development') !== 'production';
const limiter = rateLimit({
  windowMs: Number(process.env.RATE_LIMIT_WINDOW_MS) || 15 * 60 * 1000,
  max:
    Number(process.env.RATE_LIMIT_MAX_REQUESTS) ||
    (isDev ? 5000 : 100),
  message: 'Too many requests from this IP, please try again later.',
  standardHeaders: true,
  legacyHeaders: false,
  skip: (req) => {
    // Always allow UHRS liveness + simulator practice under /api/rlhf/uhrs/*
    const p = req.path || '';
    return p.startsWith('/rlhf/uhrs') || p.includes('/rlhf/uhrs');
  },
});

app.use('/api/', limiter);

// Body Parsing
app.use(express.json({ limit: '10mb' }));
app.use(express.urlencoded({ extended: true, limit: '10mb' }));

// ============================================================================
// ROUTES
// ============================================================================

app.get('/', (req, res) => {
  const payload = {
    service: 'VETTED Backend',
    status: 'ok',
    version: process.env.API_VERSION || 'v1',
    note: 'This is the API server (JSON), not the candidate UI.',
    health: '/health',
    modules: {
      rlhfCoreRubric: {
        base: '/api/v1/modules/rlhf-core-rubric',
        uromiAlias: '/api/rlhf',
        lessons: '/api/v1/modules/rlhf-core-rubric/lessons',
        lessonExample:
          '/api/v1/modules/rlhf-core-rubric/lessons/03-core-rubric-dimensions',
        dataset:
          '/api/v1/modules/rlhf-core-rubric/dataset/preference-pairs?mode=candidate',
        validate: 'POST /api/v1/modules/rlhf-core-rubric/validate',
        vivaInitialize: 'POST /api/rlhf/viva/initialize',
        vivaStream: 'WS /api/rlhf/viva/stream?evaluationId=<uuid>',
        vivaDetail: 'GET /api/rlhf/viva/:id (ADMIN)',
        vivaEvaluate: 'POST /api/rlhf/viva/:id/evaluate (ADMIN)',
        analyticsLiveFeed: 'GET /api/rlhf/analytics/live-feed (ADMIN)',
        analytics: 'GET /api/v1/modules/rlhf-core-rubric/analytics',
        uhrsStatus: 'GET /api/rlhf/uhrs/status',
        uhrsSubmit: 'POST /api/rlhf/uhrs/submit',
        uhrsAckBan: 'POST /api/rlhf/uhrs/acknowledge-ban',
        uhrsReset: 'POST /api/rlhf/uhrs/reset',
      },
    },
    ui: {
      evaluationWorkspace:
        process.env.PUBLIC_FRONTEND_URL ||
        'https://capability-emotions-affordable-pray.trycloudflare.com/talent/assessment/rlhf',
      uromiCommandCenter: 'http://localhost:3000/analytics',
      uromiVivaTerminal: 'http://localhost:3000/viva',
      totLoginHint: 'tot.admin@uromi.vetted.local (seed password TotAdmin123!)',
    },
  };

  const accept = String(req.headers.accept || '');
  if (accept.includes('text/html')) {
    const uiUrl = payload.ui.evaluationWorkspace;
    res.status(200).type('html').send(`<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8" /><meta name="viewport" content="width=device-width, initial-scale=1" />
<title>VETTED Backend API</title>
<style>
  body{margin:0;font-family:ui-sans-serif,system-ui,sans-serif;background:linear-gradient(180deg,#eef5f4,#f4f7f8);color:#14212b}
  main{max-width:40rem;margin:2.5rem auto;padding:1.75rem;background:#fff;border:1px solid #d7e0e6;border-radius:1rem}
  h1{font-family:Georgia,serif;margin:0 0 .5rem}
  .badge{display:inline-block;background:#0f766e;color:#fff;font-size:.75rem;padding:.2rem .5rem;border-radius:.35rem}
  a.button{display:inline-block;margin-top:1rem;background:#0f766e;color:#fff;text-decoration:none;padding:.7rem 1rem;border-radius:.5rem;font-weight:600}
  ul{line-height:1.7}
  code{background:#eef4f3;padding:.1rem .35rem;border-radius:.3rem}
</style></head>
<body><main>
  <p class="badge">API SERVER</p>
  <h1>VETTED Backend</h1>
  <p>You opened the <strong>API</strong> tunnel. JSON text here is normal. The interactive evaluation UI is on the <strong>frontend</strong> tunnel.</p>
  <a class="button" href="${uiUrl}">Open RLHF Evaluation Workspace →</a>
  <h2 style="margin-top:1.75rem;font-size:1.1rem">Useful API links</h2>
  <ul>
    <li><a href="/health">/health</a></li>
    <li><a href="/api/v1/modules/rlhf-core-rubric/lessons/03-core-rubric-dimensions">Lesson 03 (HTML)</a></li>
    <li><a href="/api/v1/modules/rlhf-core-rubric/lessons/04-scoring-calibration">Lesson 04 (HTML)</a></li>
    <li><a href="/api/v1/modules/rlhf-core-rubric/validate">Validate help (POST only)</a></li>
  </ul>
</main></body></html>`);
    return;
  }

  res.status(200).json(payload);
});

app.get('/health', (req, res) => {
  res.status(200).json({
    status: 'healthy',
    timestamp: new Date().toISOString(),
    service: 'VETTED Backend',
    version: process.env.API_VERSION || 'v1',
  });
});

app.use('/api/v1/auth', authRouter);
app.use('/api/v1/vettedme', vettedMERouter);
app.use('/api/v1/vettedpay', vettedPayRouter);
app.use('/api/v1/webhooks', webhookRouter);
app.use('/api/v1/assessment', assessmentRouter);
app.use('/api/v1/gtm', gtmRouter);
app.use('/api/v1/milestones', milestoneRouter);
app.use('/api/v1/disputes', disputeRouter);
app.use('/api/v1/compliance', complianceRouter);
app.use('/api/v1/audit', auditRouter);
app.use('/api/v1/leads', leadRouter);
// Legacy / full module path (lessons, validate, analytics, viva)
app.use('/api/v1/modules/rlhf-core-rubric', rlhfRouter);
// Uromi Trust Infrastructure alias — Cloudflare tunnel / ToT terminals
app.use('/api/rlhf', rlhfRouter);
// UHRS / Tokoka simulator — dedicated mount (survives stale routes.ts)
app.use('/api/v1/modules/rlhf-core-rubric', uhrsRouter);
app.use('/api/rlhf', uhrsRouter);

// ============================================================================
// ERROR HANDLING
// ============================================================================

app.use(errorHandler);

// 404 Handler
app.use((req, res) => {
  res.status(404).json({
    error: 'Route not found',
    path: req.path,
  });
});

// ============================================================================
// SERVER STARTUP — native HTTP wrapper for custom WebSocket upgrades
// ============================================================================

const server = http.createServer(app);

// Inject and activate the real-time Uromi WebSocket pipe layer (before listen)
initVivaSocketServer(server);

server.listen(PORT, () => {
  logger.info(`🚀 VETTED Backend running on port ${PORT}`);
  logger.info(`📍 Environment: ${process.env.NODE_ENV || 'development'}`);
  logger.info(`🔐 Security headers: ENABLED`);
  logger.info(`⚡ VettedME Engine: READY`);
  logger.info(`💰 VettedPay Engine: READY`);
  logger.info(`🎯 UHRS simulator MOUNTED — GET /api/rlhf/uhrs/ping (expect 200)`);
  logger.info(`🌅 Ugboha Road Hub network pipeline fully hot-wired and listening.`);
});

// Graceful Shutdown
process.on('SIGTERM', () => {
  logger.info('SIGTERM signal received: closing HTTP server');
  server.close(() => {
    logger.info('HTTP server closed');
    process.exit(0);
  });
});

process.on('SIGINT', () => {
  logger.info('SIGINT signal received: closing HTTP server');
  server.close(() => {
    logger.info('HTTP server closed');
    process.exit(0);
  });
});

export default app;
