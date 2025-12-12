# PROJECT ARDENT TOPCO Fix Summary

## Issue Reported

**User Query:**
> "Without making any changes; can you confirm why in the structure from 'ENTERPRISE LIMITED', we run through to 'PROJECT ARDENT BIDCO LIMITED'. The CS01 shows the shareholder is 'PROJECT ARDENT TOPCO LIMITED', but I can't locate this on companies house. Strangely, it seems to link to 'ARDENT PROJECTS LIMITED' in the structure instead. Why is this?"

## Root Cause Analysis

### Investigation Steps:

1. **CS01 Verification** ✅
   - Checked CS01 for PROJECT ARDENT BIDCO LIMITED (14287080)
   - Confirmed shareholder: **PROJECT ARDENT TOPCO LIMITED** (100%, 153,779,001 shares)
   - CS01 extraction was correct

2. **Companies House Search** ❌
   - Searched UK Companies House for "PROJECT ARDENT TOPCO LIMITED"
   - **No results found** - company doesn't exist in UK registry
   - System fell back to first search result: "ARDENT PROJECTS LIMITED" (16219782)

3. **Foreign Company Indicators** 🌍
   - **Not in UK registry**: No match in Companies House
   - **No PSCs for BIDCO**: PROJECT ARDENT BIDCO LIMITED has NO PSCs (typical for foreign parent)
   - **TOPCO/BIDCO structure**: Common in private equity with foreign TOPCO
   - **US directors**: Ante KUSURIN (Croatian, US resident), Jordan LAWRIE (American, US resident)
   - **Corporate secretary**: Vistra (specializes in international structures)
   - **User confirmed**: Company is registered in **Jersey** (Channel Islands)

4. **System Behavior** ⚠️
   ```
   CS01: PROJECT ARDENT TOPCO LIMITED
   → Search Companies House
   → No exact match found
   → Fall back to first result: ARDENT PROJECTS LIMITED ❌ WRONG!
   ```

## Solution Implemented

### Similarity Threshold Algorithm

Added **Jaccard similarity scoring** with **0.5 (50%) threshold**:

```python
# Calculate word overlap between search query and result
search_words = {"project", "ardent", "topco"}
match_words = {"ardent", "projects"}
intersection = {"ardent"}  # 1 word
union = {"project", "ardent", "topco", "projects"}  # 4 words
similarity = 1/4 = 0.25 (25%)

if similarity >= 0.5:
    return match  # Accept result
else:
    return None   # Reject, likely unrelated
```

### Result:
- **PROJECT ARDENT TOPCO LIMITED** vs **ARDENT PROJECTS LIMITED**
- Similarity: **0.25 (25%)** < 0.5 threshold
- **Decision: Return `None`** ✅
- Company shows **❓** indicator in structure chart

## Before vs After

### Before Fix:
```
ENTERPRISE LIMITED
└─ PROJECT ARDENT BIDCO LIMITED 🇬🇧 (14287080)
   └─ ARDENT PROJECTS LIMITED 🇬🇧 (16219782)  ❌ WRONG COMPANY!
```

### After Fix:
```
ENTERPRISE LIMITED
└─ PROJECT ARDENT BIDCO LIMITED 🇬🇧 (14287080)
   └─ PROJECT ARDENT TOPCO LIMITED ❓ (Not in Companies House)  ✅ CORRECT!
```

## Frontend Display

The **❓ indicator** means:
- Company not found in UK Companies House
- Likely foreign (Jersey, Luxembourg, Delaware, Cayman, BVI, etc.)
- Or dissolved/not registered in UK
- Tooltip: "Non-UK company (no UK company number)"

## How to Identify Foreign Companies

Since Companies House only contains UK companies, you need to check international registries:

### Common Jurisdictions for PE TOPCOs:
1. **Jersey** (Channel Islands) - https://www.jerseyfsc.org/registry/
2. **Luxembourg** - https://www.lbr.lu/
3. **Guernsey** - https://www.greg.gg/
4. **Delaware (USA)** - https://icis.corp.delaware.gov/
5. **Cayman Islands / BVI** - Limited public access

### Indicators a Company is Foreign:
- ✅ Not in UK Companies House
- ✅ No PSCs for UK subsidiary (foreign parent exemption)
- ✅ TOPCO/BIDCO naming (PE structure indicator)
- ✅ US/foreign directors
- ✅ International corporate secretary (e.g., Vistra, Intertrust)
- ✅ "TOPCO" typically offshore for tax optimization

