# T12 intake forms v1

## Scope and source

T12 implements clinic managed intake form templates, versioned questions and
sections, staff entry for a patient, clinician review, and a patient record
listing. The source plan describes configurable sections/questions, conditions,
versions, temporary patient links, duplicate identification, and staff
verification. This v1 deliberately includes no public link, patient portal, or
patient deduplication; those are v2 work. Example question text is fictional.
Serena approves the real anamnesis content separately.

## Roles and clinic boundary

All records carry a clinic id. Every lookup is scoped to the authenticated
user's clinic; a record belonging to another clinic returns 404.

The T05 clinical notes router defines `MANAGERS` as `clinic_manager` and
`administrator`; `DOCTORS` as `clinic_manager`, `doctor`, and `administrator`;
and `STAFF_ROLES` as `clinic_manager`, `doctor`, `assistant`, `reception`, and
`administrator`. Its patient-record read route uses `STAFF_ROLES`, while
clinical note content and revisions use doctor/manager access. Accordingly,
template authoring/publication is manager-only; form completion is
`STAFF_ROLES`; validation and clarification are `DOCTORS`; answer content is
readable to `DOCTORS`; reception can read only form list/status metadata, never
answers. This follows the current T05 route-level policy, not an assumption
that every clinic staff role can read clinical content.

## Data model

- `FormTemplate`: clinic-owned stable identity, name, and creation metadata.
- `FormTemplateVersion`: template id, positive version number, `draft` or
  `published` status, immutable JSON definition snapshot, creator, timestamps,
  and publication actor/time. `(template_id, version_number)` is unique.
- `PatientForm`: clinic, patient, template version, `source`, workflow status,
  creator/timestamps, submit metadata, validation actor/time, clarification
  reason, and current response snapshot. `source` is `staff` in v1 and reserves
  `patient` for v2. Status is `draft`, `submitted`, `validated`, or
  `needs_clarification`.
- `PatientFormRevision`: append-only snapshot of prior response and review
  state, revision number, actor/time, and required correction reason. Revisions
  belong to the same clinic and form.

New foreign keys use `ON DELETE RESTRICT`; versions and submissions therefore
cannot be deleted out from under historical responses. State and question-type
checks are enforced in the database as well as in the API.

## Definition format

A definition is a JSON object with ordered `sections`. Each section has a
stable `id`, `title`, and ordered `questions`. Each question has a stable `id`,
`label`, one of `short_text`, `long_text`, `yes_no`, `single_choice`,
`multiple_choice`, `date`, or `number`, a `required` boolean, and optional
choice `options` (required for choice questions). IDs are unique within a
version. Choice values are stable strings; labels are presentation text.

A question may have one simple condition:
`{"question_id":"x","operator":"equals","value":"yes"}`. For a
yes/no question the controlling value is `yes` or `no`; for a single-choice
question it must be an option value. Conditions may refer only to earlier
questions in the same or an earlier section and cannot be chained through a
hidden controller. A conditional question is visible only when the controller
answer equals the configured value. The server computes visibility in order,
rejects answers to hidden questions, validates response shape and option
membership, and requires every required visible question at submission.

## Lifecycle and immutability

Managers create a draft version and edit its definition. Publishing is a
one-way transition from draft to published. A published definition cannot be
edited or republished; changes require a new draft version with the next
version number. A patient form always references one published version, so its
historical answers render using that exact version's question snapshot.

Authorized staff create a draft patient form and save answers. Submission
transitions `draft` to `submitted`. A doctor/manager transitions a submitted
form to `validated` (recording `validated_by` and `validated_at`) or
`needs_clarification` (requiring a reason). Clarification returns to staff for
editing; resubmission returns it to `submitted`. A validated form is immutable
in place. A correction creates an append-only revision with the prior state and
answers, reason, actor, and time, then applies the corrected snapshot as a new
revisioned state; no answer history is silently overwritten. The form remains
linked to its original template version.

The patient record list exposes template/version, source, lifecycle status,
validation status, and timestamps. Reception receives only this metadata.

## Out of scope for v1

Public/temporary completion links, patient portal or patient authentication,
duplicate-patient detection/merging, and real anamnesis copy are excluded.
Serena approves clinical question content before it is populated for use.
