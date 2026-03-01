# Frontend (React + TypeScript + Vite)

This folder is a standalone Vite/React workspace.

## Current Status

- The main Busta IM product UI currently runs from Django templates in `backend/templates/`.
- This React app is not wired into Django routes yet.
- Use this folder as an isolated frontend sandbox/prototype area.

## Prerequisites

- Node.js 20+
- npm 10+

## Install

```bash
cd frontend
npm install
```

## Run Dev Server

```bash
npm run dev
```

Default URL: `http://127.0.0.1:5173/`

## Build

```bash
npm run build
```

Output is generated in `frontend/dist/`.

## Lint

```bash
npm run lint
```

## Preview Production Build

```bash
npm run preview
```

## Notes

- Keep API base URLs and auth integration explicit when wiring this into Django/backend APIs.
- `node_modules/` and build artifacts should not be committed.
