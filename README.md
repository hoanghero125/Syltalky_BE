# Syltalky Backend

FastAPI backend for Syltalky: integrated authentication, meeting orchestration, voice profiling, real-time captioning, and data post-processing.

---

## Stack

| Component | Version |
|---|---|
| Python | 3.11 |
| FastAPI | latest |
| PostgreSQL | 16 |
| SQLAlchemy (async) | 2.x |
| Alembic | migrations |
| LiveKit Server SDK | Python |
| MinIO | self-hosted S3 |
| Redis | 7 |
| Resend | transactional email |
| Qwen3.5-35B-A3B (OpenAI-compatible proxy) | meeting summaries + AI chat assistant |

---

## Setup

### Prerequisites

- Docker + Docker Compose
- A running `Syltalky_API` instance at `http://localhost:8000`

### 1. Configure environment

```bash
cp .env.example .env
# Edit .env — fill in SECRET_KEY, RESEND_API_KEY, LIVEKIT keys, LLM credentials
```

### 2. Start all services

```bash
docker compose up -d
```

This starts: `backend` (port 8001), `postgres` (5432), `minio` (9000 / 9001), `livekit` (7880), `redis` (6379).

### 3. Run database migrations

```bash
docker compose exec backend alembic upgrade head
```

### 4. Verify

```
GET http://localhost:8001/health   →  { "status": "ok" }
```

Interactive API docs: `http://localhost:8001/docs`

---

## Environment variables

| Variable | Description |
|---|---|
| `DATABASE_URL` | Async PostgreSQL URL (`postgresql+asyncpg://...`) |
| `SECRET_KEY` | JWT signing key — **change in production** |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | JWT access token TTL (default: 30) |
| `REFRESH_TOKEN_EXPIRE_DAYS` | JWT refresh token TTL (default: 7) |
| `MINIO_ENDPOINT` | Internal MinIO address (`minio:9000` inside Docker) |
| `MINIO_PUBLIC_ENDPOINT` | Browser-facing MinIO URL (`minio.syltalky.pro.vn` in prod) |
| `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` | MinIO credentials |
| `MINIO_BUCKET` | Bucket name (auto-created on startup) |
| `RESEND_API_KEY` | Resend API key for transactional email |
| `RESEND_FROM` | From address (e.g. `no-reply@syltalky.pro.vn`) |
| `LIVEKIT_URL` | LiveKit server URL (e.g. `ws://localhost:7880`) |
| `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` | LiveKit credentials |
| `AI_API_URL` | Syltalky AI API base URL (e.g. `http://localhost:8000`) |
| `LLM_BASE_URL` / `LLM_API_KEY` | OpenAI-compatible proxy for Qwen3.5-35B-A3B — used for meeting summaries and the AI chat assistant |
| `FRONTEND_URL` | CORS origin for the frontend (e.g. `http://localhost:5173`) |
| `GOOGLE_CLIENT_ID` | Google OAuth client ID (optional, for Sign in with Google) |

---

## API reference

### Auth  (`/auth`)

| Method | Path | Description |
|---|---|---|
| POST | `/auth/register` | Email + display_name + gender + password |
| POST | `/auth/login` | Returns JWT access + refresh tokens |
| POST | `/auth/refresh` | Exchange refresh token for a new access token |
| POST | `/auth/verify-email` | Confirm email from link token |
| POST | `/auth/forgot-password` | Send password-reset email via Resend |
| POST | `/auth/reset-password` | Token + new password |
| POST | `/auth/google` | Sign in / register with Google OAuth |
| POST | `/auth/complete-profile` | Set display_name + gender after Google sign-in |

### Users  (`/users`)

| Method | Path | Description |
|---|---|---|
| GET | `/users/me` | Current user profile |
| PATCH | `/users/me` | Update display_name or upload avatar |
| GET | `/users/me/voice-config` | Get TTS mode + active voice |
| PATCH | `/users/me/voice-config` | Set mode (`design`/`clone`), design tags, active profile |
| GET | `/users/by-ids` | Bulk fetch display_name + avatar by user ID list |

### Voice Profiles  (`/voices`)

| Method | Path | Description |
|---|---|---|
| GET | `/voices` | List user's voice profiles |
| POST | `/voices` | Upload ref audio → STT → register with AI API → store profile |
| DELETE | `/voices/{id}` | Delete profile |
| PATCH | `/voices/{id}` | Rename profile |

