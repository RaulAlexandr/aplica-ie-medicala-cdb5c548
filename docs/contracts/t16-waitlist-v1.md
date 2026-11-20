# T16 waitlist and recalls v1

## Scope

T16 v1 records patient availability and recalls, matches released appointment
slots, places a temporary exclusive offer, and confirms one offer into a real
appointment. Offers are made by telephone and recorded in the application.
There are no automated messages.

## Data and states

- `WaitlistEntry` belongs to one clinic and patient. It stores desired service
  or minimum duration, optional preferred doctor, one or more weekday/local
  time windows, a validity range, priority, notes, creator, and status.
- Entry states are `active -> offered -> booked`; active/offered entries may
  become `cancelled` or `expired`. An expired or declined offer returns its
  entry to `active` if its validity range still includes today; otherwise it
  becomes `expired`.
- `WaitlistOffer` binds one entry to one clinic, doctor, room, UTC interval,
  and expiry instant. States are `active`, `confirmed`, `expired`, or
  `cancelled`. Only one active offer can reserve an interval at a time.
- `Recall` belongs to one clinic and patient and stores due date, reason,
  optional service, status, creator, and timestamps. Each contact/scheduled/
  refused action appends a `RecallHistory` row with actor and time.
- New foreign keys use `ON DELETE RESTRICT`. Checks constrain all state fields.

## Matching and reservation

For a candidate slot, the server uses the clinic's configured timezone to
compare the slot's local weekday and time with the entry's availability window
and validity dates. Desired duration must fit within the offered slot, the
preferred doctor must match when specified, and the patient must have no
overlapping appointment. Candidates sort by descending priority, then oldest
entry first.

The database enforces exclusive active reservation for the exact clinic,
doctor, room, start, and duration tuple: PostgreSQL uses a partial unique index
and SQLite uses the equivalent partial unique index. Offer creation also
serializes on the clinic row, expires elapsed offers, checks for an existing
active reservation, and then inserts the offer. The DB constraint remains the
final guard if concurrent requests race.

Confirmation locks the offer, rejects non-active or expired offers with 409,
rechecks the reserved slot and patient, calls the existing appointment
availability/conflict helpers, and creates exactly one scheduled appointment
plus appointment history. Offer and entry transition to `confirmed` and
`booked` in the same transaction. Competing confirmations cannot both create
an appointment; the losing request returns 409.

## Roles and clinic access

Waitlist entries, offers, and confirmation are available to `reception`,
`clinic_manager`, and `administrator`; `assistant` has read-only access. Recall
creation and actions are available to `doctor`, `reception`,
`clinic_manager`, and `administrator`; assistants may read. All queries scope
by the authenticated user's clinic, and foreign-clinic IDs return 404.

## v2

Automated T15 messages, simultaneous offers to multiple patients, and the
patient portal are excluded. v1 has one active offer per exact interval.
