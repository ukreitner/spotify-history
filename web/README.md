# Listening Archive web app

The frontend is a Next.js app backed by the FastAPI service in the repository root.

## Local development

Start the API from the repository root:

```bash
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

Then start the frontend:

```bash
cd web
npm install
npm run dev
```

Open <http://localhost:3000>. The browser calls same-origin `/api/*` URLs and Next.js proxies them to `http://127.0.0.1:8000` by default. This avoids CORS and mixed-origin problems.

To point the proxy at a different API process:

```bash
API_PROXY_TARGET=http://127.0.0.1:8001 npm run dev
```

For a separately hosted browser-facing API, set `NEXT_PUBLIC_API_BASE_URL` instead. The FastAPI deployment can accept extra comma-separated browser origins through `CORS_ORIGINS`.

## Checks

```bash
npm run lint
npm run build
```
