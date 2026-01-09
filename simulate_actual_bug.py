"""
Simulate the actual CH number bug at line 789.
"""

def build_ownership_tree_buggy(company_number, company_name, shareholders):
    """Simulates the buggy build_ownership_tree() function."""
    processed_shareholders = []
    
    for sh in shareholders:
        sh_name = sh['name']
        
        # Search Companies House (simulated)
        if sh_name == "AMEY LIMITED":
            child_company_number = "02379479"  # CORRECT CH found
            child_company_name = "AMEY LIMITED"
        else:
            child_company_number = None
            child_company_name = sh_name
        
        # Create shareholder_info
        shareholder_info = {
            'name': sh_name,
            'percentage': sh.get('percentage'),
            'is_company': True
        }
        
        if child_company_number:
            shareholder_info['company_number'] = child_company_number  # Line 711 ✅
            print(f"✅ Line 711: Set shareholder_info['company_number'] = '{child_company_number}'")
        
        processed_shareholders.append(shareholder_info)
    
    # Line 789: THE BUG!
    return {
        'company_number': company_number,  # PARENT's CH number!
        'company_name': company_name,
        'shareholders': processed_shareholders
    }

# Test
print("=" * 80)
print("SIMULATING THE BUG")
print("=" * 80)

tree = build_ownership_tree_buggy(
    company_number="06134591",  # ENTERPRISE LIMITED
    company_name="ENTERPRISE LIMITED",
    shareholders=[
        {'name': 'AMEY LIMITED', 'percentage': 100.0}
    ]
)

print("\n📦 Returned tree structure:")
print(f"tree['company_number'] = '{tree['company_number']}'  # PARENT: 06134591")
print(f"tree['shareholders'][0]['name'] = '{tree['shareholders'][0]['name']}'")
print(f"tree['shareholders'][0]['company_number'] = '{tree['shareholders'][0]['company_number']}'  # CHILD: 02379479 ✅")

print("\n✅ CONCLUSION:")
print("The tree structure is CORRECT!")
print("tree['shareholders'][0]['company_number'] correctly has '02379479'")
print()
print("🔍 So the bug must be in app.py when reading this tree!")
print("Check if app.py is accidentally using tree['company_number'] instead of shareholder['company_number']")

print("\n" + "=" * 80)
