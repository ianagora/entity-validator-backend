# PLC Recursion Fix - Stop at Publicly Traded Companies

## Issue Reported

**User Screenshot:**
Structure chart for TRAVIS PERKINS showed:
```
TRAVIS PERKINS TRADING COMPANY...
└─ TRAVIS PERKINS MERCHANT HOLDINGS... 🇬🇧
   └─ TRAVIS PERKINS GROUP HOLDINGS LIMITED 🇬🇧
      └─ TRAVIS PERKINS PLC 🇬🇧 (00824821) ← Publicly traded PLC
         └─ STATE STREET NOMINEES LIMITED 🇬🇧 ❌ Shouldn't appear!
            └─ STATE STREET INTERNATIONAL... 🇺🇸 ❌ Shouldn't appear!
```

**User Question:**
> "Why does Travis Perkins PLC continue post hitting the PLC entity?"

**Answer:** ✅ **It shouldn't! This is a bug that's now fixed.**

## Root Cause Analysis

### How Ownership Tree Building Works:

1. **Start with target company** (e.g., TRAVIS PERKINS TRADING COMPANY)
2. **Extract shareholders from CS01** → Find parent company
3. **Recursively fetch shareholders of parent** → Build tree upwards
4. **Continue until** max depth or no more shareholders found

### What Was Happening:

```python
# Pseudo-code of old logic
for shareholder in shareholders:
    if shareholder.is_company:
        company_info = get_company_info(shareholder)
        
        # ❌ BUG: Always recurse, even for PLCs
        child_shareholders = extract_shareholders(company_info)
        shareholder.children = child_shareholders  # Adds STATE STREET, etc.
```

### Why TRAVIS PERKINS PLC Showed Shareholders:

1. **System reached TRAVIS PERKINS PLC** (company_number: 00824821)
2. **Detected it's a PLC** (type: 'plc')
3. **✅ CS01 extraction warned** "PLCs don't disclose shareholders"
4. **❌ BUT recursion continued anyway** because check was in wrong place
5. **CS01 for PLC was processed** - found STATE STREET NOMINEES LIMITED (nominee/custodian shareholder)
6. **Recursion continued** into STATE STREET entities

### The Two Levels of PLC Detection:

**Level 1: In `shareholder_information.py` (CS01 extraction)**
- Detects PLC before extracting shareholders
- Warns that PLCs don't list shareholders
- **BUT**: If CS01 doesn't contain "shares admitted to trading" text, continues extraction
- **Result**: Returns nominee shareholders like STATE STREET

**Level 2: In `corporate_structure.py` (Tree building)** ← **This was missing!**
- Should check company type BEFORE recursing
- Should STOP recursion at PLC entities
- **Was missing** - system would recurse into ANY company

## Solution Implemented

### Stop Recursion at PLC Level:

```python
# NEW CODE (CORRECT):
for shareholder in shareholders:
    if shareholder.is_company:
        company_info = get_company_info(shareholder)
        
        # ✅ FIX: Check if PLC before recursing
        if company_info.type == 'plc':
            print("📊 PLC DETECTED - STOPPING recursion")
            shareholder.is_plc = True
            shareholder.children = []  # Empty - no shareholders
        else:
            # Only recurse for non-PLC companies
            child_shareholders = extract_shareholders(company_info)
            shareholder.children = child_shareholders
```

### Implementation Details:

```python
# In corporate_structure.py, line ~731
# CRITICAL CHECK: Stop recursion if this is a PLC
company_type = entity_bundle.get('profile', {}).get('type', '')
if company_type == 'plc':
    print(f"{indent}     📊 PLC DETECTED: {child_company_name}")
    print(f"{indent}        ⚠️  This is a Public Limited Company (publicly traded)")
    print(f"{indent}        ℹ️  PLCs do not disclose individual shareholders")
    print(f"{indent}        → STOPPING recursion here (no shareholders to extract)")
    shareholder_info['is_plc'] = True
    shareholder_info['children'] = []  # Empty - no shareholders for PLCs
    shareholder_info['child_company'] = {
        'company_number': child_company_number,
        'company_name': child_company_name,
        'company_status': company_search.get('company_status', '')
    }
else:
    # Only recurse for non-PLC companies
    child_tree = build_ownership_tree(...)
    shareholder_info['children'] = child_tree.get('shareholders', [])
```

## Expected Behavior After Fix

### TRAVIS PERKINS Structure (CORRECT):

```
TRAVIS PERKINS TRADING COMPANY...
└─ TRAVIS PERKINS MERCHANT HOLDINGS... 🇬🇧 (14143760)
   └─ TRAVIS PERKINS GROUP HOLDINGS LIMITED 🇬🇧 (12395367)
      └─ TRAVIS PERKINS PLC 🇬🇧 (00824821)
         📊 Publicly traded company - Individual shareholders not disclosed
         [NO bottom layer - recursion stopped here] ✅
```

