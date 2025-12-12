# CSV Export Normalization Analysis

## Current Situation

### Backend CSV Export
**File:** `app.py` lines 3466-3648
**Function:** `export_screening_list_csv()`

**Process:**
1. Calls `build_screening_list(bundle, shareholders, item_dict)` → Raw data
2. Flattens into CSV rows (Entity, Governance & Control, Ownership Chain, UBOs, Trusts)
3. Deduplicates using `canonicalise_name()` (Python function)
   - Normalizes unicode
   - Converts to lowercase
   - Strips legal suffixes (Limited → "", Ltd → "", PLC → "")
   - Removes punctuation

**Example:**
- "UNITED KENNING RENTAL GROUP LIMITED" → "united kenning rental group"
- "UNITED KENNING RENTAL GROUP LTD" → "united kenning rental group"
- ✅ Deduplicated successfully

### Frontend Consolidated Screening List
**File:** `entity-validator-frontend/src/index.tsx` lines 775-1000
**Function:** Inline JavaScript

**Process:**
1. Receives `item.screening_list` from API (same raw data)
2. Calls local `normalizeName()` function (JavaScript)
   - For companies: uppercase, normalize suffixes, remove punctuation
   - For individuals: remove titles, reorder names, sort words
3. Uses `Map` to deduplicate by normalized name
4. Renders consolidated list in UI

**Example:**
- "UNITED KENNING RENTAL GROUP LIMITED" → "UNITED KENNING RENTAL GROUP LTD"
- "UNITED KENNING RENTAL GROUP LTD" → "UNITED KENNING RENTAL GROUP LTD"
- ✅ Deduplicated successfully (but keeps "LTD" suffix)

## Key Differences

| Aspect | Backend CSV | Frontend UI |
|--------|-------------|-------------|
| **Language** | Python | JavaScript |
| **Case** | Lowercase | Uppercase |
| **Legal Suffixes** | Stripped completely | Normalized to standard forms (LTD, PLC) |
| **Individuals** | No special handling | Removes titles, reorders names |
| **Result** | "united kenning rental group" | "UNITED KENNING RENTAL GROUP LTD" |

## Problem

The CSV export and the frontend UI use **different normalization functions**, which could lead to:

1. **Different display names:** CSV might show "COMPANY LIMITED" while UI shows "COMPANY LTD"
2. **Different deduplication results:** Edge cases where one deduplicates but the other doesn't
3. **Inconsistency:** Users expect CSV to match what they see on screen

## Solution Options

### Option 1: Backend normalizes like Frontend (Recommended)
**Change:** Update backend's CSV export to use the same normalization as frontend

**Pros:**
- CSV exactly matches what users see in UI
- Consistency across all exports
- Single source of truth (frontend logic)

**Cons:**
- Need to port JavaScript `normalizeName()` to Python
- Two normalization functions to maintain (Python for CSV, JS for UI)

**Implementation:**
```python
# Add to app.py or utils.py
def normalize_name_for_display(name: str) -> str:
    """
    Normalize name to match frontend display logic
    Returns uppercase with normalized legal suffixes (LTD, PLC, etc.)
    """
    if not name:
        return ''
    
    # For companies: uppercase and normalize suffixes
    name_upper = name.upper().strip()
    
    if any(suffix in name_upper for suffix in ['LIMITED', 'LTD', 'PLC', 'LLP']):
        # Normalize legal suffixes
        normalized = name_upper
        normalized = normalized.replace(' LIMITED', ' LTD')
        if normalized.endswith(' LIMITED'):
            normalized = normalized[:-8] + ' LTD'
        normalized = normalized.replace('P.L.C', 'PLC')
        normalized = normalized.replace('L.L.P', 'LLP')
        normalized = normalized.replace(' COMPANY', ' CO')
        
        # Remove punctuation (keep A-Z, 0-9, spaces)
        normalized = ''.join(c for c in normalized if c.isalnum() or c.isspace())
        
        # Remove extra whitespace
        normalized = ' '.join(normalized.split())
        
        return normalized
    
    # For individuals: basic normalization
    return name_upper.strip()

# In export_screening_list_csv(), replace canonicalise_name() with:
canonical_name = normalize_name_for_display(raw_name)
```

### Option 2: Frontend uses Backend normalization
**Change:** Make frontend call backend to get pre-normalized list

**Pros:**
- Single normalization function (Python only)
- Less client-side processing

**Cons:**
- Slower (extra API call)
- Frontend less responsive
- Backend `canonicalise_name()` produces lowercase (less readable)

### Option 3: Create unified normalization API
**Change:** Backend exposes a `/api/normalize-name` endpoint that both use

**Pros:**
- Single source of truth
- Easy to update normalization rules

**Cons:**
- Extra API calls
- Complexity

## Recommendation

**Use Option 1: Make CSV match frontend display**

**Rationale:**
- Users expect CSV to match what they see
- Frontend normalization produces more readable output (uppercase, keeps "LTD" suffix)
- One-time implementation cost, long-term consistency

**Next Steps:**
1. Port frontend `normalizeName()` function to Python
2. Update `export_screening_list_csv()` to use new function
3. Test with UNITED KENNING to verify deduplication
4. Deploy and verify CSV matches UI exactly

## Testing Checklist

After implementation:
- [ ] Upload UNITED KENNING RENTAL GROUP LIMITED
- [ ] Check frontend consolidated screening list
- [ ] Download CSV
- [ ] Verify CSV names match frontend display exactly
- [ ] Verify no duplicates in CSV (e.g., both "LIMITED" and "LTD" versions)
- [ ] Test with individuals (check title removal works)
- [ ] Test with European companies (SIXT SE, BMW AG)

---

**Decision Required:** Should we implement Option 1 to make CSV match frontend exactly?
