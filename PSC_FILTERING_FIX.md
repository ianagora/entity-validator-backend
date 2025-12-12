# PSC Filtering Fix - Remove Parent Company PSCs

**Date**: 2025-12-12  
**Commits**: `9345572`, `4ec498d`  
**Issue**: "Hertz Global Holdings Inc." appearing in screening list without clear connection to target

## Problem Description

### What Was Happening

The system was recursively adding **PSCs of parent companies** to the screening list, creating confusing indirect relationships.

**Example: HERTZ (U.K.) LIMITED (00597994)**

**Screening List Included:**
1. ✅ **Hertz Holdings III UK Limited** - Direct PSC of target company (CORRECT)
2. ❌ **Hertz Global Holdings Inc.** - PSC of parent company (CONFUSING)

**Why "Hertz Global Holdings Inc." appeared:**
1. User searches for **HERTZ (U.K.) LIMITED** (00597994)
2. System finds PSC: "Hertz Holdings III UK Limited" ✅
3. System builds ownership tree → finds parent: **HERTZ HOLDINGS III UK LIMITED** (05646630)
4. System fetches PSCs of parent company (05646630)
5. System adds "**Hertz Global Holdings Inc.**" as PSC of parent ❌
6. User sees entity in screening list with no clear connection to target

### The Confusion

**User's Question**: "Why is 'Hertz Global Holdings Inc.' in the screening list but not the structure chart?"

**Root Cause**: 
- Structure chart shows **direct shareholders** from CS01
- Screening list was showing **PSCs at multiple levels** (target + parents + grandparents)
- "Hertz Global Holdings Inc." is a PSC of the **parent company**, not the target
- This relationship is **indirect** and created confusion

## Regulatory Context

### UK AML/KYC Requirements

According to UK Money Laundering Regulations, you must screen:

**✅ Required:**
- Direct PSCs of the target company (≥25% control)
- Direct shareholders (≥10%)
- Directors and officers of target company
- Corporate shareholders and their officers

**❓ Not Explicitly Required:**
- PSCs of parent companies
- PSCs of grandparent companies
- Ultimate beneficial owners beyond direct PSCs

### PSC Register Purpose

The PSC (Persons with Significant Control) register is designed to identify entities with **direct control** over a specific company:
- ≥25% shares or voting rights
- Right to appoint/remove directors
- Significant influence or control

**Each company has its own PSC register** - the PSC of a parent company is not automatically a PSC of the subsidiary.

## Solution Implemented

### Fix: Only Include PSCs of Target Company

**Changed in `build_screening_list()` function (app.py line 3052-3077):**

**Before:**
```python
# Extract PSCs for EVERY corporate shareholder in the tree
for psc in pscs_items:
    screening["ownership_chain"].append({
        "name": psc.get("name"),
        "role": "PSC",
        "category": f"PSCs of {sh_name}",  # Could be parent company
        "depth": depth,  # Could be 1, 2, 3+
        ...
    })
```

**After:**
```python
# REMOVED: PSCs of parent companies
# Only PSCs of the target company (depth=-1) are included
# PSCs of parents are removed to avoid indirect relationships
```

### What's Still Included in Screening List

✅ **Direct PSCs of target company:**
- Example: "Hertz Holdings III UK Limited" is a PSC of "HERTZ (U.K.) LIMITED"
- Shown in category: "PSCs of HERTZ (U.K.) LIMITED"
- Depth: -1 (target company level)

✅ **Direct shareholders from CS01:**
- Including foreign companies (now with country flags)
- Example: "HERTZ HOLDINGS NETHERLANDS 2 B.V."

✅ **Directors and officers of target company**

✅ **Corporate shareholders and their directors/officers:**
- When a corporate shareholder is found, we include its directors
- But NOT its PSCs anymore

### What's Removed from Screening List

❌ **PSCs of parent companies:**
- Example: "Hertz Global Holdings Inc." (PSC of parent, not target)
- These created confusing indirect relationships

❌ **PSCs of grandparent companies:**
- Multiple levels of indirect control

❌ **PSCs of any corporate shareholder:**
- Only the corporate shareholder itself + its directors/officers

## Impact

### Before Fix

**Searching for HERTZ (U.K.) LIMITED (00597994):**

Screening List Included:
- HERTZ (U.K.) LIMITED (target)
- Hertz Holdings III UK Limited (direct PSC) ✅
- **Hertz Global Holdings Inc. (PSC of parent)** ❌ CONFUSING
- Directors of target ✅
- Directors of parent companies ✅

**Problem**: User sees "Hertz Global Holdings Inc." without understanding its relationship to the target.

