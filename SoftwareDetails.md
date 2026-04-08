# 🚀 Onboarding AI Assistant

A production-ready **AI onboarding assistant** that answers employee
questions using only your company's internal documentation.

Built with: - FastAPI (async backend) - Local LLM via Ollama
(llama3.1) - RAG pipeline (pgvector + embeddings) - Role-based access
control (RBAC) - Fully containerized (Docker)

------------------------------------------------------------------------

## Features

-   Chat-based onboarding assistant\
-   Context-aware answers using internal docs\
-   Role-based document access\
-   Real-time streaming responses (SSE)\
-   Learning progress tracking\
-   Auto-generated conversation titles

------------------------------------------------------------------------

## Architecture

User → FastAPI → RAG Pipeline → PostgreSQL (pgvector) → Ollama →
Streaming Response

------------------------------------------------------------------------

## Project Structure

    onboarding_final1/
    ├── app/
    ├── api/
    ├── core/
    ├── db/
    ├── services/
    ├── rag/
    ├── ingestion/
    ├── frontend/
    ├── scripts/
    ├── docker-compose.yml
    ├── Dockerfile
    ├── requirements.txt
    └── README.md

------------------------------------------------------------------------

## Tech Stack

-   FastAPI
-   PostgreSQL + pgvector
-   SQLAlchemy
-   Ollama (LLM)
-   Docker

------------------------------------------------------------------------

## Run

``` bash
docker compose up -d
```

------------------------------------------------------------------------

## Summary

A fully local, privacy-first AI onboarding assistant using RAG.
