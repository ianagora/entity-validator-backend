# Foreign Company Detection Fix

**Date**: 2025-12-12  
**Commits**: `083b3c6`, `721c746`  
**Issue**: Structure chart showing "2 HERTZ LTD" instead of "HERTZ HOLDINGS NETHERLANDS 2 B.V."

## Problem Description

### What Was Happening

When building corporate ownership trees, the system encountered **foreign (non-UK) companies** as shareholders in CS01 filings. The bug occurred because:

1. **OCR/OpenAI correctly extracted** the foreign company name from CS01 (e.g., "HERTZ HOLDINGS NETHERLANDS 2 B.V.")
2. **Companies House search was attempted** for this foreign company
3. **Search returned UK companies with similar text** (e.g., "2 HERTZ LTD")
4. **System used "first result" fallback**, incorrectly assigning the wrong UK company as the parent

### Example Case: HERTZ HOLDINGS III UK LIMITED

**Company**: HERTZ HOLDINGS III UK LIMITED (05646630)  
**Actual Parent** (from CS01): `HERTZ HOLDINGS NETHERLANDS 2 B.V.` (Dutch company, B.V. = Besloten Vennootschap)

**Before Fix**:
- CS01 extraction: ✅ "HERTZ HOLDINGS NETHERLANDS 2 B.V."
- CH search: Returns 5 results, first is "2 HERTZ LTD" (16103071)
- Structure chart: ❌ Shows "2 HERTZ LTD" (WRONG)
- Screening list: ❌ Shows "2 HERTZ LTD" (WRONG)

**After Fix**:
- CS01 extraction: ✅ "HERTZ HOLDINGS NETHERLANDS 2 B.V."
- Foreign company detected: ✅ B.V. suffix detected, CH search skipped
- Structure chart: ✅ Shows "HERTZ HOLDINGS NETHERLANDS 2 B.V." (CORRECT)
- Screening list: ✅ Shows "HERTZ HOLDINGS NETHERLANDS 2 B.V." (CORRECT)

## Technical Solution

### 1. New Function: `is_foreign_company()`

Added to `corporate_structure.py` to detect non-UK companies by legal suffix:

```python
def is_foreign_company(company_name: str) -> bool:
    """
    Detect if a company is foreign (non-UK) based on legal suffixes
    Returns True if company appears to be registered outside the UK
    """
    foreign_suffixes = [
        'B.V.',      # Netherlands: Besloten Vennootschap
        'N.V.',      # Netherlands: Naamloze Vennootschap
        'GMBH',      # Germany: Gesellschaft mit beschränkter Haftung
        'AG',        # Germany/Switzerland: Aktiengesellschaft
        'S.A.',      # France/Spain/Belgium: Société Anonyme / Sociedad Anónima
        'S.A.R.L.',  # France: Société à responsabilité limitée
        'S.R.L.',    # Italy/Romania: Società a responsabilità limitata
        'S.P.A.',    # Italy: Società per Azioni
        'A.S.',      # Denmark/Norway: Aktieselskab / Aksjeselskap
        'AB',        # Sweden: Aktiebolag
        'OY',        # Finland: Osakeyhtiö
        'LLC',       # US: Limited Liability Company
        'INC.',      # US: Incorporated
        'CORP.',     # US: Corporation
        'PTY LTD',   # Australia: Proprietary Limited
        # ... and more
    ]
    # Returns True if name ends with or contains any foreign suffix
```

### 2. Updated: `search_company_by_name()`

Modified to skip Companies House search for foreign companies:

```python
def search_company_by_name(company_name: str) -> Optional[Dict[str, Any]]:
    # CRITICAL: Check if this is a foreign company first
    if is_foreign_company(company_name):
        print(f"  ⚠️  Foreign company detected, skipping Companies House search: {company_name}")
        return None  # Don't search CH for foreign companies
    
    # ... existing CH search logic ...
```

### 3. Existing Logic Handles `None` Return

When `search_company_by_name()` returns `None`, the existing code (line 612-614) already handles this:

```python
if company_search:
    # UK company found - use CH data
    shareholder_info['name'] = company_search['company_name']
    shareholder_info['company_number'] = company_search['company_number']
    # ... recursion ...
else:
    # Foreign company or search failed - keep original name
    print(f"{indent}     ⚠️  Could not find company in Companies House")
    shareholder_info['search_failed'] = True
    # shareholder_info['name'] remains the OCR'd name from CS01
```

## Impact on Data Flow

### Before Fix
```
CS01 PDF
  ↓ [OCR/OpenAI extraction]
"HERTZ HOLDINGS NETHERLANDS 2 B.V."
  ↓ [search_company_by_name]
Companies House Search: "HERTZ HOLDINGS NETHERLANDS 2 B.V."
  ↓ [Returns 5 results]
First result: "2 HERTZ LTD" (16103071) ❌ WRONG
  ↓ [build_ownership_tree]
Structure Chart: "2 HERTZ LTD"
Screening List: "2 HERTZ LTD"
```

