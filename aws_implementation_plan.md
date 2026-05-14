# AWS Implementation Plan — Scaler Primer

> Written: 2026-05-13
> Current state: Streamlit Cloud prototype with local filesystem, multiprocessing workers, single-key API calls.
> Goal: Production-grade AWS backend that handles multiple concurrent instructors, proper job queuing, secure video delivery, and clean separation of concerns.

---

## 1. Current Architecture (What We Have)

```
Browser → Streamlit (student_app.py)
              │
              ├── multiprocessing.Process (pipeline_worker.py)
              │       ├── Groot API         → slide PNGs
              │       ├── Anthropic Claude  → evals + rewrites
              │       ├── ElevenLabs TTS    → audio MP3s
              │       └── FFmpeg            → final MP4
              │
              ├── Local filesystem (temp/, output/)
              ├── metrics.json (flat file, race-condition-prone)
              └── Progress via temp JSON files
```

**What's already AWS-ready in the codebase:**
- `modules/storage/s3.py` — S3Storage class exists (basic, needs improvements)
- `config/settings.py` — AWS credentials, bucket, region already configurable
- `modules/storage/base.py` — BaseStorage abstraction means swapping Local → S3 is one line
- `models/schemas.py` — Clean Pydantic schemas for all pipeline I/O
- All three pipelines are modular and independent

---

## 2. Target Architecture

```
Browser (New Frontend — React/Next.js)
    │
    ▼
API Gateway
    │
    ▼
FastAPI Backend (ECS Fargate)
    ├── POST /jobs          → submit generation job
    ├── GET  /jobs/{id}     → poll status
    ├── GET  /videos        → list completed videos
    └── GET  /metrics       → pipeline analytics
    │
    ▼
Amazon SQS (Job Queue)
    │
    ▼
Pipeline Workers (ECS Fargate — auto-scaled)
    ├── Pulls job from SQS
    ├── Runs pipeline (Groot → Claude → ElevenLabs → FFmpeg)
    ├── Uploads MP4 to S3
    ├── Updates job status in DynamoDB
    └── Deletes SQS message on success
    │
    ▼
Amazon S3 (Video Storage)
    │
    ▼
CloudFront (CDN — serves videos to browser)

Supporting services:
    ├── DynamoDB           → jobs table + metrics table
    ├── Secrets Manager    → API keys (Anthropic, ElevenLabs, Groot cookies)
    ├── ECR                → Docker images for workers + API
    ├── CloudWatch         → logs + alerts
    └── IAM                → roles for ECS tasks (no hardcoded credentials)
```

---

## 3. AWS Service Decisions

### Compute — ECS Fargate (not Lambda)
Lambda is ruled out: 15-minute timeout, no FFmpeg in standard runtime, 10GB memory cap. A single video generation takes 3–8 minutes and runs FFmpeg + multiple API calls in sequence. ECS Fargate containers with 4 vCPU / 8GB RAM is the right fit. Spot Fargate for cost savings (~70% cheaper for workers).

### Queue — SQS Standard Queue
One queue for all job types. Job type (single_topic / document / personalized_primer) is a field in the message body. SQS visibility timeout set to 15 minutes (covers worst-case generation time). Dead-letter queue (DLQ) for jobs that fail 3 times.

### Database — DynamoDB
Two tables:
- `primer_jobs` — tracks every generation request (status, input params, output path, timestamps)
- `primer_metrics` — one record per completed video (replaces metrics.json entirely)

DynamoDB fits our access patterns: write once on job creation, update a few times during processing, read by job ID or instructor ID. No complex joins needed.

### Storage — S3 + CloudFront
Videos stored in S3. CloudFront sits in front for fast delivery and pre-signed URL support. Temp files during pipeline execution use the ECS task's local ephemeral disk (up to 200GB on Fargate) — no EFS needed.

### Secrets — AWS Secrets Manager
All API keys stored here: Anthropic key pool (JSON array), ElevenLabs key pool, Groot cookies. Workers fetch secrets on startup via IAM role — no hardcoded credentials anywhere.

