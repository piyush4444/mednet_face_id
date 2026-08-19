# Repository Guidelines

## Project Structure & Module Organization

Iris combines a Python 3.10+ FastAPI service with a React 19/Vite client. Backend HTTP routes, schemas, database models, and business services live in `backend/app/`; multi-camera process management is in `backend/camera/`; shared detection, embedding, and FAISS matching code is in `backend/engine/`. Frontend code is under `frontend/src/`, organized into `pages/`, `components/`, `hooks/`, `store/`, and `api/`. Static assets belong in `frontend/public/` or `frontend/src/assets/`. Runtime camera configuration is stored in PostgreSQL `camera_master`; architecture and API details live in `docs/`.

## Build, Test, and Development Commands

Run commands from the repository root unless noted:

- `pip install -r backend/requirements-cpu.txt` installs CPU backend dependencies; use `requirements.txt` for CUDA hosts.
- `python run_backend.py --no-cameras` starts the API safely for local development.
- `python run_backend.py --no-reload` starts the API with configured camera workers.
- `python run_frontend.py --api-url http://127.0.0.1:8000/api/v1` starts Vite.
- `cd frontend && npm run lint` checks JavaScript and JSX with ESLint.
- `cd frontend && npm run build` creates the production bundle in `frontend/dist/`.

Do not combine Uvicorn reload or multiple web workers with the camera subsystem.

## Coding Style & Naming Conventions

Follow existing files: Python uses four spaces, type hints where useful, `snake_case` functions/modules, and `PascalCase` classes. Keep route handlers thin and place business logic in `backend/app/services/`. Access face models and FAISS only through the singleton helpers in `pipeline_service.py`. React uses functional components, `PascalCase.jsx` component files, `useCamelCase` hooks, and camelCase variables. Match the existing two-space JSX indentation and double-quoted imports. Run ESLint before submitting frontend changes.

## Testing Guidelines

No automated backend or frontend test suite is currently configured. Validate backend changes with a focused script, Swagger (`/docs`), or the relevant endpoint; use synthetic-camera mode when camera behavior matters. For frontend work, run both `npm run lint` and `npm run build`, then manually exercise affected routes. Add tests alongside new infrastructure using descriptive names such as `test_token_service.py` or `ComponentName.test.jsx`.

## Commit & Pull Request Guidelines

Use the Conventional Commit style documented in the changelog, for example `feat(kiosk): add device setup` or `fix(camera): reload role mapping`. Keep commits scoped and explain operational or schema consequences. Pull requests should include a concise summary, verification steps, linked issue, configuration or migration notes, and screenshots for visible UI changes. Never commit secrets, `.env` files, biometric media, model artifacts, or generated `dist/` output.

When asked to commit changes, do not add a `Co-Authored-By: OpenAI`, `ChatGPT`, or `Codex` trailer, or any other AI attribution, to the commit message. Commits must appear authored solely by the user.
