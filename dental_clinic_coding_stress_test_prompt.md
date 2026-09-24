You are being evaluated purely as a high-volume software implementation model.

Your job is NOT to edit files, use tools, run commands, browse the web, interact with a repository, use a terminal, or ask me questions.

You have only your reasoning capability and your text output.

Your performance will be evaluated on:

1. amount of useful production-quality code produced
2. correctness
3. consistency across files
4. ability to maintain types and interfaces across a large codebase
5. ability to implement business rules correctly
6. ability to avoid placeholders and fake implementations
7. ability to continue coding for a long context without degrading
8. ability to keep frontend, backend and database contracts synchronized

Do not waste tokens explaining basic programming concepts.

Do not give me a tutorial.

IMPLEMENT IT.

---

# PROJECT

Build a modern dental clinic management platform.

This is not merely an appointment calendar or basic clinic-management application.

The system is intended to become the central operating platform of a dental clinic, covering clinical records, patient management, scheduling, treatment, periodontal charting, odontograms, inventory, personnel, communication, reporting and eventually an AI optimization layer.

For this benchmark, however, DO NOT implement actual LLM calls or AI APIs.

Where AI functionality would eventually exist, create clean interfaces/services and deterministic placeholder business logic that could later be connected to an AI provider.

The normal application itself must be fully functional.

---

# FIXED TECHNOLOGY STACK

Do not change the stack.

Backend:

- Python 3.12
- FastAPI
- SQLAlchemy 2
- Pydantic v2
- PostgreSQL
- Alembic-style database models/migrations structure
- pytest
- async SQLAlchemy
- REST API

Frontend:

- React
- TypeScript
- Vite
- React Router
- TanStack Query
- Zustand where global client state is required
- React Hook Form
- Zod
- CSS modules or clean plain CSS

Authentication:

- JWT access tokens
- refresh tokens
- password hashing
- role-based access control

Roles:

- clinic_manager
- doctor
- assistant
- reception
- administrator

Use UUIDs for primary identifiers.

All timestamps must be timezone-aware.

Use strict typing throughout the project.

---

# DOMAIN

The system supports one or more dental clinics.

Users belong to clinics.

Patients belong to a clinic.

Each clinic contains:

- doctors
- assistants
- reception personnel
- managers
- treatment rooms / cabinets
- patients
- appointments
- clinical examinations
- odontograms
- periodontal examinations
- treatment plans
- performed procedures
- documents
- inventory
- internal communication
- reports

All clinic-specific data must be tenant-isolated.

A user from Clinic A must never access data belonging to Clinic B.

---

# 1. PATIENT 360

Implement a complete patient record.

Patient fields should include at least:

- id
- clinic_id
- first_name
- last_name
- date_of_birth
- sex
- phone
- email
- address
- emergency_contact
- occupation
- notes
- created_at
- updated_at

Medical information:

- allergies
- medications
- chronic diseases
- pregnancy status
- smoking status
- previous surgeries
- relevant medical conditions
- medical alerts

Patient page must expose a chronological timeline containing events such as:

- appointment
- consultation
- diagnosis
- odontogram modification
- periodontal examination
- treatment performed
- treatment plan created
- payment
- uploaded document
- clinical note

Create backend APIs and frontend screens for:

- patient list
- patient search
- create patient
- edit patient
- patient overview
- patient timeline

---

# 2. APPOINTMENTS

Implement scheduling.

Appointment fields:

- patient
- doctor
- assistant if assigned
- treatment room
- start time
- expected duration
- status
- appointment type
- notes

Statuses:

- scheduled
- confirmed
- arrived
- in_progress
- completed
- cancelled
- no_show

Prevent:

- doctor scheduling conflicts
- room scheduling conflicts
- assistant conflicts when an assistant is explicitly assigned

Provide daily and weekly appointment views.

An appointment may contain multiple procedures.

---

# 3. PROCEDURE TIME TRACKING

The clinic wants to measure actual procedure duration without excessive interaction.

Support two different concepts:

Procedure completion:

Each individual procedure performed during an appointment can be marked completed.

Store:

- procedure_started_at
- procedure_completed_at
- actual_duration

Appointment completion:

When the patient actually leaves, the appointment itself can be completed separately.

This avoids counting post-procedure conversation or administrative time as clinical procedure time.

Implement the domain models and APIs accordingly.

---

# 4. ODONTOGRAM

Implement an adult dental odontogram using FDI tooth numbering.

Permanent teeth:

11-18
21-28
31-38
41-48

Each tooth must have independent clinical state.

Possible findings include:

- healthy
- caries
- filling
- crown
- implant
- missing
- extraction_indicated
- root_canal
- bridge_abutment
- fracture
- observation

A tooth may have multiple surfaces:

- mesial
- distal
- buccal
- oral
- occlusal/incisal