### Auth — JWT (integrate with Scaler's existing auth)
FastAPI validates JWT tokens issued by Scaler's auth system. Each request carries instructor identity. No new auth system to build — just wire into what Scaler already has.

---

## 4. Implementation Phases

---

### Phase 1 — Storage + Metrics + Docker Foundation
*Goal: Get the pipeline running in a container with S3 storage and DynamoDB metrics. No queue yet.*

**4.1 Upgrade S3Storage (`modules/storage/s3.py`)**
- Add CloudFront URL generation (return CDN URL, not raw S3 URL)
- Add pre-signed URL method for secure access
- Add multipart upload for large files (>100MB)
- Use IAM role (no hardcoded credentials) when running on ECS

**4.2 Upgrade metrics (`utils/metrics.py`)**
- Add DynamoDB backend alongside existing JSON backend
- `METRICS_BACKEND` env var switches between `local` (JSON) and `dynamodb`
- DynamoDB write is a single `put_item` call — no read-modify-write, no race condition
- Keep JSON backend for local dev

**4.3 Add Job tracking (`utils/jobs.py` — new file)**
- `create_job(job_id, job_type, params, instructor_id)` → writes to DynamoDB `primer_jobs` table
- `update_job_status(job_id, status, output_path=None, error=None)` → updates record
- `get_job(job_id)` → reads record
- Status enum: `queued → processing → complete → failed`

**4.4 Dockerise the pipeline**
```dockerfile
# Dockerfile.worker
FROM python:3.11-slim
RUN apt-get install -y ffmpeg  ← critical
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . /app
WORKDIR /app
CMD ["python", "-m", "workers.sqs_consumer"]
```
- Add `docker-compose.yml` for local dev (mounts `.env`, uses LocalStorage + local metrics)
- Push image to ECR

**4.5 Fix Document pipeline temp dir collision (quick win)**
- Change `f"doc_{safe_topic}"` → `f"doc_{safe_topic}_{uuid4().hex[:8]}"` in `pipelines/document.py`

**New files:** `Dockerfile.worker`, `docker-compose.yml`, `utils/jobs.py`
**Modified files:** `modules/storage/s3.py`, `utils/metrics.py`, `pipelines/document.py`, `requirements.txt`

---

### Phase 2 — SQS Workers
*Goal: Replace `multiprocessing.Process` + `pipeline_worker.py` with proper SQS consumer workers.*

**4.6 SQS Consumer (`workers/sqs_consumer.py` — new file)**
```
loop:
  poll SQS (long polling, 20s wait)
  if message:
    parse job_type + params
    update_job_status(job_id, "processing")
    run the appropriate pipeline
    upload video to S3
    update_job_status(job_id, "complete", output_path=s3_url)
    delete SQS message
  if exception:
    update_job_status(job_id, "failed", error=str(e))
    let SQS visibility timeout expire → message returns to queue for retry
    after 3 failures → goes to DLQ
```

**4.7 API key pool (`utils/key_pool.py` — new file)**
- Fetch key arrays from Secrets Manager on startup
- Round-robin with retry-on-429: if a key gets rate-limited, skip to next and back-off that key for 60s
- Separate pools for Anthropic, ElevenLabs
- Worker passes the selected key into `call_llm` and TTS constructors

**4.8 ECS Task Definition**
- Worker task: 4 vCPU, 8GB RAM, Fargate Spot
- Auto-scaling: scale out when SQS queue depth > 2, scale in when queue is empty
- IAM task role: S3 read/write, DynamoDB read/write, SQS consume, Secrets Manager read
- No AWS credentials in code — IAM role only

**New files:** `workers/__init__.py`, `workers/sqs_consumer.py`, `utils/key_pool.py`
**Retired:** `dashboard/pipeline_worker.py` (logic moves to `workers/sqs_consumer.py`)

---

### Phase 3 — FastAPI Backend
*Goal: Replace Streamlit's direct pipeline calls with a proper REST API.*

