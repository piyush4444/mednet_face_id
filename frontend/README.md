# Iris Frontend

React 19 + Vite single-page app for Iris. Operators use it to enrol patients/doctors/employees/visitors/relatives, run the front-desk OPD queue, monitor live multi-camera MJPEG streams, inspect user profiles, review presence history, and administer cameras + departments + rooms + doctors. Talks to the FastAPI backend over REST and subscribes to a WebSocket for real-time detection and visit events.

---

## Tech stack

- **Framework:** React 19 + React Router 7
- **Build tool:** Vite 8
- **UI:** Material UI 9 (`@mui/material`, `@mui/icons-material`, `@mui/x-date-pickers`) + Tailwind CSS 4 (via the `@tailwindcss/vite` plugin — no separate PostCSS config)
- **Theming:** light/dark mode, toggled from the header. Preference persists in `localStorage` (`iris-theme`), defaults to the OS `prefers-color-scheme`, and is applied pre-paint by an inline script in `index.html`. State lives in [src/store/themeStore.js](src/store/themeStore.js); the dark palette is CSS-variable overrides under `.dark` in [src/index.css](src/index.css) (Tailwind semantic tokens re-skin automatically) plus a matching MUI palette from [src/theme/muiTheme.js](src/theme/muiTheme.js). Literal Tailwind colors need explicit `dark:` variants.
- **Notifications:** react-toastify
- **Date utilities:** date-fns
- **QR codes:** `qrcode` — used to render the OPD slip on the front-desk token page
- **Linting:** ESLint 9 (flat config) with `react-hooks` + `react-refresh` plugins

---

## Setup

The complete development-machine workflow is in
[`../docs/LOCAL_SETUP.md`](../docs/LOCAL_SETUP.md). This page documents
frontend-specific behavior and commands.

### Environment variables — layered

The build picks env files in this precedence (highest first):

```
.env.local        ← per-machine, gitignored
.env.production   ← committed, used by `npm run build`
.env              ← gitignored fallback for `npm run dev`
.env.example      ← committed template
```

Included in the source tree:

- **`.env.production`** — uses the same-origin relative URL `/api/v1` for production builds. A host must route that path to the backend.
- **`.env.example`** — annotated template.

Creation of the local `.env` file is covered once in
[`LOCAL_SETUP.md`](../docs/LOCAL_SETUP.md).

Required variables:

| Variable | Purpose |
| --- | --- |
| `VITE_API_URL` | Backend REST/WS base. Must end with `/api/v1`. The WebSocket URL is derived from this (http→ws, https→wss). |
| `VITE_ENABLE_LOGGING` | `true` enables console.log/info/debug; `warn`/`error` always pass through. Default `false`. |

Defaults live in [src/config.js](src/config.js): `VITE_API_URL` falls back to `http://127.0.0.1:8000/api/v1`.

### Dev server

From the repository root, run
`python run_frontend.py --api-url http://127.0.0.1:8000/api/v1`.

The launcher installs missing npm dependencies and starts the app at
<http://localhost:5173>. The backend must be reachable at the configured
`VITE_API_URL`.

### Other scripts

```bash
npm run build      # Production build → dist/
npm run preview    # Serve the production build locally
npm run lint       # ESLint
```

---

## Project structure

```text
frontend/
├── public/                       # Static assets
├── src/
│   ├── App.jsx                   # Router + lazy-loaded routes + ToastContainer
│   ├── main.jsx                  # React DOM entry
│   ├── config.js                 # API_URL + ENABLE_LOGGING resolution
│   ├── index.css                 # Tailwind v4 entry + global styles
│   ├── layout/
│   │   └── MainLayout.jsx        # Shell: header, nav, SocketProvider, page slot
│   ├── pages/
│   │   ├── Dashboard.jsx         # Live camera grid + alerts (eager-loaded)
│   │   ├── Register.jsx          # Multi-image enrolment with user-type selector
│   │   ├── Patients.jsx          # "Manage Users" — type-filtered table + edit modal + relations
│   │   ├── PatientProfile.jsx    # User profile (any type) with relations + OPD history
│   │   ├── History.jsx           # Date-scoped presence + OPD-visit history
│   │   ├── Cameras.jsx           # Camera admin (now embedded inside Settings)
│   │   ├── FrontDesk.jsx         # OPD scan → queue-aware candidates → token slip
│   │   ├── FrontDeskAdmin.jsx    # CRUD for departments / rooms / doctors (lookup tables)
│   │   └── Settings.jsx          # Tabbed container: Manage Users · Cameras · Front Desk admin
│   ├── components/               # AlertPanel, CameraCard, CameraGrid, CameraSelect,
│   │                             # CustomSelect, Header, HeaderSearch, PatientPanel,
│   │                             # PatientProfileCard, SearchBar
│   ├── hooks/
│   │   ├── useSocket.jsx         # SocketProvider + useSocketMessages — single WS
│   │   └── useWebcams.js         # navigator.mediaDevices enumeration helper
│   ├── store/                    # alertStore, connectionStore, searchStore
│   ├── theme/                    # MUI theme
│   ├── utils/
│   │   └── logger.js             # Logger gated on VITE_ENABLE_LOGGING
│   └── assets/                   # Images, icons
├── .env.example                  # Env template (committed)
├── .env.production               # Production VITE_API_URL (committed, baked at build)
├── eslint.config.js              # Flat ESLint config
├── vite.config.js                # Vite config (React plugin + Tailwind plugin)
└── package.json
```

