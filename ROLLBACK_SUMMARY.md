# Rollback Summary - Entity Validator Backend

## Date: 2025-12-12 18:30 UTC

## What Happened
The system was rolled back from commit `dccd852` to commit `2148664` due to broken matching logic introduced in recent commits.

## Version Restored
**v2.2-SCREENING-DEDUP (commit 2148664)**
- Last known working version
- Screening log normalization working correctly
- Company matching logic intact

## Commits Rolled Back (and Why)

### 1. Commit `4d2afe4` - "FIX: Reject poor company name matches"
**Why it broke:** 
- Added overly strict matching logic to corporate_structure.py
- Rejected valid company matches
- Prevented legitimate shareholder enrichment

### 2. Commit `9f012d5` - "FIX: Reject company type mismatch (SIXT SE vs SIXT PLC)"
**Why it broke:**
- Added company type validation that was too aggressive
- Blocked valid matches where company types differ slightly
- Example: Prevented matching companies with different legal structures

### 3. Commits `d5886b9`, `18854bc`, `dccd852` - OCR Debug Logging
**Why rolled back:**
- These were diagnostic commits to debug SIXT extraction
- Added extensive OCR logging that wasn't needed in production
- The root issue (SIXT CS01 has no shareholder data) was identified
- Keeping these commits would add unnecessary logging overhead

## Key Features Retained in v2.2-SCREENING-DEDUP

✅ **Screening List Deduplication**
- Uses `canonicalise_name()` to normalize company names
- Prevents duplicates like "United Kenning Limited" and "UNITED KENNING LTD"
- Ensures clean, deduplicated screening lists

✅ **PSC Enrichment**
- Extracts Date of Birth and Nationality from PSC register
- Improves screening data for UBOs and individual shareholders

✅ **Officer Caching**
- Caches officers/PSCs during enrichment
- Fast, complete screening list generation

✅ **Resignation Filtering**
- Skips resigned officers in governance_and_control list

## What Was Removed (and Good Riddance)

❌ **Overly Strict Name Matching**
- Removed fuzzy matching score requirements
- Removed company type mismatch rejections
- Restored original matching logic that worked

❌ **Excessive Debug Logging**
- Removed OCR text samples in production logs
- Removed extraction debug counters
- Cleaner production logs

## Testing Required

After Railway deployment completes, verify:

1. **UNITED KENNING RENTAL GROUP LIMITED** enrichment works
2. **Shareholder extraction** for CS01 filings works
3. **Screening list** is properly deduplicated
4. **Ownership trees** build correctly
5. **Company matching** accepts valid matches

## Next Steps

1. **Wait 2-3 minutes** for Railway deployment to complete
2. **Re-upload test entities** to verify functionality
3. **Monitor logs** for any errors
4. If issues persist, investigate root cause without breaking matching logic

## Lessons Learned

1. **Don't add strict validation without testing on real data**
2. **Company matching is complex** - be cautious with rejection logic
3. **Keep production logs clean** - debug logging should be temporary
4. **Test rollbacks early** when changes break core functionality
5. **Document what worked** before attempting "fixes"

## Current Deployment Status

- **Commit**: `64f7eb5` (CLEANUP: Remove temporary diagnostic files)
- **Base Version**: `2148664` (v2.2-SCREENING-DEDUP)
- **Status**: ✅ Deployed to Railway
- **Expected Behavior**: Working shareholder enrichment and screening list generation

---

**IMPORTANT:** This version is KNOWN TO WORK. Any future changes to matching logic must be tested thoroughly before deployment.
