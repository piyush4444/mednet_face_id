# Iris — Pilot Readiness Brief

**Product:** Iris (face recognition and patient tracking) by Synora AI Labs
**Purpose:** A summary of what the system does today, what is ready
for the pilot, and what remains to be validated on the ground with
the right hardware. Written so anyone on the team — technical or not
— can follow.

---

## 1. What Iris does today

The system is built and operational. The capabilities listed below
are working in the current deployment.

| Capability                      | Status                                                                                                             |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| Face registration               | Operators capture up to 10 face samples per person (close-up + distance) during enrolment.                         |
| Real-time face recognition      | Recognition runs continuously on every connected camera; identified people are labelled live on the dashboard.     |
| User management                 | Add, edit, update face samples, and remove registered people from a single Manage Users page.                      |
| Patient tracking across cameras | The system records when each registered person enters and leaves the camera's field of view.                       |
| Session & visit history         | Every camera-tracked session and OPD visit is logged per person, browsable by date or by user on the History page. |
| OPD front-desk workflow         | Scan a patient, pre-fill an OPD form, generate a per-department daily token, print a thermal slip with a QR code.  |
| Multi-operator dashboards       | Two or more terminals can watch the same camera simultaneously without conflict (multi-viewer fan-out).            |
| Search                          | Operators search by name or MRN; the matched patient's camera and floor are surfaced.                              |

The architecture is standards-based: any IP camera that speaks
**RTSP** (the universal protocol for network cameras) integrates
without code changes. No vendor lock-in.

---

## 2. What has been validated, and what is pending

The system is being prepared for the pilot. The table below is an
honest separation of what we have already exercised end-to-end versus
what needs the right hardware and a real installation site to confirm.

### Already validated in development

- End-to-end face enrolment, recognition, and OPD token flow.
- Multi-operator dashboards on the same camera.
- Crash recovery: the backend automatically restarts on failure.
- **Multi-camera load** — exercised with 2–3 USB cameras and 3–4
  IP cameras running simultaneously on the development host.
- **People-per-frame** — recognised 8–12 people in a single frame.
  The per-frame ceiling can be raised further in configuration; the
  practical limit hit during testing is the distance constraint
  (more people means more of them sit at the back of the frame and
  fall below the current identification range), not a software cap.
- **Lighting variation** — works well in well-lit offices, outdoor
  light, and average common-area / gallery lighting. Performance is
  **decent** under semi-low light; the chosen pilot camera's
  on-board IR addresses true low-light scenarios.

### Pending real-ground validation during the pilot

The items below have not been fully exercised in development because
we do not yet have the right cameras or installation environment.
Each is well-understood and addressed by the pilot setup recommended
in §3.

| Item                              | Today's situation                                                                                      | Resolved by                                                                       |
| --------------------------------- | ------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------- |
| **Identification range**    | Limited to ~3-4 m with the current USB webcam (very wide 95° lens, low resolution by CCTV standards). | Installing a 4 MP CCTV bullet camera (§4). Expected reliable range: 6–7 m.      |
| **Backlit / window scenes** | Not yet exercised.                                                                                     | Camera placement guidance in §5 plus the 120 dB WDR sensor in the chosen unit.   |
| **True low-light / night**  | Decent in semi-low light during development; full-dark night not yet exercised.                        | The chosen camera has 80 m IR range; pilot night test will confirm.               |
| **Continuous 24-hour run**  | Tested in development sessions, not over multiple consecutive days.                                    | First week of the pilot establishes the baseline.                                 |
| **Authentication / login**  | The dashboard is currently open to anyone who reaches it on the office network.                        | A login layer is on the roadmap before the pilot is opened to wider hospital use. |

None of the above are limitations of the software — they are items
that simply require the pilot environment to exercise. The roadmap
to address each is concrete, not speculative.

---

## 3. The pilot bring-up plan in three sentences

1. Install **one** CCTV bullet camera (model in §4) at a corridor or
   entrance with good mount geometry (§5).
2. Connect it to the existing Iris server through the office
   network; the system picks it up via the standard RTSP protocol
   the camera already speaks.
3. Run the OPD workflow against real patients for one to two weeks;
   the data collected confirms identification range, lighting
   resilience, and overall operational fit.

If the first camera passes, the pilot expands to 2–3 cameras with
the same model.

---

## 4. Recommended camera for the pilot

A single model is recommended for the pilot. Standardising on one
unit keeps installation, configuration, and troubleshooting simple
during the validation phase.

### Hikvision DS-2CD2T43G2-4I (6 mm lens variant)

| Specification       | Value                                                           |
| ------------------- | --------------------------------------------------------------- |
| Sensor / resolution | 4 MP (2688 × 1520) — exceeds our 2K target                    |
| Lens                | Fixed 6 mm, approximately 52° horizontal field of view         |
| Codec               | H.264 main profile (also supports H.265)                        |
| Wide Dynamic Range  | True 120 dB WDR — handles backlit doorways and windows         |
| IR illumination     | Up to 80 m (the "T" suffix indicates the long-range IR variant) |
| Weatherproof        | IP67 (outdoor-rated; safe for indoor use as well)               |
| Power               | PoE (single Ethernet cable carries power and data)              |
| Protocol            | RTSP — the network-camera standard Iris already speaks         |
| ONVIF               | Profile S (universally compatible)                              |