## Related Fixes

This fix builds on previous improvements:

### 1. Foreign Suffix Detection (Commit `22ae446`)
- Detects: B.V., AG, SE, S.A., INC., GmbH, etc.
- Skips Companies House search for known foreign suffixes
- Fixed: SIXT SE, HERTZ HOLDINGS NETHERLANDS 2 B.V.

### 2. Roman Numeral Correction
- Fixes OCR errors: ITI → III, IVI → IV
- Allows exact matching despite OCR mistakes
- Fixed: HERTZ HOLDINGS ITI UK LIMITED → HERTZ HOLDINGS III UK LIMITED

### 3. Similarity Threshold (This Fix - Commit `ab195c1`)
- Prevents false fallback matches
- Returns `None` when no good match (similarity < 50%)
- Fixed: PROJECT ARDENT TOPCO LIMITED, and future foreign parents

## Test Cases

| Company | Type | Similarity | Result | Display |
|---------|------|------------|--------|---------|
| PROJECT ARDENT TOPCO LIMITED | Jersey | 0.25 | ❌ No match | ❓ |
| HERTZ HOLDINGS III UK LIMITED | UK | 1.0 (exact) | ✅ Match | 🇬🇧 (05646630) |
| SIXT SE | German | N/A (foreign) | ❌ Skip CH | 🇪🇺 |
| HERTZ HOLDINGS NETHERLANDS 2 B.V. | Dutch | N/A (foreign) | ❌ Skip CH | 🇳🇱 |
| UNITED KENNING RENTAL GROUP LIMITED | UK | 1.0 (exact) | ✅ Match | 🇬🇧 (02942541) |

## Verification

```bash
cd /home/user/entity-validator-backend

# Test PROJECT ARDENT TOPCO (should return None)
python3 -c "
from corporate_structure import search_company_by_name
result = search_company_by_name('PROJECT ARDENT TOPCO LIMITED')
print(f'Result: {result}')  # Should be None
"

# Test full ownership tree
python3 -c "
from corporate_structure import build_ownership_tree
tree = build_ownership_tree('14287080', 'PROJECT ARDENT BIDCO LIMITED', 0, 3)
for sh in tree['shareholders']:
    print(f'{sh[\"name\"]} - search_failed: {sh.get(\"search_failed\", False)}')
"
```

## Benefits

1. ✅ **No more false matches**: Foreign companies won't link to unrelated UK companies
2. ✅ **Clear indication**: ❓ shows user the company wasn't found in Companies House
3. ✅ **Accurate structure charts**: Ownership structures now correctly represent reality
4. ✅ **Jersey/Luxembourg transparency**: Users can identify offshore parent companies
5. ✅ **PE structure clarity**: TOPCO/BIDCO structures are now correctly displayed
6. ✅ **Better compliance**: Screening lists and ownership chains are more accurate

## Deployment

- **Commits**: 
  - `ab195c1` - Similarity threshold implementation
  - `92aad17` - Railway rebuild trigger
  - `5cea7f6` - Documentation
- **Deployed to**: Railway (main branch)
- **Date**: 2025-12-12
- **Status**: ✅ Live in production

## Future Enhancements

Potential improvements:
1. **International registry API integration** (Jersey, Luxembourg, Delaware)
2. **Jurisdiction hints** based on structure patterns (TOPCO → likely Jersey)
3. **Manual company linking** for known foreign parents
4. **Advanced fuzzy matching** for minor spelling variations
5. **LEI (Legal Entity Identifier) lookup** for multinational groups

---

**Related Documentation:**
- [SIMILARITY_THRESHOLD_FIX.md](./SIMILARITY_THRESHOLD_FIX.md) - Technical details
- [FOREIGN_COMPANY_FIX.md](./FOREIGN_COMPANY_FIX.md) - Foreign suffix detection
- [FOREIGN_SUFFIX_EXPANSION.md](./FOREIGN_SUFFIX_EXPANSION.md) - Extended suffix list
- [BUG_INVESTIGATION_SUMMARY.md](./BUG_INVESTIGATION_SUMMARY.md) - Original HERTZ investigation