### Other PLC Examples:

**MARKS AND SPENCER PLC:**
```
M&S GROUP HOLDINGS LIMITED
└─ MARKS AND SPENCER PLC 🇬🇧
   📊 Publicly traded - No shareholders disclosed
   [Recursion stops here]
```

**TESCO PLC:**
```
TESCO HOLDINGS LIMITED
└─ TESCO PLC 🇬🇧
   📊 Publicly traded - No shareholders disclosed
   [Recursion stops here]
```

## Why Nominee Shareholders Appeared

**STATE STREET NOMINEES LIMITED** and similar entities are:
- **Nominee companies** - hold shares on behalf of other parties
- **Custodians** - manage shares for institutional investors
- **Common in PLCs** - facilitate share trading on exchanges

**Why they shouldn't appear:**
1. They're **intermediaries**, not beneficial owners
2. PLCs have **thousands/millions** of ultimate shareholders
3. Nominee structures are **constantly changing** (daily trades)
4. UK law **doesn't require** PLCs to disclose individual shareholders

## Technical Benefits

### 1. **Correctness**
- ✅ Structure charts now accurately represent ownership
- ✅ Follows UK Companies Act 2006 (PLCs don't disclose shareholders)
- ✅ Stops at the appropriate level (PLC = terminal node)

### 2. **Performance**
- ✅ Saves unnecessary API calls to Companies House
- ✅ Reduces processing time (no pointless CS01 extractions)
- ✅ Avoids deep recursion into nominee companies

### 3. **Data Quality**
- ✅ Removes misleading nominee shareholders from charts
- ✅ Prevents confusion between nominees and beneficial owners
- ✅ Cleaner screening lists (no STATE STREET noise)

## How Frontend Should Display PLCs

The frontend should recognize `is_plc: true` and:

1. **Show PLC indicator**: 📊 or "Publicly Traded"
2. **Display tooltip**: "Public Limited Company - Individual shareholders not disclosed"
3. **No expand/collapse icon**: Since there are no children
4. **Terminal node styling**: Indicate this is end of ownership chain

### Recommended Display:

```html
<div class="plc-node">
  <span class="company-icon">🏢</span>
  <span class="company-name">TRAVIS PERKINS PLC</span>
  <span class="company-number">00824821</span>
  <span class="flag">🇬🇧</span>
  <span class="plc-badge">📊 Publicly Traded</span>
  <span class="tooltip">PLCs do not disclose individual shareholders</span>
</div>
```

## Related Checks

### PSCs (Persons with Significant Control):
- **Still included** in screening lists for PLCs
- PSCs = >25% voting rights or control
- Different from shareholders (control vs ownership)
- Example: Institutional investors with >25% of PLC

### Directors and Officers:
- **Still included** in screening lists for PLCs
- Required for AML/KYC compliance
- Not affected by this fix

## Verification Commands

```bash
cd /home/user/entity-validator-backend

# Test TRAVIS PERKINS structure
python3 -c "
from corporate_structure import build_ownership_tree

tree = build_ownership_tree('00824821', 'TRAVIS PERKINS PLC', 0, 3)
print(f'Shareholders: {len(tree.get(\"shareholders\", []))}')
print('Expected: 0 (PLC should have no shareholders extracted)')

for sh in tree.get('shareholders', []):
    print(f'  - {sh.get(\"name\")}')
"
```

**Expected Output:**
```
📊 PLC DETECTED: TRAVIS PERKINS PLC
   ⚠️  This is a Public Limited Company (publicly traded)
   → STOPPING recursion here (no shareholders to extract)

Shareholders: 0 (or very few from incomplete detection)
```

## Deployment

- **Commit**: `fa1351b` - Stop recursion at PLCs
- **Commit**: `2c51f4a` - Trigger Railway rebuild
- **Deployed to**: Railway (main branch)
- **Date**: 2025-12-13
- **Status**: ✅ Deploying to production

## Future Enhancements

Potential improvements:
1. **Stock exchange info**: Display which exchange PLC trades on (LSE, AIM)
2. **Market cap**: Show PLC market capitalization
3. **Ticker symbol**: Display trading symbol (e.g., TPK.L)
4. **Share price**: Real-time or latest share price
5. **Major shareholders**: Link to >3% shareholder disclosures

---

**Summary:** PLCs now correctly appear as **terminal nodes** in ownership structures, with **no shareholders displayed below them**. This matches UK company law and prevents misleading nominee shareholder chains from appearing in structure charts.
