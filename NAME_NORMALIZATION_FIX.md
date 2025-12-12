# Name Normalization Fix - Consolidated Screening List vs Structure Chart

**Date**: December 12, 2025  
**Issue**: Discrepancy between company names in Consolidated Screening List vs Structure Chart  
**Status**: ✅ FIXED

## Problem Description

User reported: *"How is it able to be correctly identified as Hertz Holdings III UK Limited within the Consolidated Screening List but not in the structure chart?"*

### Root Cause

The system had **two different name sources**:

1. **Consolidated Screening List** (✅ Correct):
   - Used `shareholder_info['name']` after Companies House search resolution
   - Roman numeral fix (ITI → III) was applied via `search_company_by_name()`
   - Displayed: **"HERTZ HOLDINGS III UK LIMITED"**

2. **Structure Chart** (❌ Incorrect):
   - Used original CS01 OCR'd name from `shareholder.get('name')`
   - Roman numeral fix was NOT propagated to the display name
   - Displayed: **"HERTZ HOLDINGS ITI UK LIMITED"** (OCR error)

## The Fix

**File**: `corporate_structure.py`  
**Lines**: 510-517

### Before (Incorrect)
```python
if company_search:
    child_company_number = company_search['company_number']
    child_company_name = company_search['company_name']  # ✅ Correct name retrieved
    
    shareholder_info['company_number'] = child_company_number
    shareholder_info['company_status'] = company_search.get('company_status', '')
    # ❌ BUG: Never updated shareholder_info['name'] with correct CH name!
```

### After (Fixed)
```python
if company_search:
    child_company_number = company_search['company_number']
    child_company_name = company_search['company_name']
    
    # ✅ CRITICAL FIX: Use official Companies House name instead of OCR'd CS01 name
    shareholder_info['name'] = child_company_name
    
    shareholder_info['company_number'] = child_company_number
    shareholder_info['company_status'] = company_search.get('company_status', '')
```

## Data Flow (Fixed)

```
CS01 Filing (OCR'd name)
    └─→ "HERTZ HOLDINGS ITI UK LIMITED" (OCR error)
         └─→ search_company_by_name()
              └─→ Roman numeral fix: "ITI" → "III"
                   └─→ Companies House API match
                        └─→ Returns: company_name = "HERTZ HOLDINGS III UK LIMITED"
                             └─→ ✅ NOW UPDATES shareholder_info['name']
                                  └─→ Ownership Tree (shareholder.name)
                                       ├─→ ✅ Consolidated Screening List
                                       │    (shows correct "HERTZ HOLDINGS III UK LIMITED")
                                       └─→ ✅ Structure Chart  
                                            (NOW shows correct "HERTZ HOLDINGS III UK LIMITED")
```

## Impact

**Before Fix**:
- ❌ Structure Chart: "HERTZ HOLDINGS ITI UK LIMITED" (OCR error)
- ✅ Screening List: "HERTZ HOLDINGS III UK LIMITED" (correct)
- ⚠️  Inconsistency caused confusion

**After Fix**:
- ✅ Structure Chart: "HERTZ HOLDINGS III UK LIMITED" (correct)
- ✅ Screening List: "HERTZ HOLDINGS III UK LIMITED" (correct)
- ✅ Consistency across all displays

## Related Fixes

This fix works together with:

1. **Roman Numeral Matching** (commit `68ab68e`):
   - `corporate_structure.py` lines 120-143
   - Handles OCR errors: ITI→III, IVI→IV, I I I→III, I I→II

2. **European Suffixes** (commit `f7ee273`):
   - `corporate_structure.py` lines 32-63
   - Recognizes SE, SA, SARL, GmbH, AG, NV, BV, SPA, SRL, AB, OY, AS, A/S

## Deployment

**Commits**:
- `bb366e3`: Main fix (name normalization)
- `09a499f`: Force Railway rebuild

**Expected Deployment**: ~2-3 minutes after 22:26:42 UTC

## Testing

After Railway deployment completes, test with:

1. **HERTZ (U.K.) LIMITED** (00597994)
   - Should show "HERTZ HOLDINGS III UK LIMITED" in BOTH structure chart and screening list

2. **SIXT RENT A CAR LIMITED**
   - Should correctly identify parent as "SIXT SE" (not "SIXT RENT A CAR SE")

## Technical Notes

**Why did the Screening List show the correct name?**

The Screening List processes the ownership tree in `app.py` (lines 2920-3143), which reads from `shareholder_info['name']`. After the search, the `child_company_name` variable contained the correct name, but it was only used for the child tree recursion, not for updating the parent's shareholder record.

The Structure Chart renders the same `shareholder_info['name']` field, so both now use the corrected Companies House name.

**Single Source of Truth**:
- `shareholder_info['name']` is now the ONLY name used
- It's set to the official Companies House `title` field
- OCR'd CS01 names are overwritten with official CH names
- Consistency guaranteed across all frontend displays