### After Fix
```
CS01 PDF
  ↓ [OCR/OpenAI extraction]
"HERTZ HOLDINGS NETHERLANDS 2 B.V."
  ↓ [is_foreign_company check]
B.V. suffix detected → Foreign company ✅
  ↓ [search_company_by_name returns None]
No CH search performed
  ↓ [build_ownership_tree]
Keep original name from CS01 ✅
Structure Chart: "HERTZ HOLDINGS NETHERLANDS 2 B.V." ✅ CORRECT
Screening List: "HERTZ HOLDINGS NETHERLANDS 2 B.V." ✅ CORRECT
```

## Supported Foreign Company Types

The fix detects companies from:
- 🇳🇱 Netherlands: B.V., N.V.
- 🇩🇪 Germany: GmbH, AG
- 🇫🇷 France: S.A., S.A.R.L., SARL
- 🇮🇹 Italy: S.R.L., S.P.A., SRL, SPA
- 🇪🇸 Spain: S.A.
- 🇧🇪 Belgium: S.A.
- 🇩🇰 Denmark: A.S.
- 🇳🇴 Norway: A.S.
- 🇸🇪 Sweden: AB
- 🇫🇮 Finland: OY
- 🇨🇭 Switzerland: AG
- 🇺🇸 USA: LLC, INC., CORP.
- 🇦🇺 Australia: PTY LTD

## Testing

### Test Script
```bash
cd /home/user/entity-validator-backend
python3 -c "
from corporate_structure import is_foreign_company, search_company_by_name

# Test cases
test_cases = [
    'HERTZ HOLDINGS NETHERLANDS 2 B.V.',  # Netherlands
    'DEUTSCHE BANK AG',                    # Germany
    'TOTAL S.A.',                          # France
    'FIAT S.P.A.',                         # Italy
    'MICROSOFT CORPORATION INC.',          # USA
    'HERTZ (U.K.) LIMITED',                # UK (should NOT be foreign)
    'APPLE UK LIMITED'                     # UK (should NOT be foreign)
]

for company in test_cases:
    is_foreign = is_foreign_company(company)
    print(f'{company}: {'FOREIGN' if is_foreign else 'UK'}')
"
```

### Expected Output
```
HERTZ HOLDINGS NETHERLANDS 2 B.V.: FOREIGN ✅
DEUTSCHE BANK AG: FOREIGN ✅
TOTAL S.A.: FOREIGN ✅
FIAT S.P.A.: FOREIGN ✅
MICROSOFT CORPORATION INC.: FOREIGN ✅
HERTZ (U.K.) LIMITED: UK ✅
APPLE UK LIMITED: UK ✅
```

### Verification
```bash
# Search for foreign company - should return None
python3 -c "
from corporate_structure import search_company_by_name
result = search_company_by_name('HERTZ HOLDINGS NETHERLANDS 2 B.V.')
print(f'Result: {result}')
"
# Expected: "Result: None" (no CH search performed)
```

## Edge Cases Handled

1. **Foreign companies with spaces**: "MICROSOFT CORPORATION INC." ✅
2. **Suffixes without periods**: "GMBH" (not "GMBH.") ✅
3. **Multiple word suffixes**: "PTY LTD" ✅
4. **Mixed case**: Detection is case-insensitive ✅
5. **UK companies with "LIMITED"**: Not flagged as foreign ✅

## Related Fixes

This fix complements the previous Roman numeral fix (commit `bb366e3`):
- **Roman numeral fix**: Corrects OCR errors like "ITI" → "III" for UK companies
- **Foreign company fix**: Prevents false matches when shareholders are non-UK entities

## Files Modified

- `corporate_structure.py`: Added `is_foreign_company()`, updated `search_company_by_name()`

## Deployment

**Status**: ✅ Deployed to Railway  
**Commits**: `083b3c6`, `721c746`  
**Expected ETA**: 2-3 minutes for rebuild

## Verification Steps

After deployment, test with HERTZ (U.K.) LIMITED (00597994):

1. Go to application
2. Search for "HERTZ UK LIMITED" or company number "00597994"
3. Check Structure Chart:
   - Should show "HERTZ HOLDINGS III UK LIMITED" as shareholder
   - Parent of "HERTZ HOLDINGS III UK LIMITED" should be "HERTZ HOLDINGS NETHERLANDS 2 B.V." (NOT "2 HERTZ LTD")
4. Check Consolidated Screening List:
   - Should show "HERTZ HOLDINGS NETHERLANDS 2 B.V." in ownership chain

## Future Enhancements

Potential improvements:
1. **Fetch foreign company data** from external registries (e.g., European Business Register)
2. **Add more foreign suffixes** as new jurisdictions are encountered
3. **Store foreign company metadata** (country, registry URL) for reference
4. **Flag foreign companies in UI** with country flags or indicators