**4.9 FastAPI app (`api/` — new directory)**
```
api/
  main.py           ← FastAPI app, CORS, middleware
  routers/
    jobs.py         ← POST /jobs, GET /jobs/{id}
    videos.py       ← GET /videos, GET /videos/{id}/url
    metrics.py      ← GET /metrics (internal only)
  auth.py           ← JWT validation middleware
  dependencies.py   ← shared DynamoDB client, SQS client
```

**Key endpoints:**

`POST /jobs`
- Body: `{ job_type, topic, level, options: { num_scenes, scribble, lecture_eval, ... } }`
- Validates input
- Creates job record in DynamoDB (status: queued)
- Sends message to SQS
- Returns `{ job_id }`

`GET /jobs/{job_id}`
- Returns current status + output URL if complete
- Frontend polls this every 3s during generation

`GET /videos`
- Lists all completed videos for the authenticated instructor
- Returns S3 pre-signed URLs (24h expiry) — not public S3 URLs

`GET /metrics`
- Internal endpoint, restricted to admin role
- Queries DynamoDB metrics table

**4.10 Dockerfile.api**
- Lighter image (no FFmpeg needed)
- ECS Fargate, 1 vCPU / 2GB RAM, 2 tasks minimum for availability

**New files:** `api/` directory, `Dockerfile.api`

---

### Phase 4 — New Frontend
*Goal: Replace Streamlit with a proper React/Next.js frontend.*

**4.11 Frontend (separate repository recommended)**

Pages:
- `/generate` — form to submit a job (topic, level, options)
- `/jobs` — list of in-progress and completed jobs with real-time status
- `/library` — completed videos with inline player
- `/metrics` — admin-only analytics page

Key frontend behaviours:
- Job submission hits `POST /jobs`
- Status polling: `GET /jobs/{id}` every 3s while status is `queued` or `processing`
- Video playback: fetch pre-signed URL from `GET /videos/{id}/url`, feed to `<video>` tag
- Auth: attach JWT from Scaler's auth system to every API request

**No Streamlit dependency anywhere in this phase.**

---

### Phase 5 — Production Hardening
*Goal: Make it reliable, observable, and cost-efficient at scale.*

**4.12 CloudFront**
- Sit in front of S3 for video delivery
- Pre-signed CloudFront URLs (more secure than S3 pre-signed)
- Cache static assets, don't cache video content (each URL is unique per user)

**4.13 CloudWatch**
- Structured JSON logging from all workers and API (already have `utils/logger.py` — update to emit JSON)
- Alarms: DLQ message count > 0 (job failed 3 times), worker task count = 0, API error rate > 5%
- Dashboard: queue depth, active workers, jobs per hour, API latency

**4.14 Auto-scaling**
- Workers: SQS queue depth → target tracking scaling policy
- API: CPU/request count → target tracking
- Scale-to-zero for workers when no jobs (cost saving overnight)

**4.15 Cost controls**
- Fargate Spot for workers (70% cheaper, acceptable for async jobs)
- S3 lifecycle policy: move videos older than 90 days to S3 Glacier Instant Retrieval
- DynamoDB on-demand pricing (pay per request, not provisioned)
- CloudWatch log retention: 30 days

---

## 5. Code Changes Summary

| File | Change |
|---|---|
| `modules/storage/s3.py` | CloudFront URLs, pre-signed URLs, IAM role support, multipart upload |
| `utils/metrics.py` | DynamoDB backend, keep JSON for local dev |
| `utils/jobs.py` | New — job CRUD against DynamoDB |
| `utils/key_pool.py` | New — API key pool with round-robin + 429 back-off |
| `pipelines/document.py` | UUID temp dir fix (1 line) |
| `config/settings.py` | Add SQS URL, DynamoDB table names, CloudFront domain |
| `workers/sqs_consumer.py` | New — replaces pipeline_worker.py |
| `api/` | New — entire FastAPI application |
| `Dockerfile.worker` | New |
| `Dockerfile.api` | New |
| `docker-compose.yml` | New — local dev |
| `dashboard/` | Retired after Phase 4 |