---

## Routes

| Path | Component | Notes |
| --- | --- | --- |
| `/` | Live View | Eager-loaded landing — live camera grid + alert feed. |
| `/register` | Register | Enrolment for any user type via `POST /register/multi`. Type selector + conditional sub-forms; relatives can pre-link patients. |
| `/frontdesk` | FrontDesk | Scan → queue-aware candidate strip → OPD form (patients) or NonPatientCard (other types) → token slip with QR. |
| `/patients/:id` | PatientProfile | Profile for any user type. Shows demographics per type, relations panel, OPD-visit history. Fetches `/users/{id}` (legacy `/patients/{id}` is a back-compat alias). |
| `/history` | History | Date-scoped attendance + OPD visits with per-type filter strip. |
| `/settings` | Settings | Tabbed container for Manage Users, Cameras admin, and Front Desk admin (departments/rooms/doctors). |
| `/patients` | → `/settings?section=manage` | Legacy path; redirected for back-compat. |
| `/cameras` | → `/settings?section=cameras` | Legacy path; redirected. |
| `/frontdesk/admin` | → `/settings?section=frontdesk` | Legacy path; redirected. |
| `*` | → `/` | Unknown paths redirect to Live View. |

Every route except Live View is code-split via `React.lazy` — chunks download on first navigation.

---

## Unified user model

Since the user-model expansion (see `docs/CHANGELOG.md` Phases 1–8), the frontend speaks to one unified `/users/*` API that handles five types: `PATIENT`, `DOCTOR`, `EMPLOYEE`, `VISITOR`, `RELATIVE`. Implications you'll see in the UI code:

- **Register** has a type selector at the top; sub-forms render conditionally. Relatives can attach to one or more patients via a debounced picker.
- **Patients.jsx** (kept by filename to avoid churning imports) is "Manage Users" — filter tabs per type, type badges per row, type-aware edit modal, inline relations panel.
- **Front Desk** branches `OPDForm` (patients only — those eligible for tokens) vs. `NonPatientCard` (other types — "this person is a doctor / visitor / relative, no token").
- **PatientProfileCard** renders demographics relevant to the user_type only — non-patients don't show empty Age/DOB/Department.

---

## Live data flow

### MJPEG streams

Each `CameraCard` renders an `<img>` pointing at `${API_URL}/stream/{camera_id}`. Bounding boxes and identity labels are drawn server-side by the backend's annotated encoder — the frontend just displays the multipart JPEG stream.

### Real-time events (WebSocket)

[`src/hooks/useSocket.jsx`](src/hooks/useSocket.jsx) opens a single connection in `SocketProvider` (mounted in `MainLayout`) and derives the WS URL from the same `VITE_API_URL`:

```js
// http(s) → ws(s); strip trailing /api/v1; re-add /api/v1/ws/live
ws://host/api/v1/ws/live
```

Any component can subscribe to incoming messages via `useSocketMessages(handler)`. Reconnects on drop with exponential backoff (initial 2 s, max 30 s). Events dispatched today:

- `DETECTION` — per-frame face detections from each camera; drives the dashboard tile labels and the alert panel.
- `frontdesk.visit.created` / `frontdesk.visit.status_changed` — pushed by the front-desk visit endpoints; updates the queue UI in real-time.

### Registration

`Register.jsx` collects up to 10 multi-angle images and submits `multipart/form-data` to `POST /register/multi`. The backend detects + embeds each image, persists the user in PostgreSQL, and pushes the embeddings into FAISS in one call. The last 5 sample slots prompt for distance-enrolled angles — varied gallery dramatically improves off-frontal recognition.

### Front-desk scan

`FrontDesk.jsx` submits a frame to `POST /frontdesk/scan`. The backend ranks identified faces by bbox area (largest = closest to camera = person at the counter) and returns a primary plus `candidates[]` for everyone else in the frame. The UI prefills the OPD form for the primary and shows a one-click "Others in frame" strip to swap targets without re-scanning.

---

## Building for production

```bash
npm run build
```

Outputs to `dist/`. Supply your own static-file server or hosting platform.
The build reads `.env.production` for `VITE_API_URL`.

> **Important:** `VITE_API_URL` is baked into the bundle at build time, not
> read at runtime. Rebuild the frontend whenever the public API URL changes.

---

## See also

- [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) — full system design.
- [`../docs/API_REFERENCE.md`](../docs/API_REFERENCE.md) — every REST endpoint + WebSocket event the frontend talks to.