A clinician must be able to:

- select a tooth
- select one or multiple surfaces
- register a finding
- register treatment already performed
- add a clinical observation without starting a procedure

Example:

The doctor performs a filling on tooth 16.

During the procedure the doctor notices caries on tooth 17.

The doctor must be able to add:

tooth 17
observation/finding: caries

without creating or starting a procedure.

Keep historical changes so previous odontogram states are not destroyed.

Frontend odontogram should support selecting teeth and surfaces.

Do not build 3D rendering.

Use a clean 2D clinical representation.

---

# 5. PERIODONTOLOGY

Implement periodontal charting.

Each tooth has SIX periodontal measurement sites.

Use:

- mesiobuccal
- buccal
- distobuccal
- mesiooral
- oral
- distooral

For EACH of the six sites store:

- probing depth in millimeters
- bleeding on probing: boolean
- plaque: boolean
- suppuration: boolean

The data-entry interface should prioritize speed.

For one tooth, show six sites and allow rapid numeric depth entry.

BOP, plaque and suppuration should be simple present/absent controls.

An examination must preserve historical measurements.

Do not overwrite the previous periodontal examination.

Provide calculations for:

- percentage of sites with BOP
- percentage of sites with plaque
- number of sites with suppuration
- maximum probing depth
- average probing depth
- sites >= 4 mm
- sites >= 6 mm

Allow comparison between two periodontal examinations.

Create clean domain services for future periodontal stage/grade logic without pretending that incomplete clinical data is sufficient for automatic diagnosis.

---

# 6. TREATMENT PLANS

A doctor can create treatment plans containing multiple planned procedures.

Each item must support:

- tooth
- surfaces where applicable
- procedure type
- estimated duration
- price
- priority
- status
- notes

Treatment-plan statuses:

- draft
- proposed
- accepted
- partially_completed
- completed
- rejected

Treatment item statuses:

- planned
- scheduled
- in_progress
- completed
- cancelled

Completed procedures must remain historically traceable to the treatment-plan item from which they originated.

---

# 7. STAFF

Implement personnel management.

Only properly authorized management users should access sensitive staff management information.

Staff profile:

- identity
- role
- specialization
- contact information
- employment status
- working schedule
- associated clinic
- uploaded document metadata
- activity metrics

Provide:

- staff list
- individual staff page
- availability/schedule
- high-level activity statistics

Do not implement payroll.

---

# 8. TREATMENT ROOMS

Treatment rooms are physical resources.

A room itself is not permanently tied to one doctor.

Example:

Today Doctor Ion and Assistant Maria work in Treatment Room 3.

Tomorrow different personnel may use Treatment Room 3.

Implement dynamic room assignment through appointments/schedules rather than permanent ownership.

---

# 9. INTERNAL COMMUNICATION

Implement internal clinic messaging.

Support:

- direct messages
- team/channel messages
- treatment-room contextual channel

A treatment-room channel represents whoever is currently working in that room.

For example:

Reception can send a message to "Treatment Room 3".

The system resolves which staff members are currently assigned to that room.

Persist message history.

Implement unread state.

Do not implement WebSockets unless necessary for output volume; REST polling is acceptable for this benchmark.

---

# 10. INVENTORY

Implement clinic inventory.

Inventory items should include:

- name
- SKU
- category
- unit
- current quantity
- minimum threshold
- target quantity
- supplier metadata
- expiration tracking where relevant
- lot/batch where relevant
- cost
- location

Inventory movements:

- stock_in
- usage
- adjustment
- expired
- damaged
- transfer

When supplies arrive, personnel register stock.

When an assistant takes supplies, usage can be recorded manually without requiring scanners.

Maintain an immutable movement history.

Current stock should be derived consistently from movements or kept synchronized transactionally.

Generate low-stock alerts when quantity falls below the configured threshold.

Provide:

- inventory list
- low-stock list
- stock movement history
- receive stock
- register usage
- adjust inventory

---

# 11. REPORTING

Create backend reporting services and frontend pages for useful clinic metrics.

Include:

- appointments by status
- doctor utilization
- treatment-room utilization
- planned vs actual procedure duration
- no-show rate
- completed procedures
- revenue-ready procedure totals, without building full accounting
- periodontal statistics
- inventory consumption
- low-stock frequency
- patient growth
- doctor activity

Reports must support date ranges.

Reports must respect role permissions and tenant isolation.

---

# 12. PATIENT WEB PORTAL

Patients should NOT have access to the internal clinic application.

Provide a conceptually separate patient-facing frontend area.

A clinic can send a patient a clinic-specific access/invitation link.

After authentication, the patient remains associated with that clinic.

Patient capabilities:

- see upcoming appointments
- see appointment history
- see accepted treatment plan
- view clinic-shared documents
- view basic treatment information
- manage basic profile/contact information

