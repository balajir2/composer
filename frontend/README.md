# Composer frontend

The Composer web application is a Next.js 15 App Router project. It provides the workflow Designer, end-user Runs experience, and administrator console.

The repository-level [README](../README.md) and [documentation index](../docs/README.md) are the primary product documentation. This file covers frontend development only.

## Prerequisites

- Node.js 20 or newer
- npm (the committed `package-lock.json` is authoritative)
- A running Composer backend, normally at `http://localhost:8000`

## Local development

```bash
npm ci
npm run dev
```

Open <http://localhost:3000>. Configure backend and authentication values as described in [Getting Started](../docs/getting-started.md) and [Azure SSO](../docs/operations/azure-sso.md).

## Quality gates

```bash
npm run format:check
npm run type-check
npm test
npm run build
```

The GitHub Actions CI workflow runs these gates and a Playwright smoke suite. Full browser tests live in `e2e/`.

## API client

With the backend running locally, regenerate the OpenAPI types with:

```bash
npm run generate-client
```

Review the generated diff in `lib/api/generated/schema.ts`; do not edit that file manually.

## Production

The current production path builds `frontend/Dockerfile` and deploys the server application to GCP Cloud Run. Vercel remains an alternate deployment path; see [Production Deployment](../docs/operations/production-deployment.md).