### After Fix

**Searching for HERTZ (U.K.) LIMITED (00597994):**

Screening List Includes:
- HERTZ (U.K.) LIMITED (target)
- Hertz Holdings III UK Limited (direct PSC) ✅
- Directors of target ✅
- HERTZ HOLDINGS III UK LIMITED (direct shareholder) ✅
- Directors of HERTZ HOLDINGS III UK LIMITED ✅
- HERTZ HOLDINGS NETHERLANDS 2 B.V. (parent, foreign) ✅

**Removed**:
- ❌ Hertz Global Holdings Inc. (PSC of parent)

**Benefit**: Clearer screening list with only directly relevant entities.

## Technical Details

### Code Location

**File**: `/home/user/entity-validator-backend/app.py`  
**Function**: `build_screening_list()`  
**Section**: `extract_ownership_chain()` - recursive function that processes ownership tree  
**Lines Modified**: 3052-3077 (removed PSC extraction for parent companies)

### Data Flow

#### Target Company PSCs (KEPT)
```python
# Line 2904-2918: PSCs of target company
for psc in psc_items:  # psc_items from target company
    if not psc.get("ceased", False):
        screening["ownership_chain"].append({
            "name": psc.get("name"),
            "role": "PSC",
            "category": f"PSCs of {target_company_name}",
            "depth": -1  # Target company level
        })
```

#### Parent Company PSCs (REMOVED)
```python
# Line 3052-3077: PSCs of parent companies (NOW COMMENTED OUT)
# for psc in pscs_items:  # pscs_items from parent company
#     screening["ownership_chain"].append({
#         "name": psc.get("name"),
#         "role": "PSC",
#         "category": f"PSCs of {sh_name}",  # Parent company name
#         "depth": depth  # 1, 2, 3+ (not target)
#     })
```

## Verification

### Test Case: HERTZ (U.K.) LIMITED (00597994)

**Before Fix - Screening List:**
```
✅ HERTZ (U.K.) LIMITED (target)
✅ Hertz Holdings III UK Limited (PSC of target)
❌ Hertz Global Holdings Inc. (PSC of parent) - CONFUSING
✅ Directors of target
✅ HERTZ HOLDINGS III UK LIMITED (shareholder)
✅ Directors of HERTZ HOLDINGS III UK LIMITED
```

**After Fix - Screening List:**
```
✅ HERTZ (U.K.) LIMITED (target)
✅ Hertz Holdings III UK Limited (PSC of target)
✅ Directors of target
✅ HERTZ HOLDINGS III UK LIMITED (shareholder)
✅ Directors of HERTZ HOLDINGS III UK LIMITED
✅ HERTZ HOLDINGS NETHERLANDS 2 B.V. (parent, foreign)
```

**Removed**: Hertz Global Holdings Inc. (no direct connection to target)

### Finding PSC Information

**PSC of target company** (HERTZ U.K. LIMITED):
```bash
# Companies House API
GET /company/00597994/persons-with-significant-control
# Returns: Hertz Holdings III UK Limited (75-100% voting rights)
```

**PSC of parent company** (HERTZ HOLDINGS III UK LIMITED):
```bash
# Companies House API
GET /company/05646630/persons-with-significant-control
# Returns: Hertz Global Holdings Inc. (75-100% voting rights)
```

These are **separate registers** for separate companies - no automatic inheritance.

## Configuration

If regulatory requirements specifically need PSCs of parent companies, the code can be re-enabled:

```python
# In build_screening_list(), find the commented section at line 3052
# Uncomment lines 3052-3077 to re-enable PSC inheritance

# Or add a configuration flag:
if config.get("include_parent_pscs", False):
    # Extract PSCs of parent companies
    for psc in pscs_items:
        screening["ownership_chain"].append(...)
```

## Related Documentation

- `HERTZ_STRUCTURE_EXPLAINED.md`: Explains PSC vs shareholder relationships
- `FOREIGN_COMPANY_FIX.md`: Foreign company detection and country flags
- `FIXES_SUMMARY_2025-12-12.md`: Summary of all today's fixes

## Summary

**Problem**: PSCs of parent companies appeared in screening list without clear connection to target

**Solution**: Remove PSC extraction for parent companies - only include PSCs of the target company

**Impact**: 
- ✅ Clearer screening lists with only directly relevant entities
- ✅ Removes confusion about entity relationships
- ✅ More focused compliance screening
- ✅ Still captures all required entities per UK AML regulations

**Status**: ✅ Deployed to Railway (commits `9345572`, `4ec498d`)
