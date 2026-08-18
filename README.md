# AI Study Companion — Backend

The server-side of the AI Study Companion, a full-stack web application built as a Final Year Project at Universiti Sains Malaysia. The backend is a FastAPI application that handles multi-modal document ingestion, a configurable RAG retrieval pipeline, LangGraph-powered agentic chat with streaming, exam-aware study material generation, AI-powered note-taking, and user management.

---

## Tech Stack

| Technology | Version | Purpose |
|---|---|---|
| Python | 3.13 | Core language |
| FastAPI | 0.135.x | API framework |
| Uvicorn | 0.41.x | ASGI server |
| LangGraph | via LangChain | Agent orchestration |
| LangChain | 1.0.8 | LLM abstractions, prompt templates, tool definitions |
| LangChain OpenAI | 1.0.3 | OpenAI integration |
| LangChain Tavily | 0.2.13 | Web search tool for the Supervisor Agent |
| OpenAI GPT-4o | via openai 2.30.x | Chat, generation, vision for image-containing chunks |
| OpenAI text-embedding-3-large | via openai | Vector embeddings at 1536 dimensions |
| Unstructured.io | 0.18.11 (all-docs) | Multi-modal document partitioning |
| Supabase | 2.28.x | PostgreSQL database and pgvector for vector search |
| NumPy | 2.4.x | Cosine similarity matrix for cross-referencing |
| Celery | 5.6.x | Background task queue |
| Redis | 7.3.x (client) | Celery broker |
| boto3 | 1.42.x | AWS S3 client |
| ScrapingBee | 2.0.x | Web scraping for URL-based ingestion |
| Stripe | 15.2.x | Payment processing |
| Clerk Backend API | 5.0.x | JWT verification and authentication |
| structlog | 25.5.x | Structured JSON logging |
| RAGAS | 0.4.x | RAG pipeline evaluation |
| Poetry | 2.x | Dependency management |

---

## Architecture Overview

Three processes run together to make the application work:

**FastAPI server** handles all HTTP requests, authentication, and business logic.

**Celery worker** runs document ingestion and study material generation as background jobs so heavy processing never blocks the API.

**Redis** acts as the message broker between the API and the Celery worker.

---

## Project Structure

```
fyp-server/
├── src/
│   ├── agents/
│   │   ├── simple_agent/agent.py          # ReAct agent with RAG tool and guardrails
│   │   └── supervisor_agent/agent.py      # Multi-agent supervisor (RAG + web search)
│   ├── config/
│   │   ├── index.py                       # App config from environment variables
│   │   └── logging.py                     # structlog setup with request/user/project context vars
│   ├── features/
│   │   ├── cross_reference.py             # Cosine similarity cross-referencing (numpy)
│   │   ├── flashcards.py                  # Flashcard generation, exam-aware
│   │   ├── generate.py                    # Feature generation orchestrator
│   │   ├── mind_map.py                    # MindElixir-compatible JSON mind map generation
│   │   ├── practice_questions.py          # MCQ, short answer, and paragraph question generation
│   │   ├── summary.py                     # Summary generation with map-reduce for large docs
│   │   └── utils.py                       # Shared helpers: get chunks, merge contents, generate title
│   ├── middleware/
│   │   └── logging_middleware.py          # Request logging with request_id, path, duration, status
│   ├── models/index.py                    # All Pydantic request/response models and enums
│   ├── rag/
│   │   ├── ingestion/
│   │   │   ├── index.py                   # Full ingestion pipeline: download, partition, chunk, summarize, embed
│   │   │   └── utils.py                   # Partition, image extraction, chunking, AI summary helpers
│   │   └── retrieval/
│   │       ├── index.py                   # Retrieval strategies: basic, hybrid, multi-query variants
│   │       └── utils.py                   # RRF fusion, query variation generation, context builder
│   ├── routes/
│   │   ├── chatRoutes.py                  # Chat CRUD
│   │   ├── featureRoutes.py               # Feature generation, merge, expand, quiz grading
│   │   ├── notesRoutes.py                 # Notes CRUD and AI conversation
│   │   ├── paymentRoutes.py               # Stripe checkout and webhook
│   │   ├── projectFilesRoutes.py          # File upload, URL ingestion, document management
│   │   ├── projectRoutes.py               # Project CRUD, settings, message sending and SSE streaming
│   │   └── userRoutes.py                  # Clerk webhook for user creation
│   ├── services/
│   │   ├── awsS3.py                       # S3 client
│   │   ├── celery.py                      # Celery app and task definitions
│   │   ├── clerkAuth.py                   # JWT verification FastAPI dependency
│   │   ├── llm.py                         # OpenAI LLM and embeddings instances
│   │   ├── stripe_service.py              # Stripe checkout, webhook, plan catalog
│   │   ├── supabase.py                    # Supabase client
│   │   └── webScrapper.py                 # ScrapingBee client
│   ├── utils/index.py                     # URL validation helper
│   └── server.py                          # FastAPI app, middleware registration, router mounting
├── evaluation/
│   ├── ragas_data_collection.py           # Runs test questions through the RAG pipeline
│   ├── run_evaluation.py                  # RAGAS evaluation: faithfulness, relevancy, precision, recall
│   └── datasets/                          # Evaluation datasets and results CSV
├── redis/
│   └── docker-compose.yaml                # Standalone Redis container for local development
├── supabase/
│   └── migrations/
│       ├── 20260310120153_initial_schema.sql   # Core tables and HNSW vector index
│       └── 20260319100121_chunk_search_func.sql # vector_search and keyword_search RPC functions
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── start_redis.sh
├── start_server.sh
├── start_worker.sh
└── stopAll.sh
```

