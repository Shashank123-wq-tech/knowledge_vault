# Understand and rebuild KnowledgeVault

## 1. Begin with the problem

You keep notes on FastAPI, databases, and ML systems. A file name may not tell you where a
particular idea occurs. This API stores the notes, indexes small passages, and returns the
matching passage with its source. Multiple people can use the same backend while each sees
only their own documents.

The system has two processes: the API responds to requests; the worker processes saved jobs.
Their shared database is the source of truth. A process can stop without losing queued work.

## 2. Data relationships

| Record | Parent | Important invariant |
|---|---|---|
| User | None | Unique normalized username; password hash only |
| Session token | User | Token hash plus expiry; raw token is returned once |
| Collection | User | Name unique within that user's collections |
| Document | Collection | Text hash unique within the collection |
| Job | Document | One reusable indexing job per document |
| Chunk | Document | Unique zero-based chunk number within document |
| FTS row | Chunk | Automatically synchronized by SQL triggers |

Ownership is resolved through the collection. The server never accepts an owner ID in the
request body. Authentication supplies the user ID; queries constrain resources to that user.
Deletion cascades down the relationships, and chunk deletion triggers remove indexed text.

## 3. Walk through one document

1. A login verifies the password and returns a random bearer token.
2. The client submits title and text to a collection endpoint.
3. Pydantic checks the content; the authorization dependency resolves the user.
4. A transaction checks ownership, inserts the document, and inserts its queued job.
5. The API returns 202 and the two IDs. It does not promise that the text is indexed yet.
6. The worker claims a job, increments attempts, and writes a fresh lease token.
7. Outside that transaction it splits the document into overlapping word chunks.
8. A second transaction verifies the lease, writes chunks, and marks the job successful.
9. Search matches terms against the FTS index, restricts results to the owner, and ranks passages.

## 4. Why a separate worker?

A function scheduled only in the API process is lost if that process dies before finishing.
Here the queued row already exists in SQLite. The worker can find it after a restart.
The lease adds recovery for a worker that crashes after claiming work. A random lease token
prevents an old process from publishing after someone else has reclaimed the same job.

| Job event | Job status | Document status |
|---|---|---|
| Create / replace / explicit retry | queued | queued |
| Worker claims | running | indexing |
| Atomic publication | succeeded | ready |
| Failure with attempts remaining | queued | queued |
| Third failure or expired final lease | failed | failed |
| Delete document or collection | Row removed | Row removed |

The worker does not heartbeat. Each current document is bounded and indexing is small local
work. If you add slow OCR, embeddings, or external calls, extend the lease design with periodic
renewal and cooperative cancellation rather than assuming 60 seconds is always sufficient.

## 5. Why transactions matter

**Ingestion:** saving a document without a job would leave it permanently unindexed.
Both inserts therefore commit together.

**Publication:** search must not observe half of the chunks as a completed document.
The chunks, FTS trigger effects, and successful status commit together.

**Replacement:** old chunks are removed and the old lease is invalidated together.
A worker holding the previous text cannot publish after replacement.

**Concurrent claims:** `BEGIN IMMEDIATE` reserves the SQLite write transaction before selecting
a candidate. The next worker sees the committed lease rather than claiming the same queued row.
The tests use two threads and prove only one claim succeeds for one queued job.

## 6. Understand retrieval

Exact matching here means matching tokenized words, not raw substring matching.
For example, querying `FastAPI validation` in `all` mode finds a chunk containing both words.
In `any` mode either word is sufficient. Overlap helps when relevant content crosses a boundary.

FTS5 computes BM25 ranking, which considers term frequency, corpus frequency, and document
length. SQLite's native BM25 scores sort with smaller values first. The API negates that value
so its exposed `score` sorts higher first. It is not a percentage confidence.

Token search does not understand that "authentication" and "sign in" express related ideas.
That is a possible future reason to add embeddings and evaluate hybrid retrieval. Do not label
this implementation an LLM, an AI model, or a semantic search system.

## 7. Read the code in this order

1. `schemas.py`: understand the API's input/output contracts.
2. `database.py`: trace the ownership chain, unique constraints, and FTS triggers.
3. `security.py`: separate password hashing from high-entropy token hashing.
4. `routes.py`: read register/login, collection creation, document ingestion, then search.
5. `indexing.py`: trace claim, compute, publish, and retry independently.
6. `worker.py`: see how a process repeatedly calls the indexing service.
7. `main.py` and `middleware.py`: startup, body limits, timing, and error boundaries.
8. `tests/test_api.py`: follow both normal behavior and interrupted-worker cases.

Synchronous routes use ordinary `def` because hashing and SQLite are blocking operations.
The asynchronous middleware handles ASGI request messages and delegates route execution.
There is no benefit in adding `async` around a blocking database call without changing the I/O layer.

## 8. Rebuild as checkpoints

| Checkpoint | Outcome | Evidence |
|---|---|---|
| A | App factory and database | Health works and data survives restart |
| B | Registration and login | Stored password differs from input; expired token fails |
| C | Collections and ownership | Another user's ID always returns 404 |
| D | Document and queued job | Both records exist after 202 |
| E | Worker and FTS | Successful job produces a relevant passage |
| F | Recovery and replacement | Stale lease cannot publish obsolete content |
| G | Tests and demo | Normal workflow and failure scenarios pass |

## 9. Interview explanation

"I built a multi-user FastAPI document search service with private collections and SQLite FTS5
retrieval. Document ingestion atomically creates a durable indexing job. A separate Python
worker uses leases, bounded retries, and atomic index publication so it can recover from
crashes without stale workers overwriting newer content. Tests cover cross-user access,
revoked sessions, search cleanup, concurrent claims, and crash recovery."

Discuss why this is at-least-once rather than exactly-once execution, why 202 differs from 201,
how foreign keys and triggers cooperate, and why every resource endpoint needs authorization.

## 10. Future extensions (not included)

For your AI-engineering direction, a natural continuation is to add a retrieval benchmark,
embeddings, hybrid BM25/vector ranking, and then an answer endpoint with source citations.
Measure retrieval quality first: create query-to-relevant-document labels and evaluate Recall@k
or MRR. Keep generated answers separate from retrieval evaluation.

For larger infrastructure, use a server database, explicit migrations, a distributed queue,
per-user quotas enforced at the service boundary, centralized rate limits, and worker heartbeats.
The current version deliberately keeps the entire running stack on one machine.
