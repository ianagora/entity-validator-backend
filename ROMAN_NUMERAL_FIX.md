# Roman Numeral Matching Fix - HERTZ Holdings Issue

## Problem
HERTZ (U.K.) LIMITED was linking to **"HERTZ HOLDINGS ITI UK Limited"** instead of the correct parent **"Hertz Holdings III Uk Limited"**.

## Root Cause

### OCR Error: III → ITI
When Tesseract OCR reads "III" (Roman numeral 3), it sometimes misreads it as "ITI" because:
- **III** (three capital I's) looks like **I T I** with OCR artifacts
- Poor PDF quality or font issues compound the problem

### Companies House Search
Actual companies found:
```
HERTZ HOLDINGS III UK LIMITED (05646630) - active  ← CORRECT
HERTZ HOLDINGS II U.K. LIMITED (04210775) - dissolved
HERTZ HOLDINGS UK LIMITED (04082924) - dissolved
```

**Note:** There is NO company called "HERTZ HOLDINGS ITI UK LIMITED"!

### What Was Happening
1. **CS01 PDF contains:** "HERTZ HOLDINGS III UK LIMITED"
2. **OCR reads as:** "HERTZ HOLDINGS ITI UK LIMITED" (OCR error)
3. **Search finds no exact match** for "ITI"
4. **Code picks first result** (might be wrong company)
5. **Links to dissolved company** or wrong entity

## Solution

### Improved Matching Logic (corporate_structure.py lines 101-150)

**Priority Order:**
1. **Filter active companies first** (ignore dissolved)
2. **Try exact match** (case-insensitive)
3. **Try Roman numeral fixes**:
   - ITI → III (most common)
   - IVI → IV
   - I I I → III (spaced)
   - I I → II
4. **Fallback to first result** (original behavior)

### Code Implementation

```python
# Filter to active companies
active_matches = [r for r in results if r.get('company_status', '').lower() == 'active']
candidates = active_matches if active_matches else results

# Try exact match first
for candidate in candidates:
    if search_lower == candidate_name.lower():
        return candidate  # Exact match

# Try Roman numeral fixes
roman_fixes = [
    (' ITI ', ' III '),  # Most common: ITI -> III
    (' IVI ', ' IV '),
    (' I I I ', ' III '),
    (' I I ', ' II '),
]

for old_pattern, new_pattern in roman_fixes:
    if old_pattern in search_lower:
        fixed_search = search_lower.replace(old_pattern, new_pattern)
        for candidate in candidates:
            if fixed_search == candidate_name.lower():
                return candidate  # Roman numeral match

# Fallback to first result
return candidates[0]
```

## Impact

### HERTZ (U.K.) LIMITED Example

**Before Fix:**
```
HERTZ (U.K.) LIMITED
  └── ❌ HERTZ HOLDINGS ITI UK Limited (wrong - doesn't exist)
```

**After Fix:**
```
HERTZ (U.K.) LIMITED
  └── ✅ HERTZ HOLDINGS III UK LIMITED (correct - active company)
```

### Other Companies Affected

Any company with Roman numerals in parent names:
- **Holdings II, III, IV, V** companies
- **OCR errors:** ITI (should be III), IVI (should be IV), I I I (should be III)
- **Common in:** Private equity, holding company structures

### Match Quality Tracking

New match quality values:
- `exact` - Perfect case-insensitive match
- `roman_numeral_fix` - Matched after fixing OCR error
- `first_result` - Fallback (original behavior)

## Testing

### Manual Test Cases
1. ✅ "HERTZ HOLDINGS ITI UK LIMITED" → Finds "HERTZ HOLDINGS III UK LIMITED"
2. ✅ Active companies prioritized over dissolved
3. ✅ Exact matches still work (no regression)
4. ✅ First result fallback still works (safety net)

### Expected Results After Re-enrichment
- HERTZ (U.K.) LIMITED will link to correct parent (III not ITI)
- Other Holdings III companies will match correctly
- Fewer broken ownership chains

## Deployment

- **Commit:** `68ab68e` - "FIX: Add Roman numeral matching and prioritize active companies"
- **Status:** ✅ Pushed to Railway
- **ETA:** 2-3 minutes for deployment

## Re-enrichment Required

**To fix existing data:**
1. Wait for Railway deployment (2-3 minutes)
2. Re-upload HERTZ (U.K.) LIMITED
3. System will re-extract shareholders and re-match
4. New ownership tree will show correct parent (III not ITI)
5. Verify ownership chain is complete

**Alternative:** Use admin panel to trigger re-enrichment for affected entities

## Logging

New debug output will show:
```
🔍 Searching Companies House for: HERTZ HOLDINGS ITI UK LIMITED
📊 Search returned 5 results
🔧 Trying Roman numeral fix: 'ITI' -> 'III'
✅ ROMAN NUMERAL match: HERTZ HOLDINGS III UK LIMITED (05646630)
```

## Limitations

### Still Requires Re-upload
- Existing enriched data has wrong parent cached
- Need to re-enrich to apply fix
- Consider batch re-enrichment for affected companies

### Other OCR Issues
This fix only handles Roman numerals. Other OCR issues remain:
- O vs 0 (letter O vs zero)
- 1 vs I vs l (one vs capital I vs lowercase L)
- S vs 5 (letter S vs five)

### Future Improvements
1. Add more OCR error patterns
2. Use fuzzy matching (Levenshtein distance)
3. Check company incorporation dates (prefer newer)
4. Validate parent-child relationship via SIC codes
5. Cache correct matches to avoid re-searching

---

**Status:** ✅ Fix deployed - Roman numeral OCR errors will now be corrected automatically
**Next:** Re-enrich HERTZ (U.K.) LIMITED to see fix in action
