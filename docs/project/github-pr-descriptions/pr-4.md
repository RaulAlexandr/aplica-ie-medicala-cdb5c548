# T03: clinic procedure catalog and base prices

Adds the clinic-scoped procedure catalog, codes, names/category, default duration, exact decimal API pricing, currency, activation state and staff UI. This description does not claim that every original T03 requirement has been accepted.

Verified on 2026-10-31: this PR is open/draft and targets main; head 415915faced44dc8ebcbbf782f46646a6316399f. CI run 37018096812 and all three jobs are completed/success.

The catalog is included in integration/t02-t05 at 430ebeed97e5eddf30649ff4cda270251a6b375a and in clinical UI ede0446e03e46daf1dc128afcdf1b1d28acdb6a5. These are separate published branches, not merges into main.

The independent Astra review must reconcile original allowed-localization requirements and HTTP validation/role/migration/concurrency coverage with the actual code and assertions. CI green alone is not full acceptance.