**Pipelines themselves (`direct.py`, `document.py`, `dynamic.py`) need zero changes** — they're already clean and modular. The worker just calls them the same way pipeline_worker.py does today.

---

## 6. DynamoDB Table Schemas

**`primer_jobs`**
```
PK: job_id (string, UUID)
SK: (none — single-table, job_id is unique)
GSI: instructor_id-created_at-index (for listing a user's jobs)

Attributes:
  instructor_id     string
  job_type          string  (single_topic | document | personalized_primer)
  status            string  (queued | processing | complete | failed)
  params            map     (topic, level, options — everything the worker needs)
  output_path       string  (S3 key of the finished video)
  error             string  (populated on failure)
  created_at        string  (ISO timestamp)
  updated_at        string
  processing_ms     number  (wall clock time for the worker)
```

**`primer_metrics`**
```
PK: metric_id (string, UUID — avoids any write conflicts)
GSI: topic-index, status-index

Attributes:
  (same fields as current metrics.json entries)
  instructor_id     string  (new — for per-user analytics)
```

---

## 7. Infrastructure as Code

Use **AWS CDK (Python)** — matches our existing Python stack, no context switching.

```
infra/
  app.py                 ← CDK app entry point
  stacks/
    storage_stack.py     ← S3 bucket, CloudFront distribution
    database_stack.py    ← DynamoDB tables
    queue_stack.py       ← SQS queues (main + DLQ)
    worker_stack.py      ← ECS cluster, task definition, auto-scaling
    api_stack.py         ← ECS service for FastAPI, API Gateway
    secrets_stack.py     ← Secrets Manager entries
    iam_stack.py         ← IAM roles and policies
```

CDK lets us version-control the entire infrastructure alongside the application code.

---

## 8. Migration Strategy

**Do NOT big-bang migrate. Run both in parallel.**

1. Deploy Phase 1 (Docker + S3 + DynamoDB) while Streamlit is still live
2. Test workers locally with Docker Compose — same pipelines, new infra
3. Deploy Phase 2 (SQS workers) to AWS, run shadow traffic (same jobs sent to both Streamlit and AWS workers, compare outputs)
4. Deploy Phase 3 (FastAPI) — point Streamlit at the new API instead of calling pipelines directly
5. Deploy Phase 4 (new frontend) — switch instructors over, keep Streamlit as fallback for 1 week
6. Decommission Streamlit

At no point is there a hard cutover — each phase is independently deployable.

---

## 9. What Stays Unchanged

- All pipeline logic (`direct.py`, `document.py`, `dynamic.py`)
- All modules (`groot/`, `tts/`, `video_assembler/`, `annotation/`, `animation/`)
- All eval logic (`utils/evals.py`)
- All Pydantic schemas (`models/schemas.py`)
- Logging (`utils/logger.py` — minor update to emit JSON)

The abstraction layers (BaseStorage, BaseTTS, etc.) were designed for exactly this migration. They earn their value here.

---

## 10. Open Questions to Decide Before Starting

1. **Groot network access** — Groot is internal Scaler infra. Workers running in AWS need a route to it. Options: VPC peering with Scaler's network, Groot exposed via an internal ALB, or a VPN. Scaler infra team needs to be looped in.

2. **Auth integration** — Do we integrate with Scaler's existing SSO/JWT issuer, or build a standalone auth? Need to know what Scaler's auth system exposes.

3. **AWS region** — `ap-south-1` (Mumbai) is already in settings. Confirm this is the right region for latency to Groot + instructor locations.

4. **Multiple API keys** — How many Anthropic and ElevenLabs keys are available? The key pool design scales with however many we have, but we need the keys to start.

5. **Frontend ownership** — Does the Scaler frontend team build Phase 4, or do we? This affects timeline significantly.

6. **CDK vs Terraform** — CDK (Python) recommended since the team is Python-native. If Scaler already uses Terraform for other infra, match that instead.
