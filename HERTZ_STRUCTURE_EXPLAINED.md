# HERTZ Corporate Structure Explained

**Date**: 2025-12-12  
**Company**: HERTZ (U.K.) LIMITED (00597994)

## Question: Why is "Hertz Global Holdings Inc." in the Screening List but not the Structure Chart?

This is **CORRECT** and reflects the difference between **direct ownership** (shown in structure chart) and **ultimate beneficial ownership** (shown in screening list).

## Complete Corporate Structure

### Visual Hierarchy

```
Hertz Global Holdings, Inc. (USA) 🇺🇸
    └─ Ultimate Beneficial Owner (PSC: 75-100% voting rights)
        │
        └─ HERTZ HOLDINGS NETHERLANDS 2 B.V. (Netherlands) 🇳🇱
            └─ Direct Shareholder (100% shares)
                │
                └─ HERTZ HOLDINGS III UK LIMITED (05646630) 🇬🇧
                    └─ UK Holding Company
                        │
                        └─ HERTZ (U.K.) LIMITED (00597994) 🇬🇧
                            └─ Operating Company
```

### Data Sources

#### 1. Structure Chart (Direct Ownership from CS01)
**Source**: Companies House CS01 filing (Confirmation Statement with shareholder details)

**What it shows**: Direct shareholders holding shares

For **HERTZ HOLDINGS III UK LIMITED**:
- **Shareholder**: HERTZ HOLDINGS NETHERLANDS 2 B.V.
- **Shares**: 103 ordinary shares (100%)
- **Source**: CS01 filing dated 2024-01-11

#### 2. Screening List (Ultimate Control from PSC Register)
**Source**: Companies House PSC (Persons with Significant Control) register

**What it shows**: Entities with ultimate beneficial ownership or control (≥25% voting rights, ≥25% shares, or right to appoint/remove directors)

For **HERTZ HOLDINGS III UK LIMITED**:
- **PSC #1**: Hertz Global Holdings Inc.
  - **Control**: 75-100% voting rights
  - **Control**: Right to appoint and remove directors
- **PSC #2**: Hertz Global Holdings, Inc. (duplicate entry with different punctuation)
  - **Control**: 75-100% voting rights

## Why This Happens

### International Corporate Structures
Large multinational corporations often use **intermediate holding companies** in different jurisdictions for:
- Tax efficiency
- Legal liability separation
- Regulatory compliance
- Operational flexibility

### HERTZ Example
1. **USA**: Hertz Global Holdings, Inc. (publicly traded parent company)
2. **Netherlands**: HERTZ HOLDINGS NETHERLANDS 2 B.V. (European holding company)
3. **UK**: HERTZ HOLDINGS III UK LIMITED (UK holding company)
4. **UK**: HERTZ (U.K.) LIMITED (operating company)

The **Netherlands B.V.** is owned by the **USA Inc.**, but the B.V. directly holds the shares in the UK company.

## Regulatory Requirements

### Companies House Rules
1. **CS01 (Confirmation Statement)**: Must list **direct shareholders** who own shares
2. **PSC Register**: Must identify **ultimate beneficial owners** with ≥25% control

### KYC/AML Screening
Both sources are important for screening:
- **Direct shareholders**: Immediate corporate entities with shareholding
- **PSCs**: Ultimate beneficial owners who control the company

## Data Consistency

### Structure Chart Shows:
- ✅ HERTZ HOLDINGS NETHERLANDS 2 B.V. (100% direct shareholder from CS01)

### Consolidated Screening List Shows:
- ✅ HERTZ HOLDINGS NETHERLANDS 2 B.V. (direct shareholder, now included after fix)
- ✅ Hertz Global Holdings Inc. (ultimate beneficial owner from PSC register)
- ✅ Hertz Global Holdings, Inc. (duplicate PSC entry)

### Why Both Are Correct
- **Netherlands B.V.**: Direct shareholder on share register
- **USA Inc.**: Ultimate owner controlling the Netherlands B.V.
- Both require KYC/AML screening under UK regulations

## Common Patterns

This structure is typical for:
1. **Car rental companies**: Hertz, Avis, Enterprise
2. **Hotel chains**: Marriott, Hilton, IHG
3. **Retail chains**: Walmart, Tesco, Carrefour
4. **Banks**: HSBC, Barclays, Deutsche Bank

Large companies use **multiple layers** of holding companies across different countries.

## Technical Implementation

### Before Fixes (Issues)
1. ❌ "2 HERTZ LTD" shown instead of "HERTZ HOLDINGS NETHERLANDS 2 B.V."
2. ❌ "?" shown instead of Netherlands flag 🇳🇱
3. ❌ Foreign company missing from Consolidated Screening List

### After Fixes (Resolved)
1. ✅ Detects foreign companies by suffix (B.V., Inc., etc.)
2. ✅ Skips Companies House search for foreign entities
3. ✅ Preserves original foreign company names from CS01
4. ✅ Adds country detection (B.V. → Netherlands, Inc. → USA)
5. ✅ Displays country flags in structure chart (🇳🇱, 🇺🇸, 🇬🇧)
6. ✅ Includes foreign companies in screening list
7. ✅ PSCs added to screening list (existing functionality)

## Verification

### Test with HERTZ (U.K.) LIMITED (00597994):

**Structure Chart Should Show**:
```
HERTZ (U.K.) LIMITED 🇬🇧
  └─ HERTZ HOLDINGS III UK LIMITED 🇬🇧 (100%)
      └─ HERTZ HOLDINGS NETHERLANDS 2 B.V. 🇳🇱 (100%)
```

**Consolidated Screening List Should Include**:
- HERTZ (U.K.) LIMITED (target company)
- HERTZ HOLDINGS III UK LIMITED (direct parent)
- HERTZ HOLDINGS NETHERLANDS 2 B.V. (grandparent, direct shareholder)
- Hertz Global Holdings Inc. (ultimate beneficial owner, PSC)
- Hertz Global Holdings, Inc. (duplicate PSC entry)

## Related Documentation

- `FOREIGN_COMPANY_FIX.md`: Technical details of foreign company detection
- `BUG_INVESTIGATION_SUMMARY.md`: Investigation process and root cause analysis
- `NAME_NORMALIZATION_FIX.md`: Roman numeral OCR correction

## Summary

**Q: Why is "Hertz Global Holdings Inc." in the Screening List but not the Structure Chart?**

**A**: Because they represent different data sources:
- **Structure Chart** = Direct shareholders from CS01 (HERTZ HOLDINGS NETHERLANDS 2 B.V.)
- **Screening List** = All entities requiring screening, including:
  - Direct shareholders from CS01
  - Ultimate beneficial owners from PSC register (Hertz Global Holdings Inc.)

Both are correct and necessary for comprehensive KYC/AML compliance.

The Netherlands B.V. **directly holds the shares** (shown in structure), while the USA Inc. **ultimately controls** the B.V. (shown in PSC register).
