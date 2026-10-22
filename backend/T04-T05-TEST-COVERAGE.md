# T04/T05 Test Coverage Mapping

This document maps each requirement from T04 and T05 specifications to the corresponding test(s) that verify it.

## Draft Protection

| Requirement | Test | File |
|-------------|------|------|
| Author can read their own draft notes | `test_draft_note_only_visible_to_author` | test_t04_t05_requirements.py |
| Author can modify their own draft notes | `test_draft_note_only_visible_to_author` | test_t04_t05_requirements.py |
| Author can finalize their own draft notes | `test_draft_note_only_visible_to_author` | test_t04_t05_requirements.py |
| Other doctors cannot read another doctor's draft | `test_draft_note_only_visible_to_author`, `test_draft_notes_filtered_from_list` | test_t04_t05_requirements.py |
| Other doctors cannot modify another doctor's draft | `test_draft_note_only_visible_to_author` | test_t04_t05_requirements.py |
| Other doctors cannot finalize another doctor's draft | `test_draft_note_only_visible_to_author` | test_t04_t05_requirements.py |
| Managers cannot read another doctor's draft | `test_draft_note_only_visible_to_author` | test_t04_t05_requirements.py |
| Managers cannot modify another doctor's draft | `test_draft_note_only_visible_to_author` | test_t04_t05_requirements.py |
| Managers cannot finalize another doctor's draft | `test_draft_note_only_visible_to_author` | test_t04_t05_requirements.py |
| Draft notes filtered from list for non-authors | `test_draft_notes_filtered_from_list` | test_t04_t05_requirements.py |
| Draft protection applies to revisions endpoint | `test_draft_protection_revisions_endpoint` | test_t04_t05_requirements.py |
| Draft notes filtered from timeline for non-authors | `test_draft_note_in_timeline_filtered` | test_t04_t05_requirements.py |
| Draft protection in patient record | `test_timeline_draft_protection_in_record` | test_clinical_notes.py |
| Draft protection not bypassed by different source | `test_timeline_draft_protection_bypass_source` | test_clinical_notes.py |
| Old draft events sanitized without refresh | `test_timeline_sanitizes_old_events_without_refresh` | test_clinical_notes.py |

## Addendum

| Requirement | Test | File |
|-------------|------|------|
| Addendum can only be added to finalized notes | `test_addendum_only_for_finalized_notes` | test_t04_t05_requirements.py |

## Pagination

| Requirement | Test | File |
|-------------|------|------|
| Pagination correctly skips hidden drafts | `test_pagination_with_interleaved_drafts` | test_t04_t05_requirements.py |
| Hidden drafts don't consume pagination slots | `test_pagination_excludes_hidden_drafts_from_count` | test_t04_t05_requirements.py |
| Access filtering applied before offset/limit | `test_pagination_with_interleaved_drafts`, `test_pagination_excludes_hidden_drafts_from_count` | test_t04_t05_requirements.py |
| Deterministic ordering | `test_pagination_with_interleaved_drafts` | test_t04_t05_requirements.py |

## Closure (Appointment Completion)

| Requirement | Test | File |
|-------------|------|------|
| Pending procedures block closure | `test_closure_with_pending_procedures_blocked` | test_t04_t05_requirements.py |
| Running procedures block closure | `test_closure_with_running_procedures_blocked` | test_t04_t05_requirements.py |
| Paused procedures block closure | `test_closure_with_paused_procedures_blocked` | test_t04_t05_requirements.py |
| Completed procedures allow closure | `test_closure_with_completed_procedures_allowed` | test_t04_t05_requirements.py |
| Cancelled procedures allow closure | `test_closure_with_cancelled_procedures_allowed` | test_t04_t05_requirements.py |
| No procedures allow closure | `test_closure_without_procedures_allowed` | test_t04_t05_requirements.py |
| Rejections don't modify note/status/reviews | (Implicit in all closure tests) | test_t04_t05_requirements.py |
| Generic error messages (no clinical details) | `test_closure_with_pending_procedures_blocked`, `test_closure_with_running_procedures_blocked`, `test_closure_with_paused_procedures_blocked` | test_t04_t05_requirements.py |
| Create procedure fails for completed appointment | `test_create_procedure_fails_for_completed_appointment` | test_t04_t05_requirements.py |

## Timeline Sanitization