---

## Core Pipelines

### Ingestion Pipeline

When a document is uploaded or a URL is submitted, the following steps run as a Celery background task:

**Step 1 — Download**
Files are downloaded from S3 to a temp path. URLs are crawled via ScrapingBee and saved as HTML. Supported file types: PDF, DOCX, PPTX, TXT, MD, HTML.

**Step 2 — Partition**
Unstructured.io partitions the document into atomic elements: Text, Tables, Images, Titles, Headers.
- PDFs use high-resolution strategy with table structure inference and image block extraction.
- DOCX files use a dual-pass image extractor: relationship scanning via python-docx, with a raw ZIP media-folder fallback to recover any images missed.
- PPTX files use the same dual-pass approach via python-pptx, deduped by byte hash so no image appears twice.

**Step 3 — Chunk by title**
`chunk_by_title` from Unstructured groups elements into semantically coherent chunks. A post-processing step re-attaches any images that were misplaced or dropped during chunking, matched to chunks by page number.

**Step 4 — Summarize rich chunks**
Any chunk containing tables or images is sent to GPT-4o (vision-enabled) to produce a searchable AI summary. This makes visual content retrievable by meaning, not just metadata.

**Step 5 — Embed and store**
Each chunk's content is embedded with `text-embedding-3-large` (1536 dimensions) in batches of 10, with exponential backoff retry. Results are inserted into `document_chunks` in Supabase with the embedding stored as a pgvector column.

After ingestion completes, a chained Celery task automatically generates all four study features (summary, mind map, flashcards, practice questions) for the document.

---

### Retrieval Pipeline

Four strategies are configurable per project from the settings panel:

**Basic** — single vector search using cosine similarity via the pgvector HNSW index.

**Hybrid** — vector search and keyword search run in parallel, then fused with Reciprocal Rank Fusion (RRF) using configurable vector/keyword weights.

**Multi-Query Vector** — GPT-4o generates N query variations from the user's question. Each variation runs a vector search. All results are fused with RRF.

**Multi-Query Hybrid (most optimized)** — GPT-4o generates N query variations. Each runs both vector and keyword search. All results across all queries and both search types are fused together with RRF, giving approximately 30 candidate chunks.

If reranking is enabled, the top candidate chunks are passed to a Cohere cross-encoder reranker. The reranker reads the actual text of each chunk and the original query side by side and scores them by genuine relevance, selecting the top 5 to 10 chunks for the generation step.

---

### Generation Pipeline

Every user message goes through the following before anything reaches the LLM:

**Input Guardrails** — a structured-output call to GPT-4o-mini checks for toxicity, prompt injection attempts, and PII. If any check fails, the request is rejected with a clear explanation.

If the check passes, the message is routed to one of two agents based on the project's `agent_type` setting:

**Simple Agent** — a ReAct agent built with LangGraph. Has a single RAG search tool that runs the retrieval pipeline and returns grounded results with citations.

**Supervisor Agent (Multi-Agent)** — a supervisor that coordinates two sub-agents as callable tools. The RAG sub-agent searches the project's documents. The Web Search sub-agent uses Tavily (or DuckDuckGo as fallback) to search the internet in real time. The supervisor decides which agent(s) to call based on the question and synthesizes the results.

Both agents use a custom `CustomAgentState` that extends LangGraph's `MessagesState` to accumulate citations across all tool calls, so citations from every retrieved chunk are returned in the final response.

