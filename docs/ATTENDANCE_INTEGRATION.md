# Attendance Integration

## Decision Flow

Only stable recognitions of active, registered `EMPLOYEE` or `DOCTOR` users
are eligible. The database policy selects attendance cameras and defines
same-day IN and OUT windows. The first eligible sighting in the IN window
creates one daily IN decision. The first eligible sighting in the OUT window
creates one OUT decision only after an IN and the configured minimum work
duration. Database uniqueness on user, date, and direction prevents duplicate
punches during concurrent camera events.

Use `/attendance` to configure the policy. It is disabled and in shadow mode
by default. Shadow mode records decisions without creating outbound calls;
review its observations before enabling live delivery.

## Delivery Reliability

Punch creation writes the local visit log, daily decision, and
`punch_export_sync` outbox in one transaction. The camera process never calls
Mednet directly. A background worker sends one JSON object to
`CLIENT_PUNCH_API_URL`, using `bioStatus: "0"` for IN and `"1"` for OUT.
OUT rows depend on a successfully delivered IN row.

Failures use exponential backoff and remain queryable. A circuit breaker
pauses calls after repeated failures, while the local recognition pipeline
continues normally. `biometricIDX` is stable across retries for downstream
idempotency. Every HTTP attempt is persisted in `punch_delivery_attempt`,
including start/completion time, status, latency, category, and response.

## Operations and Security

The frontend module requires `attendance.read`; policy changes and retry
actions require `attendance.manage` and `attendance.retry`. Policy updates and
manual retries also enter the general audit log. API credentials stay in
`.env`; do not place them in the database or frontend.

For the current integration, the canonical local `users.id` is sent as both
`biometricUID` and `biometricUserID`. Configure each selected camera's Mednet
serial number before enabling the policy. Set the endpoint, restart the
backend so its export worker starts, and use shadow mode for initial rollout.