On startup, all stored voice profiles are automatically re-registered with the AI API (voice IDs are volatile — they live only in the AI API's memory).

### Meetings  (`/meetings`)

| Method | Path | Description |
|---|---|---|
| POST | `/meetings` | Create meeting + LiveKit room, return room code |
| GET | `/meetings` | List user's meetings (history) |
| GET | `/meetings/check/{room_code}` | Validate room code before joining |
| POST | `/meetings/join` | Join by room code → LiveKit participant token |
| GET | `/meetings/{id}` | Meeting detail (transcript, summary, participants) |
| POST | `/meetings/{id}/end` | Host ends meeting, triggers post-processing |
| POST | `/meetings/{id}/kick/{participant_id}` | Host kicks a participant |
| POST | `/meetings/{id}/tts` | Generate TTS audio, broadcast to all participants |
| POST | `/meetings/{id}/summarize` | Manually trigger LLM summary |
| POST | `/meetings/{id}/ask` | Ask the AI assistant about the meeting |
| GET | `/meetings/{id}/chat` | Retrieve in-meeting chat history |
| POST | `/meetings/webhook` | LiveKit webhook (participant events) |
| WS | `/meetings/{id}/waiting-ws` | Waiting room WebSocket (host approves/denies) |
| GET | `/meetings/{id}/waiting` | List pending waiting room requests |
| POST | `/meetings/{id}/approve/{request_id}` | Approve waiting room request |
| POST | `/meetings/{id}/approve-all` | Approve all pending waiting room requests at once |
| POST | `/meetings/{id}/deny/{request_id}` | Deny waiting room request |
| PATCH | `/meetings/{id}/waiting-room` | Toggle waiting room on/off |

### Captions  (`/meetings/{id}/captions`)

| Method | Path | Description |
|---|---|---|
| WS | `/meetings/{id}/captions` | Receive live captions + TTS audio events |

The backend taps each LiveKit participant's audio track, streams it to `/ws/stt` on the AI API, and broadcasts transcribed text to all WebSocket subscribers. TTS audio URLs are also delivered through this same channel.

### Meeting Extras  (`/meetings/{id}/...`)

| Method | Path | Description |
|---|---|---|
| GET | `/meetings/{id}/state` | Full meeting state (pins, polls, notes) |
| GET | `/meetings/{id}/co-hosts` | Get current co-host list |
| POST | `/meetings/{id}/co-hosts` | Set co-host list (host only) |
| POST | `/meetings/{id}/pins` | Pin a chat message |
| DELETE | `/meetings/{id}/pins/{pin_id}` | Unpin |
| POST | `/meetings/{id}/polls` | Create a poll |
| POST | `/meetings/{id}/polls/{poll_id}/vote` | Submit a vote |
| POST | `/meetings/{id}/polls/{poll_id}/close` | Close a poll |
| DELETE | `/meetings/{id}/polls/{poll_id}` | Delete a poll |
| POST | `/meetings/{id}/notes` | Create a collaborative note |
| PATCH | `/meetings/{id}/notes/{note_id}` | Rename a note |
| POST | `/meetings/{id}/notes/{note_id}/snapshot` | Save a snapshot of note content |
| DELETE | `/meetings/{id}/notes/{note_id}` | Delete a note |
| WS | `/meetings/{id}/notes/{note_id}/sync` | Yjs CRDT sync for collaborative editing |

### Sign Language  (`/sign`)

| Method | Path | Description |
|---|---|---|
| POST | `/sign` | Proxy to AI API `/sign` — ASL video → Vietnamese text |

---

## Database schema

```
users               — accounts (email, password hash, display_name, gender, avatar)
voice_profiles      — cloned voice profiles (ref audio path, ref text, active_voice_id)
user_voice_config   — per-user TTS settings (mode: design|clone, design tags, active profile)
meetings            — meeting records (room code, LiveKit room, transcript JSONB, summary)
meeting_participants — join/leave timestamps, kicked flag per user per meeting
meeting_waiting_requests — waiting room requests with status (pending/approved/denied/cancelled)
captions            — per-segment transcription rows (text, speaker, timestamp_ms, is_tts)
meeting_extras      — pinned_messages, polls, poll_votes, notes
```

Migrations are managed with Alembic (`alembic/versions/`).

---

## Post-processing pipeline

When a host ends a meeting (`POST /meetings/{id}/end`):

1. All caption rows for the meeting are fetched from the database.
2. A speaker-labelled transcript is assembled.
3. Qwen3.5-35B-A3B (via `LLM_BASE_URL`) summarises the transcript into bullet points (Vietnamese Markdown).
4. The summary and full transcript are stored on the `meetings` row.

---

## Project structure

```
Syltalky_BE/
├── app/
│   ├── main.py              ← FastAPI app, lifespan (bucket + voice re-registration)
│   ├── config.py            ← Settings (Pydantic BaseSettings, reads .env)
│   ├── database.py          ← Async SQLAlchemy engine + session factory
│   ├── core/
│   │   ├── deps.py          ← get_current_user dependency
│   │   ├── security.py      ← JWT creation/verification, password hashing
│   │   └── meeting_auth.py  ← require_host_or_cohost, require_participant helpers
│   ├── models/              ← SQLAlchemy ORM models
│   ├── schemas/             ← Pydantic request/response schemas
│   ├── routers/             ← One file per domain (auth, users, meetings, …)
│   └── services/
│       ├── minio_client.py  ← Upload, presigned URL, public URL generation
│       ├── email.py         ← Resend email wrapper
│       └── post_processing.py ← Transcript build + LLM summarise + notify
├── alembic/                 ← Migration scripts
├── docker-compose.yml
├── Dockerfile
└── .env.example
```