Responses stream back token-by-token over Server-Sent Events with granular status events: "Thinking...", "Searching documents...", "Searching the web...", "Generating response...".

---

### Exam-Aware Cross-Referencing

When a user tags a document as `past_year_paper` and generates study materials with both lecture notes and a past year paper selected, the system cross-references them:

1. Fetch pre-computed embedding vectors for all lecture chunks and all past year chunks from the database (two queries, no new embedding API calls).
2. L2-normalize both sets of embeddings and compute the full cosine similarity matrix in numpy.
3. For each lecture chunk, find the best-matching past year chunk and record the similarity score.
4. Filter by a similarity threshold to keep only meaningful matches.

The resulting linkage structure (lecture excerpt, matched exam excerpt, similarity score, page numbers from both documents) is passed directly into the generation prompts for summary, flashcards, and practice questions. Every generated output then explicitly flags exam-relevant content with `is_past_year: true` and an `exam_similarity` score.

---

## API Routes

### Projects `/api/projects`
| Method | Endpoint | Description |
|---|---|---|
| GET | `/` | List all projects |
| POST | `/` | Create a project with default settings |
| DELETE | `/{project_id}` | Delete project and all related data (CASCADE) |
| GET | `/{project_id}` | Get project details |
| GET | `/{project_id}/chats` | List project chats |
| GET | `/{project_id}/settings` | Get RAG and agent settings |
| PUT | `/{project_id}/settings` | Update RAG and agent settings |
| POST | `/{project_id}/chats/{chat_id}/messages` | Send a message (JSON response) |
| POST | `/{project_id}/chats/{chat_id}/messages/stream` | Send a message (SSE streaming) |

### Files `/api/projects`
| Method | Endpoint | Description |
|---|---|---|
| GET | `/{project_id}/files` | List project documents |
| POST | `/{project_id}/files/upload-url` | Generate S3 presigned upload URL |
| POST | `/{project_id}/files/confirm` | Confirm upload and trigger ingestion task |
| POST | `/{project_id}/urls` | Add a website URL for ingestion |
| DELETE | `/{project_id}/files/{file_id}` | Delete document from S3 and database |
| GET | `/{project_id}/files/{file_id}/chunks` | Inspect document chunks |

### Features `/api/projects`
| Method | Endpoint | Description |
|---|---|---|
| PUT | `/{project_id}/files/{document_id}/tag` | Tag as `lecture_notes` or `past_year_paper` |
| POST | `/{project_id}/features/generate` | Generate a feature, exam-aware when past year is selected |
| POST | `/{project_id}/features/merge` | Merge features from multiple documents into one generated source |
| POST | `/{project_id}/sources/{source_id}/expand` | Generate more flashcards or questions and append to existing source |
| GET | `/{project_id}/sources` | List all generated sources |
| DELETE | `/{project_id}/sources/{source_id}` | Delete a generated source |
| GET | `/{project_id}/documents/{document_id}/features` | Get all features for a document |
| POST | `/{project_id}/quiz/evaluate` | AI grading for short and long answer questions |

### Chats `/api/chats`
| Method | Endpoint | Description |
|---|---|---|
| POST | `/` | Create a chat |
| DELETE | `/{chat_id}` | Delete a chat |
| GET | `/{chat_id}` | Get chat with all messages |

### Notes `/api/projects`
| Method | Endpoint | Description |
|---|---|---|
| POST | `/{project_id}/notes` | Create a note |
| GET | `/{project_id}/notes` | List all notes in a project |
| GET | `/{project_id}/notes/{note_id}` | Get a single note |
| PUT | `/{project_id}/notes/{note_id}` | Update a note |
| DELETE | `/{project_id}/notes/{note_id}` | Delete a note and its conversation history |
| GET | `/{project_id}/notes/{note_id}/conversation` | Load AI conversation history for a note |
| DELETE | `/{project_id}/notes/{note_id}/conversation` | Clear conversation history for a note |
| POST | `/{project_id}/notes/{note_id}/ask` | Ask the AI a question about a specific note |

### Payments `/api/payments`
| Method | Endpoint | Description |
|---|---|---|
| GET | `/me` | Get current user's plan, credits, and limits |
| GET | `/plans` | Get public plan catalog |
| POST | `/create-checkout-session` | Start a Stripe Checkout session |
| POST | `/webhook` | Stripe webhook receiver (signature-verified) |

### Users `/api/user`
| Method | Endpoint | Description |
|---|---|---|
| POST | `/create` | Clerk webhook for user creation on sign-up |