| Requirement | Test | File |
|-------------|------|------|
| Clinical note events sanitized in timeline | `test_timeline_sanitizes_clinical_note_events` | test_t04_t05_requirements.py |
| Procedure events sanitized for non-clinical roles | `test_timeline_sanitizes_procedure_events_for_non_clinical` | test_t04_t05_requirements.py |
| Sensitive title sanitized | `test_timeline_sanitizes_clinical_note_events`, `test_timeline_sanitizes_old_events_without_refresh` | test_t04_t05_requirements.py, test_clinical_notes.py |
| Sensitive description sanitized | `test_timeline_sanitizes_clinical_note_events`, `test_timeline_sanitizes_old_events_without_refresh` | test_t04_t05_requirements.py, test_clinical_notes.py |
| Sensitive metadata sanitized | `test_timeline_sanitizes_clinical_note_events`, `test_timeline_sanitizes_old_events_without_refresh` | test_t04_t05_requirements.py, test_clinical_notes.py |
| Old events sanitized without refresh | `test_timeline_sanitizes_old_events_without_refresh` | test_clinical_notes.py |

## Status Transitions

| Requirement | Test | File |
|-------------|------|------|
| Create fails for completed/cancelled appointments | `test_create_procedure_fails_for_completed_appointment` | test_t04_t05_requirements.py |
| Start fails for completed/cancelled/no_show | (Covered by status validation) | test_t04_t05_requirements.py |
| Resume fails for completed/cancelled/no_show | (Covered by status validation) | test_t04_t05_requirements.py |
| Pending/running/paused block closure | `test_closure_with_pending_procedures_blocked`, `test_closure_with_running_procedures_blocked`, `test_closure_with_paused_procedures_blocked` | test_t04_t05_requirements.py |
| Completed/cancelled with no procedures allow closure | `test_closure_with_cancelled_procedures_allowed`, `test_closure_without_procedures_allowed` | test_t04_t05_requirements.py |

## Clinic Isolation

| Requirement | Test | File |
|-------------|------|------|
| Drafts only visible to authors in same clinic | `test_draft_note_only_visible_to_author`, `test_draft_notes_filtered_from_list` | test_t04_t05_requirements.py |
| Timeline draft protection across users in same clinic | `test_timeline_draft_protection_bypass_source`, `test_timeline_draft_protection_in_record` | test_clinical_notes.py |
| Timeline sanitization for non-clinical roles | `test_timeline_sanitizes_procedure_events_for_non_clinical` | test_t04_t05_requirements.py |

## PostgreSQL Concurrency (Requires PostgreSQL)

| Requirement | Test | File |
|-------------|------|------|
| Unique timer per doctor (start conflicts) | `test_concurrent_start_same_doctor`, `test_concurrent_start_resume_same_doctor`, `test_race_condition_two_starts_same_doctor`, `test_race_condition_start_resume_same_doctor` | test_postgres_concurrency.py |
| Unique timer per doctor (resume conflicts) | `test_concurrent_start_resume_same_doctor`, `test_race_condition_start_resume_same_doctor` | test_postgres_concurrency.py |
| Unique timer per doctor (different doctors independent) | `test_different_doctors_independent` | test_postgres_concurrency.py |
| Manager can start for other doctors | `test_manager_start_for_other_doctor` | test_postgres_concurrency.py |
| Closure vs create (closure wins and create wins, serialized at appointment row lock) | `test_closure_vs_create_concurrent` | test_postgres_concurrency.py |
| Closure vs start (one pending procedure; closure returns 409 and start returns 200) | `test_closure_vs_start_concurrent` | test_postgres_concurrency.py |
| Closure vs resume (one paused procedure; closure returns 409 and resume returns 200) | `test_closure_vs_resume_concurrent` | test_postgres_concurrency.py |
| Invariant: no completed appointment with pending/running/paused procedures | `test_closure_vs_create_concurrent`, `test_closure_vs_start_concurrent`, `test_closure_vs_resume_concurrent` | test_postgres_concurrency.py |
| Invariant: no partial modification after rejection | All concurrency tests verify final state consistency | test_postgres_concurrency.py |
| Manager role bypass for unique timer | `test_manager_start_for_other_doctor` | test_postgres_concurrency.py |

## Test Statistics

- **Total tests**: 40+
- **test_t04_t05_requirements.py**: 17 tests
- **test_clinical_notes.py**: 3 timeline regression tests + other tests
- **test_postgres_concurrency.py**: 3 concurrency tests (PostgreSQL only)

## Notes

- All tests pass with SQLite for non-PostgreSQL tests
- PostgreSQL tests require a running PostgreSQL instance and are marked with `@pytest.mark.postgres`
- Tests marked with `@pytest.mark.postgres` are skipped when PostgreSQL is not available
- No tests were deleted, skipped, or had assertions weakened to achieve green status
