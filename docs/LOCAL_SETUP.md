# Local Development Setup

This guide runs Iris directly on a development machine. It does not require
Docker, nginx, systemd, or any deployment scripts.

## 1. Prerequisites

Install:

- Python 3.10 or newer
- Node.js 20 or newer, including npm
- PostgreSQL 14 or newer
- Git is optional; the application does not require a Git repository

An NVIDIA GPU is optional. CPU mode is the simplest local setup. The first
recognition startup may download the InsightFace `buffalo_l` model, so allow
network access or pre-populate the InsightFace model cache.

Run all commands below from the repository root, the directory containing
`run_backend.py` and `run_frontend.py`.

## 2. Create the PostgreSQL database

Start PostgreSQL, then create a development database. The default application
configuration expects this connection:

```text
postgresql://postgres:postgres@localhost:5432/facedb
```

If the local `postgres` role already has password `postgres`:

```bash
createdb -h localhost -U postgres facedb
```

Otherwise create a dedicated local role and database from `psql` using an
existing PostgreSQL administrator account:

```sql
CREATE ROLE facedb WITH LOGIN PASSWORD 'facedb';
CREATE DATABASE facedb OWNER facedb;
```

For the dedicated role, use this URL in `.env`:

```text
postgresql://facedb:facedb@localhost:5432/facedb
```

Do not run SQL schema files manually. Backend startup calls `init_db()`, which
creates the current tables and applies the project's startup migrations.

## 3. Create the backend environment

Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

On Windows PowerShell, activate it with:

```powershell
.venv\Scripts\Activate.ps1
```

For a CPU-only machine:

```bash
python -m pip install -r backend/requirements-cpu.txt
```

Then set these constants in `backend/config.py` for CPU inference:

```python
GPU_ENABLED = False
GPU_DEVICE_ID = -1
```

For a compatible NVIDIA CUDA 12 machine:

```bash
python -m pip install -r backend/requirements.txt
python -m pip uninstall -y onnxruntime
python -m pip install --force-reinstall --no-deps "onnxruntime-gpu>=1.16.0,<1.23"
```

The second and third commands prevent InsightFace's CPU ONNX dependency from
overwriting the GPU runtime module.

## 4. Configure local environment variables

Copy the backend template:

```bash
cp .env.example .env
```

At minimum, confirm the database URL. A useful local configuration is:

```env
DATABASE_URL="postgresql://facedb:facedb@localhost:5432/facedb"
ALLOWED_ORIGINS="http://localhost:5173,http://127.0.0.1:5173"
AUTH_ENABLED=false
START_CAMERA_SYSTEM=0
BOOTSTRAP_FACILITY_NAME="Mednet"
BOOTSTRAP_SUPERADMIN_NAME="Mednet Superadmin"
BOOTSTRAP_SUPERADMIN_USERNAME="superadmin"
BOOTSTRAP_SUPERADMIN_PASSWORD="replace-with-at-least-8-characters"
```

Leave both `CLIENT_*_API_URL` variables empty during local development unless
you intentionally want to send data to an external HIS. Empty URLs keep the
export worker dormant.

### Optional local authentication

The default `AUTH_ENABLED=false` bypasses login enforcement. To test login and
RBAC locally, generate a secret and add these values to `.env`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

```env
AUTH_ENABLED=true
SESSION_SECRET="paste-the-generated-value-here"
SESSION_COOKIE_SECURE=false
```

Create the singleton facility and first superadmin after PostgreSQL is reachable:

```bash
python -m backend.scripts.seed_initial
```

The command is idempotent and never prints or rotates the configured password.
Remove `BOOTSTRAP_SUPERADMIN_PASSWORD` from the runtime environment after seeding.

## 5. Start the backend

For normal local API and UI development, start without camera processes:

```bash
python run_backend.py --no-cameras
```

Verify:

- Health: <http://127.0.0.1:8000/api/v1/health>
- Swagger UI: <http://127.0.0.1:8000/docs>

To exercise the camera pipeline without physical cameras:

```bash
python run_backend.py --test-mode
```

To run configured RTSP cameras:

```bash
python run_backend.py --no-reload
```

Do not combine camera workers with auto-reload for sustained testing. Reloading
can restart the parent process while multiprocessing children are still exiting.
Keep the Uvicorn worker count at one when cameras are enabled.

## 6. Start the frontend

Open a second terminal in the repository root. Activate the Python environment
if using the convenience launcher, then run:

```bash
python run_frontend.py --api-url http://127.0.0.1:8000/api/v1
```

The launcher installs npm dependencies the first time and starts Vite at:

<http://localhost:5173>

The equivalent manual commands are:

```bash
cd frontend
cp .env.example .env
npm install
npm run dev
```

## 7. First local workflow

After both processes are running:

1. Open the frontend.
2. If authentication is enabled, sign in with the administrator account.
3. Create locations as needed; the Mednet facility is resolved automatically.
4. Add a camera under the camera settings, or continue without cameras.
5. Register a test identity using several face angles.
6. For an employee/doctor, enable application access under Accounts & RBAC.

Do not use real patient data or production API credentials in a local database.
Profile photos and face indexes under `database/` are biometric data.

## 8. Checks and troubleshooting

Check PostgreSQL connectivity:

```bash
psql "$DATABASE_URL" -c "SELECT 1;"
```

If `DATABASE_URL` is only stored in `.env`, pass the URL explicitly or load the
file before running `psql`.

Common problems:

| Problem | Resolution |
| --- | --- |
| `connection refused` on port 5432 | Start PostgreSQL and verify `DATABASE_URL`. |
| Password authentication failed | Correct the PostgreSQL role/password in `.env`. |
| CUDA provider warnings on a CPU machine | Set `GPU_ENABLED=False` and `GPU_DEVICE_ID=-1` in `backend/config.py`. |
| InsightFace model download fails | Restore network access or place `buffalo_l` in the local InsightFace model cache. |
| Frontend reports offline | Start the backend and confirm `VITE_API_URL` ends with `/api/v1`. |
| Browser CORS error | Add the exact Vite origin to `ALLOWED_ORIGINS` and restart the backend. |
| Login fails before an admin exists | Run the `create_account` command in section 4. |
| Camera child processes remain after stopping | Avoid reload with cameras; stop leftover development Python processes before restarting. |

## 9. Stop and restart

Stop the frontend and backend with `Ctrl+C` in their terminals. PostgreSQL may
remain running as a local system service. On the next session, activate
`.venv` again and repeat the backend and frontend commands from sections 5 and
6; dependency installation and database creation do not need to be repeated.