---

## Getting Started

### Prerequisites

- Python 3.13
- Poetry
- Docker (for Redis)
- A Supabase project with pgvector enabled
- An AWS S3 bucket
- OpenAI API key
- Clerk application
- ScrapingBee API key

Optional:
- Cohere API key (for reranking)
- Tavily API key (for web search in Supervisor Agent)
- Stripe keys (for payment plans)

---

### Installation

```bash
git clone <repo-url>
cd fyp-server
pip install poetry
poetry install
```

The `unstructured[all-docs]` extra installs all document format parsers including PDF, DOCX, PPTX, and HTML support. The Dockerfile also installs the required system libraries: `poppler-utils` (PDF rendering), `tesseract-ocr` (OCR fallback), `libmagic-dev`, `libgl1`, and `libglib2.0-0`.

---

### Environment Variables

Create a `.env` file in the root of the project:

```env
# Supabase
SUPABASE_API_URL=https://your-project.supabase.co
SUPABASE_SECRET_KEY=your_supabase_service_role_key

# Clerk
CLERK_SECRET_KEY=sk_live_...
DOMAIN=http://localhost:3000

# AWS S3
S3_BUCKET_NAME=your-bucket-name
AWS_REGION=ap-southeast-1
AWS_ACCESS_KEY_ID=your_access_key
AWS_SECRET_ACCESS_KEY=your_secret_key

# Redis
REDIS_URL=redis://localhost:6379/0

# OpenAI
OPENAI_API_KEY=sk-...

# ScrapingBee
SCRAPINGBEE_API_KEY=your_scrapingbee_key

# Optional — Tavily (web search for Supervisor Agent)
TAVILY_API_KEY=tvly-...

# Optional — Stripe (payment plans)
STRIPE_SECRET_KEY=sk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...
FRONTEND_URL=http://localhost:3000

# Logging
LOG_LEVEL=INFO
```

---

### Running Locally

Run each process in a separate terminal. All scripts automatically navigate to the correct directory.

**Terminal 1 — Redis**
```bash
./start_redis.sh
```
This starts Redis using Docker Compose from the `redis/` directory.

**Terminal 2 — API Server**
```bash
./start_server.sh
```
This runs Uvicorn with `--reload` via Poetry. The API will be available at `http://localhost:8000`. Auto-generated docs are at `http://localhost:8000/docs`.

**Terminal 3 — Celery Worker**
```bash
./start_worker.sh
```
This starts the Celery worker using threads pool mode via Poetry.

**To stop everything:**
```bash
./stopAll.sh
```
This kills the Celery worker, stops Redis via Docker Compose, and kills Uvicorn.

---

### Running with Docker Compose

The `docker-compose.yml` at the root runs all three services together: Redis, the API server, and the Celery worker. The API and worker share the same image build.

```bash
# Build and start everything
docker-compose up --build

# Stop everything
docker-compose down
```

The worker reads the same `.env` file as the API server. Redis data is persisted in a named Docker volume (`redis_data`) with AOF enabled.

---

## Supabase Setup

### Step 1 — Enable Extensions

In the Supabase SQL editor, run:

```sql
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "vector";
```

### Step 2 — Run the Initial Schema Migration

Run the file `supabase/migrations/20260310120153_initial_schema.sql`.

This creates the following tables:

- `users` — clerk_id, created_at
- `projects` — id, name, description, clerk_id
- `project_settings` — per-project RAG configuration (strategy, agent type, chunk sizes, weights, reranking)
- `project_documents` — document records with processing status, S3 key, source type, source tag
- `document_chunks` — individual chunks with content, embedding vector(1536), full-text search tsvector, page number, original content (JSON with text, tables, images)
- `chats` — per-project conversations
- `messages` — individual messages with role, content, and citations JSON

It also creates two indexes on `document_chunks`:

- `document_chunks_fts_idx` — GIN index on the tsvector column for keyword search
- `document_chunks_embedding_hnsw_idx` — HNSW index using inner product for vector similarity search

### Step 3 — Run the Search Functions Migration

Run the file `supabase/migrations/20260319100121_chunk_search_func.sql`.

This creates two RPC functions the retrieval pipeline calls directly:

**`vector_search_document_chunks`** — performs cosine similarity search filtered by document IDs, with a configurable similarity threshold and result limit.

**`keyword_search_document_chunks`** — performs full-text keyword search using PostgreSQL's `websearch_to_tsquery`, ranked by `ts_rank_cd`.

### Step 4 — Run Additional Schema Snippets