Do not expose internal staff notes.

---

# 13. FUTURE AI LAYER

The long-term product will include an AI orchestration layer capable of analyzing clinic operations and helping managers optimize workflows.

DO NOT connect an actual LLM.

Create interfaces suitable for future functionality such as:

- scheduling optimization
- inventory predictions
- procedure-duration estimation
- operational anomaly detection
- reporting insights
- manager-requested workflow automation

Use deterministic service stubs/interfaces only where necessary.

The rest of the software must not depend tightly on a specific AI provider.

---

# SECURITY AND ENGINEERING REQUIREMENTS

Implement:

- tenant isolation
- RBAC
- authentication
- input validation
- transactional database operations
- consistent exception handling
- API error schemas
- pagination
- filtering
- search
- created_at / updated_at fields
- audit events for clinically relevant changes

Never trust clinic_id supplied directly by the frontend when it can be derived from authentication context.

Prevent horizontal privilege escalation.

Clinical history must not silently disappear when records are updated.

Prefer append/history records for clinically important changes.

---

# REQUIRED BACKEND ORGANIZATION

Use a structure similar to:

backend/
    app/
        main.py
        config.py
        database.py

        auth/
        users/
        clinics/
        patients/
        appointments/
        procedures/
        odontogram/
        periodontology/
        treatment_plans/
        staff/
        rooms/
        messaging/
        inventory/
        reports/
        audit/
        ai/

Each domain should have sensible separation such as:

- models
- schemas
- repository/data access where appropriate
- service/business logic
- API router

Do not mechanically create useless abstraction layers.

---

# REQUIRED FRONTEND ORGANIZATION

Use a structure similar to:

frontend/
    src/
        app/
        api/
        components/
        features/
            auth/
            patients/
            appointments/
            odontogram/
            periodontology/
            treatmentPlans/
            staff/
            rooms/
            messaging/
            inventory/
            reports/
        pages/
        hooks/
        stores/
        types/
        utils/

Create reusable components where doing so actually reduces duplication.

---

# OUTPUT RULES

THIS IS IMPORTANT.

You cannot create files.

Therefore simulate a repository in your response.

For every file output:

FILE: backend/app/patients/models.py

```python
...complete code...
```

FILE: backend/app/patients/schemas.py

```python
...complete code...
```

Continue like this.

Do not use pseudo-code.

Do not write:

"implementation omitted"

"same as above"

"for brevity"

"TODO: implement"

"..."

"etc."

Do not replace code with comments describing what the code should do.

If you introduce an interface that intentionally represents a future external AI integration, a stub is acceptable ONLY for the unavailable external AI operation.

Normal application functionality must contain real implementations.

---

# CONSISTENCY RULE

Before emitting each file, mentally verify:

- imported symbols exist
- schemas match models
- frontend types match API schemas
- foreign keys are consistent
- enum values are consistent
- routes correspond to frontend API calls
- identifiers use the same types everywhere
- names do not spontaneously change between modules

When you discover an earlier design mistake, correct it explicitly instead of silently creating a second incompatible implementation.

---

# TESTING REQUIREMENT

Produce meaningful tests.

Prioritize tests for:

- authentication
- tenant isolation
- appointment collision detection
- patient access
- odontogram history
- periodontal six-site storage
- periodontal calculations
- treatment-plan transitions
- inventory movement calculations
- RBAC

---

# BENCHMARK BEHAVIOR

Do NOT spend your first response giving me a giant architecture essay.

You may spend a SMALL amount of internal reasoning deciding implementation order.

Then start producing code.

At the top of your response output only:

IMPLEMENTATION ORDER

followed by a concise numbered list of at most 15 items.

After that immediately begin writing files.

Do not stop after producing a project skeleton.

A skeleton full of empty modules is considered failure.

Prioritize COMPLETE vertical functionality over creating hundreds of empty files.

Continue producing useful code until you approach your response limit.

If you cannot implement the entire application in one response:

1. stop only at a logical file boundary
2. output a final line exactly in this format:

NEXT_FILE: <path>

This allows the benchmark to continue in the next message.

When I subsequently reply:

CONTINUE

resume directly from NEXT_FILE.

Do not repeat previous code.

Do not summarize previous work.

Do not redesign the architecture unless a real consistency problem requires it.

---

# IMPLEMENTATION PRIORITY

Implement in this order:

1. project foundation and shared types
2. authentication and RBAC
3. clinics and tenant isolation
4. patients
5. appointments and room/staff conflict detection
6. procedures and time tracking
7. odontogram
8. periodontology
9. treatment plans
10. inventory
11. staff and rooms
12. internal messaging
13. reports
14. patient portal
15. AI service interfaces
16. tests

Start now.
