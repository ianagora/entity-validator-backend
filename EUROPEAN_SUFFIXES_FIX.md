# European Company Suffixes Fix

## Issue
**SIXT SE** was being classified as a **person** instead of a **company/entity**, causing it to appear incorrectly in the screening list.

## Root Cause
The `is_company_name()` function in `corporate_structure.py` only checked for UK and US company suffixes:
- UK: Limited, Ltd, PLC, LLP
- US: Corporation, Corp, Inc

**Missing:** European company legal forms like:
- SE (Societas Europaea - European Company)
- SA (Société Anonyme - French/Spanish public limited company)
- AG (Aktiengesellschaft - German public limited company)
- GmbH (German private limited company)
- NV, BV (Dutch companies)
- SpA, SRL (Italian companies)
- AB (Swedish companies)
- And many more...

## Solution
Added comprehensive European company suffixes to the `is_company_name()` function:

### European Suffixes Added
- **SE** / S.E. - Societas Europaea (pan-European)
- **SA** / S.A. - Société Anonyme (French/Spanish/Portuguese)
- **SARL** / S.A.R.L. - Société à Responsabilité Limitée (French)
- **GmbH** - Gesellschaft mit beschränkter Haftung (German)
- **AG** / A.G. - Aktiengesellschaft (German/Swiss/Austrian)
- **NV** / N.V. - Naamloze Vennootschap (Dutch/Belgian)
- **BV** / B.V. - Besloten Vennootschap (Dutch)
- **SpA** / S.p.A. - Società per Azioni (Italian)
- **SRL** / S.r.l. - Società a Responsabilità Limitata (Italian)
- **AB** - Aktiebolag (Swedish)
- **OY** - Osakeyhtiö (Finnish)
- **AS** - Aksjeselskap (Norwegian)
- **A/S** - Aktieselskab (Danish)

## Testing
Tested with 26 test cases covering:
- ✅ 17 European companies (SIXT SE, BMW AG, TOTAL SA, etc.)
- ✅ 5 UK/US companies
- ✅ 4 individual persons (correctly identified as non-companies)

**Result:** 26/26 tests passed ✅

## Impact
- **SIXT SE** now correctly identified as a company
- Will be enriched recursively like other corporate shareholders
- Will appear in correct section of screening list (corporate entities, not individuals)
- Fixes classification for ALL European companies in ownership trees

## Deployment
- **Commit:** `f7ee273`
- **Branch:** `main`
- **Status:** ✅ Pushed to Railway
- **Expected Result:** SIXT SE should appear as an entity in the screening list after re-upload

## Testing Instructions
1. Wait 2-3 minutes for Railway deployment
2. Re-upload **SIXT RENT A CAR LIMITED**
3. Check screening list - **SIXT SE** should be in **"Corporate Entities"** section
4. Verify **SIXT SE** has an enrichment attempt (recursive lookup)
5. Check that it's NOT in the **"Individuals"** section

---

**Fix verified with comprehensive test suite covering European, UK, and US company legal forms.**