Run these in order from `supabase/snippets/`:

**Add feature columns to `project_documents`:**
```sql
ALTER TABLE project_documents
  ADD COLUMN IF NOT EXISTS summary TEXT,
  ADD COLUMN IF NOT EXISTS mind_map TEXT,
  ADD COLUMN IF NOT EXISTS features_status TEXT DEFAULT 'pending';
```

**Create `generated_sources` table:**
```sql
CREATE TABLE IF NOT EXISTS generated_sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    clerk_id TEXT NOT NULL REFERENCES users(clerk_id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    source_type TEXT NOT NULL,
    content TEXT NOT NULL,
    document_ids UUID[] NOT NULL,
    total_sources INTEGER NOT NULL DEFAULT 1,
    expand_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_generated_sources_project
  ON generated_sources(project_id, source_type);
```

**Add exam-aware columns:**
```sql
ALTER TABLE project_documents
  ADD COLUMN IF NOT EXISTS source_tag TEXT DEFAULT 'lecture_notes',
  ADD COLUMN IF NOT EXISTS flashcards TEXT,
  ADD COLUMN IF NOT EXISTS practice_questions TEXT;
```

**Create `notes` and `note_conversations` tables:**
```sql
CREATE TABLE notes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    clerk_id TEXT NOT NULL REFERENCES users(clerk_id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE note_conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    note_id UUID NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    project_id UUID NOT NULL,
    clerk_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_notes_project_id ON notes(project_id);
CREATE INDEX idx_notes_clerk_id ON notes(clerk_id);
```

**Add payments support:**
```sql
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS plan TEXT NOT NULL DEFAULT 'free',
  ADD COLUMN IF NOT EXISTS credits INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT,
  ADD COLUMN IF NOT EXISTS plan_purchased_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS email TEXT;

CREATE TABLE IF NOT EXISTS payments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    clerk_id TEXT NOT NULL REFERENCES users(clerk_id) ON DELETE CASCADE,
    plan TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'usd',
    credits_granted INTEGER NOT NULL DEFAULT 0,
    stripe_checkout_session_id TEXT UNIQUE,
    stripe_payment_intent_id TEXT,
    stripe_customer_id TEXT,
    status TEXT NOT NULL DEFAULT 'succeeded',
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now()
);
```

---

## Plan Limits

| Feature | Free | Pro ($5) | Elite ($20) |
|---|---|---|---|
| Max projects | 3 | 15 | 100 |
| Max docs per project | 5 | 20 | 50 |
| Max pages per doc | 20 | 100 | 300 |
| Max chats per project | 2 | 10 | Unlimited |
| Messages per chat | 10 | Unlimited | Unlimited |
| Feature generations | 1 per doc | Unlimited | Unlimited |
| Expand per source | 0 | 1 | Unlimited |

---

## RAG Evaluation

The `evaluation/` directory contains RAGAS-based evaluation tooling used to measure retrieval quality.

**Data collection** (`ragas_data_collection.py`) runs a set of test questions through the live RAG pipeline and saves the question, retrieved contexts, and generated answer to a JSON dataset.

**Evaluation** (`run_evaluation.py`) loads the dataset and scores it across four RAGAS metrics:
- Faithfulness — does the answer stick to the retrieved context
- Answer Relevancy — does the answer actually address the question
- Context Precision — are the retrieved chunks relevant to the question
- Context Recall — did retrieval find all the chunks needed to answer

Results are saved to `evaluation/datasets/results.csv`.

To run the evaluation:

```bash
# Step 1: collect data against your live project
poetry run python evaluation/ragas_data_collection.py

# Step 2: run the RAGAS evaluation
poetry run python evaluation/run_evaluation.py
```

---

## Deployment

The backend is containerized with Docker. For AWS ECS Fargate deployment:

```bash
# Authenticate to ECR
aws ecr get-login-password --region ap-southeast-1 | docker login --username AWS --password-stdin <account-id>.dkr.ecr.ap-southeast-1.amazonaws.com

# Build and push
docker build -t study-ai-companion .
docker tag study-ai-companion:latest <ecr-uri>:latest
docker push <ecr-uri>:latest
```

The Celery worker runs as a separate ECS task using the same image with a different command override:

```
celery -A src.services.celery:celery_app worker --loglevel=info --pool=threads
```

HTTPS is handled via AWS CloudFront in front of the Application Load Balancer. Redis should be replaced with AWS ElastiCache (Redis) in production.

---

## Related

Frontend repository: https://github.com/mueed26/fyp-client.git
