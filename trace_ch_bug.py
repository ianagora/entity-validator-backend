"""
Trace the CH number bug from the logs.

From logs:
1. ENTERPRISE LIMITED (06134591) is being enriched
2. CS01 extraction finds: AMEY LIMITED (100%, 156000010 shares)
3. Search finds: AMEY LIMITED (02379479) [CORRECT]
4. But screenshot shows: AMEY LIMITED with CH 06134591 [WRONG]

Hypothesis: The 'company_number' parameter passed to build_ownership_tree()
is being used instead of child_company_number for shareholders.
"""

print("=" * 80)
print("TRACING CH NUMBER BUG")
print("=" * 80)

# Simulate the enrichment process from logs
print("\n1️⃣ ENRICHMENT STARTS")
print("   Target: ENTERPRISE LIMITED")
print("   Target CH: 06134591")

print("\n2️⃣ CS01 EXTRACTION")
print("   Found shareholder: AMEY LIMITED (100%)")

print("\n3️⃣ COMPANIES HOUSE SEARCH")
print("   Searching for: AMEY LIMITED")
print("   Result: AMEY LIMITED (02379479) ✅ CORRECT")

print("\n4️⃣ BUILD_OWNERSHIP_TREE() CALL")
print("   Function signature:")
print("   def build_ownership_tree(company_number: str, ...) -> Dict:")
print()
print("   Called as:")
print("   build_ownership_tree(")
print("       company_number='06134591',  # ENTERPRISE LIMITED")
print("       depth=0")
print("   )")

print("\n5️⃣ INSIDE build_ownership_tree() - Processing AMEY LIMITED")
print("   Line 684-692: shareholder_info = {...}")
print("   Line 696: child_company_number = None")
print("   Line 700-708: company_search for 'AMEY LIMITED'")
print("   Line 709: child_company_number = '02379479' ✅")
print("   Line 711: shareholder_info['company_number'] = '02379479' ✅")

print("\n6️⃣ BUG HYPOTHESIS")
print("   ❓ Is there a line AFTER 711 that overwrites company_number?")
print("   ❓ Does the recursive call modify shareholder_info?")
print("   ❓ Is shareholder_info['company_number'] being read from 'company_number' parameter?")

print("\n7️⃣ CHECKING LINE 750-756 (Recursive call)")
print("   if child_company_number and not circular_ref:")
print("       shareholder_info['children'] = build_ownership_tree(")
print("           company_number=child_company_number,  # '02379479' ✅")
print("           depth=depth + 1")
print("       )")
print()
print("   ⚠️ This SHOULD be correct - passing '02379479'")

print("\n8️⃣ KEY QUESTION")
print("   After line 756, is shareholder_info['company_number'] still '02379479'?")
print("   Or has it been corrupted to '06134591'?")

print("\n9️⃣ NEXT STEP")
print("   Check lines 757-790 for any code that modifies shareholder_info['company_number']")

print("\n" + "=" * 80)