### Why this model and lens

- **4 MP at 60° HFOV** is the configuration that gives the best
  identification distance for corridors and entrances. Expected
  reliable identification range: **6–7 metres**.
- **120 dB WDR** is essential for hospital entrances where bright
  windows or glass doors sit behind the subject.
- **80 m IR range** ensures the camera works in dimmed corridors at
  night without changing the deployment.
- **PoE** means one cable per camera (power + network), simplifying
  installation.
- **RTSP** is what Iris connects to natively — no custom integration
  required.

The 6 mm lens variant is specifically chosen for **corridor and
entrance use cases** where subjects walk toward the camera. Other
lens lengths in the same family exist; this pilot brief recommends
the 6 mm only.

---

## 5. Installation guidance

Camera placement determines half of the recognition quality. The
following guidance applies to every camera installed during the
pilot.

### Mount geometry

| Parameter    | Recommendation                                                         |
| ------------ | ---------------------------------------------------------------------- |
| Mount height | 1.6–1.8 m above floor (face level)                                    |
| Tilt         | ≤ 15° downward                                                       |
| Orientation  | Subjects should walk**toward** the camera, not across it         |
| Mounting     | Solid wall or beam — avoid vibrating ceiling tiles                    |
| Avoid        | Mounting where bright windows or doors sit directly behind the subject |

### Lighting

| Situation               | Recommendation                                                                              |
| ----------------------- | ------------------------------------------------------------------------------------------- |
| Daytime                 | The chosen camera's WDR handles mixed light well; no extra action needed.                   |
| Night-time              | The on-board 80 m IR is sufficient for corridors.                                           |
| Backlight (window/door) | Re-orient camera so the light source is**behind** the camera, not behind the subject. |

### Network

- **Wired Ethernet** with PoE+ — one cable per camera.
- **Static IP** assigned to the camera by the network admin (or a
  DHCP reservation). The same IP must remain stable so the Iris
  server can reach it consistently.
- **Same network** as the Iris server, or reachable via the office
  VPN — the camera must have a stable network path to the server.

---

## 6. How the camera connects to Iris

Iris connects to network cameras using **RTSP**, the standard
streaming protocol that every IP camera supports.

### One-time camera configuration (camera's own web UI)

1. Assign the camera a static IP on the office network.
2. Set video encoding to **H.264 main profile** at **4–6 Mbps**
   constant bitrate.
3. Disable on-camera AI features (face detection, motion events) —
   Iris does its own recognition.
4. Note down the camera's RTSP URL. The format is usually
   `rtsp://<username>:<password>@<camera-ip>:554/Streaming/Channels/101`
   (the exact path is given in the camera's documentation).

### Adding the camera in Iris

1. Open the Iris dashboard.
2. Go to **Settings → Cameras → Add IP Camera**.
3. Enter:
   - **Name:** human-readable label, e.g. "Lobby Entrance".
   - **Stream URL:** the RTSP URL from the camera.
   - **Floor:** the floor identifier where the camera is mounted.
4. Click **Test connection** — Iris probes the URL and confirms the
   stream is reachable.
5. Save. The camera tile appears on the dashboard within seconds and
   live video starts flowing.

No backend restart is required. Adding, editing, or removing a
camera takes effect immediately.

---

## 7. What the pilot will confirm

By the end of the pilot the team will have measured the following
on real hospital traffic with the recommended hardware:

- **Identification range** — expected 6–7 m on a corridor with the
  recommended camera; pilot confirms on real ground.
- **Backlight resilience** — confirmed against real hospital entrance
  scenarios (windows / glass doors behind subjects).
- **Night operation** — confirmed under IR illumination.
- **Continuous uptime** — confirmed over a one-to-two week run.
- **Operator workflow fit** — the OPD front-desk team uses the
  system daily and provides feedback.

---

## 8. Roadmap items already in development

The following items are actively being worked on and will be in
place by, or shortly after, the pilot.

| Item                                                                       | Status                                                            |
| -------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| Unified user model (patients + doctors + employees + visitors + relatives) | In active development; supports the wider hospital workflow.      |
| Login and role-based access control                                        | On the roadmap before the system is opened to wider hospital use. |
| Automated backups + alerting                                               | Scripts ready, scheduled for activation at pilot bring-up.        |
| Multi-scale recognition for distance                                       | On the technical roadmap; further extends identification range.   |

---

## 9. Summary

Iris is **functionally complete for the pilot scope**.
The single remaining external dependency is the installation of a CCTV-grade
camera in a real environment — without it, identification range and real-world
lighting resilience cannot be measured.

The **Hikvision DS-2CD2T43G2-4I (6 mm)** is the recommended camera for the pilot.
Adding it to Iris is a configuration step in the camera admin UI;
no code changes are required.

The pilot itself is the right vehicle to validate the items that have
not yet been exercised in development. The roadmap is concrete
and the architecture is ready for it.
