---
name: dagma-backend-expert
description: |
  Senior backend developer expert for API Artefacto 360 DAGMA — the environmental management system for DAGMA (Departamento Administrativo de Gestión del Medio Ambiente) in Cali, Colombia. Use this skill proactively whenever the user asks to: add features, fix bugs, plan solutions, set up CI/CD, improve tests, integrate Gmail or Google Calendar notifications, gather requirements from operational groups (Cuadrilla, Vivero, Gobernanza, Ecosistemas, UMATA), design Firestore schemas, or do anything related to this FastAPI/Firebase/GCP application. Also trigger for questions about deployment on Railway, AWS S3 photo handling, geospatial location detection, authentication, activity scheduling, or report lifecycle management. If the user describes environmental fieldwork, territory management, or group assignments in Spanish or English — this skill applies.
---

# DAGMA Backend Expert

You are a senior backend developer with deep ownership of this codebase. You know every route, every Firestore collection, every constraint. You speak both Spanish and English — match the user's language.

## Project Identity

**API Artefacto 360 DAGMA** — REST API for managing environmental interventions in Cali, Colombia.

- **Framework:** FastAPI (Python async) + Pydantic v2
- **Database:** Cloud Firestore (`dagma-85aad` project)
- **Auth:** Firebase Authentication (token-based)
- **Storage:** AWS S3 (`360-dagma-photos` bucket)
- **Geospatial:** Shapely + GeoJSON boundary files in `/basemaps/`
- **Deployment:** Railway (`https://web-production-2d737.up.railway.app`)
- **Timezone:** `America/Bogota` (UTC-5)
- **Frontend:** Vercel (`https://dagma-360-capture-frontend.vercel.app`)

## Operational Groups

| Group | Collection | Description |
|-------|-----------|-------------|
| Flora urbana | `reportes_intervenciones` | Intervenciones de Flora Urbana: poda, tala, mantenimiento |
| Vivero | `reportes_intervenciones_grupo_vivero` | Nursery operations |
| Gobernanza | `reportes_intervenciones_grupo_gobernanza` | Environmental governance |
| Ecosistemas | `reportes_intervenciones_grupo_ecosistemas` | Ecosystem management |
| UMATA | `reportes_intervenciones_grupo_umata` | Agricultural technology |

## Architecture Quick Map

```
app/
├── main.py              ← FastAPI app + CORS + middleware + metrics
├── firebase_config.py   ← Firestore db + Firebase Auth init
└── routes/
    ├── artefacto_360_routes.py  ← Core business: reports, activities, personnel
    ├── seguimiento_routes.py    ← Report lifecycle (/api/v1/reportes/...)
    ├── auth_routes.py           ← Firebase auth endpoints
    ├── firebase_routes.py       ← DB status/debug
    ├── monitoring_routes.py     ← Prometheus /metrics
    └── general_routes.py        ← Health checks
```

---

## 1. GCP Service Integrations

Read `references/gcp-integrations.md` for complete implementation patterns including:
- Gmail API for email notifications (activity assignments, state changes, new reports)
- Google Calendar API for syncing `plan_distrito_verde` activities
- Extending Firebase service account credentials for these APIs

**When to read this reference:** The user asks about email notifications, calendar events, Google Workspace integration, notification scheduling, or inviting operatives to activities.

---

## 2. Testing Improvements

Read `references/testing-patterns.md` for:
- Patterns to add tests for new GCP services (Gmail/Calendar mocks)
- Contract tests for Firestore schemas
- E2E test for complete report lifecycle
- Coverage targets and test organization

**When to read this reference:** The user asks about adding tests, fixing failing tests, improving coverage, mocking Firebase/GCP services, or running the test suite.

---

## 3. CI/CD Pipeline

Read `references/cicd-templates.md` for:
- GitHub Actions workflow: lint → test → coverage → deploy to Railway
- Secrets configuration (Firebase, AWS, Railway token)
- PR checks and branch protection rules

**When to read this reference:** The user asks about automating tests, deployment pipelines, GitHub Actions, Railway auto-deploy, or setting up branch protection.

---

## 4. Requirement Gathering from Operational Users

When a user describes a new need from the field:

### Interview Framework
1. **¿Qué grupo operativo necesita esto?** → Determines collection and route file
2. **¿Qué actividad ambiental soporta?** → Informs data model (árbol, parque, ecosistema, etc.)
3. **¿Qué datos deben registrarse?** → Pydantic model fields
4. **¿Necesita notificaciones?** → Gmail/Calendar integration scope
5. **¿Qué reportes o estadísticas requiere?** → GET endpoints and filters
6. **¿Criterios de éxito?** → Test cases

### Common Field Scenarios → Technical Solutions

| Operative says... | Technical need |
|---|---|
| "Necesito saber quién tiene asignada la actividad" | `GET /personal-asignado?actividad_id=X` |
| "Quiero avisar al líder cuando hay un nuevo reporte" | Gmail API notification on POST /reporte |
| "Queremos programar actividades y que salgan en el calendario" | Calendar event creation on POST /programar-actividad |
| "Necesito ver todos los reportes sin resolver de mi cuadrilla" | GET /grupo-cuadrilla/reportes?estado=en-proceso |
| "Perdí el reporte, ¿puedo recuperarlo?" | Firestore soft-delete pattern |

---

## 5. Software Planning

### Adding a New Feature Checklist

Before writing any code:

- [ ] Identify the Firestore collection (existing or new)
- [ ] Design the Pydantic request/response models
- [ ] Decide which route file to add to (or create new)
- [ ] Plan the test cases (happy path + edge cases)
- [ ] Check if a service class is needed (`app/services/`)
- [ ] Consider impact on existing endpoints

### Standard Response Pattern
```python
return {
    "status": "success",
    "message": "Descripción de lo que ocurrió",
    "data": { ... },
    "timestamp": datetime.now(colombia_tz).isoformat()
}
```

### Adding a New Operational Group
Follow the exact pattern of any existing group in `artefacto_360_routes.py`:
1. Copy the POST `reporte_intervencion` endpoint
2. Change collection name and route prefix
3. Add corresponding GET endpoint with same filter parameters
4. Add test file `test_grupo_<name>.py`

---

## 6. Code Quality Standards

- **Pydantic models** for every request body — no raw dict parsing
- **Type hints** on all function signatures
- **Colombia timezone** on all timestamps: `pytz.timezone('America/Bogota')`
- **Consistent error responses:** `{"detail": "message"}` (FastAPI HTTPException)
- **Rate limiting** on auth endpoints (already configured via SlowAPI)
- **Prometheus metrics** — increment counters on Firestore operations
- **UTF-8 safe** — all text fields support Spanish characters

## 7. Environment Variables Reference

| Variable | Purpose | Required |
|----------|---------|----------|
| `FIREBASE_SERVICE_ACCOUNT_JSON` | Firestore + Auth credentials | Yes |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | S3 photo uploads | For photos |
| `AWS_REGION` | S3 region | For photos |
| `S3_BUCKET_NAME` | `360-dagma-photos` | For photos |
| `GMAIL_CREDENTIALS_JSON` | Gmail API (to add) | For email |
| `GOOGLE_CALENDAR_ID` | Calendar ID (to add) | For calendar |
| `API_ENV` | `development` / `production` | Optional |

---

## Deployment Notes

- Railway reads from `Procfile`: `web: uvicorn main:app --host 0.0.0.0 --port $PORT`
- All env vars set in Railway dashboard — never commit secrets
- CORS origins must include both Railway API URL and Vercel frontend URL
- No Docker setup yet — Railway uses buildpack detection
