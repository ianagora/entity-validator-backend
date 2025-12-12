# Similarity Threshold Fix for Company Matching

## Problem Identified

When extracting shareholder information from CS01 filings, the system would search Companies House for each corporate shareholder. If no exact match was found, it would **always fall back to the first search result**, even when that result was completely unrelated.

### Examples of False Matches:
1. **PROJECT ARDENT TOPCO LIMITED** (Jersey company) → **ARDENT PROJECTS LIMITED** (unrelated UK company)
2. **SIXT SE** (German company) → **SIXT PLC** (UK company) *(fixed by foreign suffix detection)*
3. **HERTZ HOLDINGS NETHERLANDS 2 B.V.** → **2 HERTZ LTD** *(fixed by foreign suffix detection)*

## Root Cause

In `search_company_by_name()` function (lines 305-318 in `corporate_structure.py`):

```python
# OLD CODE (INCORRECT):
# Fallback: Use first result (original behavior)
best_match = candidates[0]
return {
    'company_number': best_match.get('company_number'),
    'company_name': best_match.get('title', ''),
    'match_quality': 'first_result'
}
```

This logic would **always return something**, even when the match was completely wrong.

## Solution Implemented

Added **Jaccard similarity scoring** with a **0.5 (50%) threshold**:

```python
# NEW CODE (CORRECT):
# Calculate similarity score (word overlap)
search_words = set(search_lower.split())
match_words = set(best_match_name.split())

# Remove stop words (limited, ltd, holdings, etc.)
stop_words = {'limited', 'ltd', 'plc', 'llp', 'holdings', 'company'}
search_words_filtered = search_words - stop_words
match_words_filtered = match_words - stop_words

# Jaccard similarity = intersection / union
similarity = intersection / union

if similarity >= 0.5:
    return match  # Good enough match
else:
    return None   # Not a match, likely foreign/dissolved
```

## How It Works

### Similarity Calculation Example:

**Case 1: PROJECT ARDENT TOPCO LIMITED vs ARDENT PROJECTS LIMITED**
- Search words: `{project, ardent, topco}`
- Match words: `{ardent, projects}`
- Intersection: `{ardent}` = 1 word
- Union: `{project, ardent, topco, projects}` = 4 words
- **Similarity: 1/4 = 0.25 (25%)** ❌ Too low → Return `None`

**Case 2: HERTZ HOLDINGS III UK LIMITED (exact match)**
- Exact match detected before similarity check
- **Result: Exact match** ✅

**Case 3: SIXT SE (foreign company)**
- Foreign suffix detected → Skip CH search entirely
- **Result: `None`** ✅

## Impact on Structure Charts

### Before Fix:
```
ENTERPRISE LIMITED
└─ PROJECT ARDENT BIDCO LIMITED 🇬🇧
   └─ ARDENT PROJECTS LIMITED 🇬🇧 (16219782)  ❌ WRONG!
```

### After Fix:
```
ENTERPRISE LIMITED
└─ PROJECT ARDENT BIDCO LIMITED 🇬🇧 (14287080)
   └─ PROJECT ARDENT TOPCO LIMITED ❓ (Not in Companies House)  ✅ CORRECT!
```

The **❓ indicator** signals:
- Company not found in UK Companies House
- Likely foreign (Jersey, Luxembourg, Delaware, etc.)
- Or dissolved/not registered in UK

## Frontend Display

The frontend should display the **❓** indicator when:
- `shareholder_info['search_failed'] = True`
- No `company_number` present
- Shows tooltip: "Not in Companies House (likely foreign or dissolved)"

## Threshold Selection

**Why 0.5 (50%)?**
- Too low (e.g., 0.3): Still allows poor matches
- Too high (e.g., 0.7): Might reject valid OCR variations
- **0.5 balances**: Accepts reasonable matches, rejects unrelated companies

### Test Cases:

| Search Query | First Result | Similarity | Decision |
|--------------|--------------|------------|----------|
| PROJECT ARDENT TOPCO LIMITED | ARDENT PROJECTS LIMITED | 0.25 | ❌ Reject |
| HERTZ HOLDINGS ITI UK LIMITED | HERTZ HOLDINGS III UK LIMITED | 1.0 (exact after roman fix) | ✅ Accept |
| SIXT SE | SIXT PLC | N/A (foreign detected) | ❌ Skip CH |
| UNITED KENNING RENTAL GROUP LIMITED | (exact match) | 1.0 | ✅ Accept |

## Related Fixes

This fix complements other improvements:

1. **Foreign Suffix Detection** (`get_country_from_suffix()`)
   - Detects B.V., AG, SE, S.A., INC., etc.
   - Skips CH search for foreign companies
   - Added in commit `22ae446`

2. **Roman Numeral Correction**
   - Fixes OCR errors: ITI → III, IVI → IV
   - Allows exact matching despite OCR mistakes

3. **Similarity Threshold** (this fix)
   - Prevents false fallback matches
   - Returns `None` when no good match found

## Verification Commands

```bash
# Test similarity threshold
cd /home/user/entity-validator-backend
python3 -c "
from corporate_structure import search_company_by_name

# Should return None (similarity too low)
result = search_company_by_name('PROJECT ARDENT TOPCO LIMITED')
print(f'PROJECT ARDENT TOPCO: {result}')

# Should return exact match
result = search_company_by_name('HERTZ HOLDINGS III UK LIMITED')
print(f'HERTZ HOLDINGS III: {result.get(\"company_name\") if result else None}')
"
```

## Deployment

- **Commit**: `ab195c1`
- **Deployed to**: Railway (main branch)
- **Date**: 2025-12-12
- **Status**: ✅ Live in production

## Benefits

1. ✅ **No more false matches**: Foreign/dissolved companies won't link to unrelated UK companies
2. ✅ **Clear indication**: ❓ shows user that company wasn't found in CH
3. ✅ **Better data quality**: Structure charts now accurately represent ownership
4. ✅ **Jersey/Luxembourg transparency**: Users can identify offshore parent companies

## Future Enhancements

Potential improvements:
1. Add international registry lookups (Jersey, Luxembourg, Delaware)
2. Show "Likely jurisdiction: Jersey" based on BIDCO/TOPCO naming patterns
3. Allow manual company linking for known foreign parents
4. Add fuzzy matching for minor spelling variations (with higher threshold)

---

**Related Documentation:**
- [FOREIGN_COMPANY_FIX.md](./FOREIGN_COMPANY_FIX.md) - Foreign suffix detection
- [FOREIGN_SUFFIX_EXPANSION.md](./FOREIGN_SUFFIX_EXPANSION.md) - Extended suffix list
- [BUG_INVESTIGATION_SUMMARY.md](./BUG_INVESTIGATION_SUMMARY.md) - Original HERTZ investigation
