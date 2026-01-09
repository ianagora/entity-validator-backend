"""
New hypothesis: The screenshot shows AMEY LIMITED as a SEPARATE UPLOADED ENTITY,
not as a shareholder of Enterprise Limited.

If AMEY LIMITED was uploaded separately and resolved to the wrong company
during the upload/resolution phase, then:
1. The items table would have AMEY LIMITED with CH: 06134691 (wrong)
2. When enriching AMEY LIMITED, it would build a tree using CH: 06134591
3. The screening list would show CH: 06134691

This would explain why:
- AMEY LIMITED shows CH: 06134691
- Directors are linked to "ENTERPRISE LIMITED" (because target_company_number = 06134691)
"""

print("=" * 80)
print("HYPOTHESIS: Separate Entity Upload Issue")
print("=" * 80)

print("\n📊 Scenario:")
print("1. User uploads entity 'Amey Limited'")
print("2. During resolution phase (batch_resolver.py):")
print("   resolve_company('Amey Limited') incorrectly returns:")
print("   {")
print("     'company_number': '06134591',  # ENTERPRISE LIMITED (WRONG!)")
print("     'entity_name': 'ENTERPRISE LIMITED'")
print("   }")
print()
print("3. This wrong CH number is stored in items table:")
print("   items.company_number = '06134591'")
print()
print("4. When building screening list for 'Amey Limited' entity:")
print("   target_company_number = '06134591'  # From items table")
print()
print("5. All directors/officers of Amey Limited get:")
print("   officer['company_number'] = '06134591'")
print("   officer['linked_entity'] = 'ENTERPRISE LIMITED'")

print("\n✅ This matches the screenshot exactly!")
print()
print("🔍 To confirm:")
print("Check if 'Amey Limited' exists as a separate uploaded entity")
print("with company_number = '06134591' in the items table")

print("\n📝 Solution:")
print("1. Delete the incorrectly resolved 'Amey Limited' entity")
print("2. Re-upload with correct CH number: 02379479")
print("3. Or: Manually update items.company_number for that entity")

print("\n" + "=" * 80)
