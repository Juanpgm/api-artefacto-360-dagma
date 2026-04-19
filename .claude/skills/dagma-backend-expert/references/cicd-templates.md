# CI/CD Templates — DAGMA API

## GitHub Actions: Main CI Pipeline

Create `.github/workflows/ci.yml` in the repository root:

```yaml
name: CI/CD Pipeline

on:
  push:
    branches: [master, main]
  pull_request:
    branches: [master, main]

env:
  PYTHON_VERSION: "3.11"

jobs:
  lint:
    name: Lint & Format Check
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: ${{ env.PYTHON_VERSION }}
          cache: pip
      
      - name: Install lint deps
        run: pip install ruff black isort
      
      - name: Check formatting (ruff)
        run: ruff check app/ --select E,W,F
      
      - name: Check imports (isort)
        run: isort --check-only app/

  test:
    name: Test Suite
    runs-on: ubuntu-latest
    needs: lint
    
    steps:
      - uses: actions/checkout@v4
      
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: ${{ env.PYTHON_VERSION }}
          cache: pip
      
      - name: Install dependencies
        run: pip install -r requirements.txt
      
      - name: Run unit tests
        env:
          # Minimal Firebase mock — unit tests should not need real credentials
          FIREBASE_SERVICE_ACCOUNT_JSON: ${{ secrets.FIREBASE_SERVICE_ACCOUNT_JSON }}
          AWS_ACCESS_KEY_ID: ${{ secrets.AWS_ACCESS_KEY_ID }}
          AWS_SECRET_ACCESS_KEY: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          AWS_REGION: us-east-1
          S3_BUCKET_NAME: 360-dagma-photos
          API_ENV: testing
        run: |
          pytest -m "unit" --cov=app --cov-report=xml --cov-report=term-missing -v
      
      - name: Run integration tests (non-Firebase)
        env:
          FIREBASE_SERVICE_ACCOUNT_JSON: ${{ secrets.FIREBASE_SERVICE_ACCOUNT_JSON }}
          AWS_ACCESS_KEY_ID: ${{ secrets.AWS_ACCESS_KEY_ID }}
          AWS_SECRET_ACCESS_KEY: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          AWS_REGION: us-east-1
          S3_BUCKET_NAME: 360-dagma-photos
          API_ENV: testing
        run: |
          pytest -m "not firebase and not s3 and not slow" --cov=app --cov-append --cov-report=xml -v
      
      - name: Upload coverage to Codecov
        uses: codecov/codecov-action@v4
        with:
          token: ${{ secrets.CODECOV_TOKEN }}
          file: ./coverage.xml
          flags: unittests
          fail_ci_if_error: false  # Don't block deploy on coverage upload failure

  deploy:
    name: Deploy to Railway
    runs-on: ubuntu-latest
    needs: test
    if: github.ref == 'refs/heads/master' && github.event_name == 'push'
    
    steps:
      - uses: actions/checkout@v4
      
      - name: Install Railway CLI
        run: npm install -g @railway/cli
      
      - name: Deploy to Railway
        env:
          RAILWAY_TOKEN: ${{ secrets.RAILWAY_TOKEN }}
        run: railway up --detach
```

---

## Required GitHub Secrets

Set these in GitHub → Settings → Secrets and variables → Actions:

| Secret | How to get |
|--------|-----------|
| `FIREBASE_SERVICE_ACCOUNT_JSON` | Firebase Console → Project Settings → Service Accounts → Generate new private key |
| `AWS_ACCESS_KEY_ID` | AWS Console → IAM → Your user → Security credentials |
| `AWS_SECRET_ACCESS_KEY` | Same as above |
| `RAILWAY_TOKEN` | Railway Dashboard → Account Settings → Tokens → New token |
| `CODECOV_TOKEN` | codecov.io → Repository settings (optional) |

---

## Optional: Separate Integration Test Job (uses real Firebase)

```yaml
  test-integration-firebase:
    name: Firebase Integration Tests
    runs-on: ubuntu-latest
    needs: test
    if: github.ref == 'refs/heads/master'  # Only on merge to master
    
    steps:
      - uses: actions/checkout@v4
      
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip
      
      - name: Install dependencies
        run: pip install -r requirements.txt
      
      - name: Run Firebase tests against real Firestore
        env:
          FIREBASE_SERVICE_ACCOUNT_JSON: ${{ secrets.FIREBASE_SERVICE_ACCOUNT_JSON_TEST }}
          API_ENV: testing
        run: pytest -m "firebase" -v --timeout=30
```

> Note: Use a separate `dagma-test` Firebase project for integration tests, not production.

---

## Branch Protection Rules

Set in GitHub → Settings → Branches → Add rule for `master`:

- [x] Require a pull request before merging
- [x] Require status checks to pass before merging
  - Required checks: `lint`, `test`
- [x] Require branches to be up to date before merging
- [x] Do not allow bypassing the above settings

---

## Railway Auto-Deploy (Alternative to GitHub Actions deploy job)

Railway can auto-deploy without the GitHub Actions deploy step:

1. Railway Dashboard → Your Service → Settings → Source
2. Connect GitHub repository
3. Enable "Auto Deploy" on push to `master`
4. Set build/start commands:
   - Build: (auto-detected from requirements.txt)
   - Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`

If using Railway auto-deploy, remove the `deploy` job from CI and keep only `lint` + `test`.

---

## Local Pre-commit Hooks (optional but recommended)

```bash
# Install pre-commit
pip install pre-commit

# Create .pre-commit-config.yaml
cat > .pre-commit-config.yaml << 'EOF'
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.3.0
    hooks:
      - id: ruff
        args: [--fix]
  - repo: https://github.com/psf/black
    rev: 24.2.0
    hooks:
      - id: black
        language_version: python3.11
  - repo: local
    hooks:
      - id: pytest-unit
        name: Run unit tests
        entry: pytest -m unit -x -q
        language: system
        pass_filenames: false
        always_run: true
EOF

# Activate
pre-commit install
```

---

## Monitoring CI Health

Once CI is running, check:
- GitHub Actions tab → see pass/fail history
- Codecov.io → track coverage trends over time
- Railway Dashboard → deployment logs and health checks

**Recommended next step:** After setting up CI, add the Railway health check URL (`/health`) as a Railway health check endpoint so Railway automatically rolls back failed deployments.
