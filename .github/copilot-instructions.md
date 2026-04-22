# DAGMA-360 · Copilot Instructions

## Contexto

Monorepo meta con tres servicios independientes:
- `DEV_DIMENSION/APP_LAYER/back` — FastAPI + Firebase + S3
- `DEV_DIMENSION/APP_LAYER/front` — SvelteKit PWA + Vercel
- `DATA_DIMENSION/DBSM` — Postgres medallion en Railway

Solo `back/` y `front/` son repos git con remote. El root no se versiona.

## Reglas

1. **Ports & adapters.** Jamás importes `boto3`/`litellm`/`firebase_admin` en services. Usa un Port.
2. **Async siempre** en backend. httpx no requests.
3. **Pydantic en fronteras**, dataclasses dentro.
4. **Errores estructurados**: `{"error":{"code":"X","message":"..."}}`.
5. **Local-first**. `LLM_DEFAULT_MODEL=ollama/qwen2.5:14b` en dev.
6. **Tipos estrictos** en front. Zero `any` sin comentario justificante.
7. **Mobile-first** UI (touch ≥44px, offline-first).
8. **Medallion en DB** (bronze/silver/gold). Nunca DDL manual en prod.

## Hardware

RTX 5070 Ti 16GB VRAM · Ryzen 9 9950X · 61GB RAM. Usa modelos locales cuando sea razonable.

## Skills referencia

- Backend: `AI_DIMENSION/agents/creed/skills/backend/SKILL.md`
- Frontend: `AI_DIMENSION/agents/creed/skills/frontend/SKILL.md`
- DB: `AI_DIMENSION/agents/creed/skills/DBSM-Postgresql/SKILL.md`

## Comandos

- Dev up: `.\BUILDER\config_helper\dev_up.ps1`
- Push+PR: `.\DEVOps_DIMENSION\CI_CD\scripts\push_dev.ps1 <back|front> "msg"`
- Possess: `.\AI_DIMENSION\agents\creed\spirit\possession\possess.ps1`
