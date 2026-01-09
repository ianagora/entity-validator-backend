"""
Diagnostic tool to check CH numbers in stored ownership trees
"""
import json
import requests
import os

# Read Railway database URL from environment
database_url = os.getenv('DATABASE_URL')
if not database_url:
    print("❌ DATABASE_URL not set. This tool requires access to the production database.")
    print("Please run this on Railway or set DATABASE_URL locally.")
    exit(1)

# API endpoint to get item details
RAILWAY_API_URL = "https://entity-validator-production.up.railway.app"

def check_entity_tree(entity_name):
    """Check ownership tree for an entity via API"""
    # Search for entity
    search_url = f"{RAILWAY_API_URL}/api/items"
    response = requests.get(search_url)
    
    if response.status_code != 200:
        print(f"❌ Failed to fetch items: {response.status_code}")
        return
    
    items = response.json()
    
    # Find the entity
    target_item = None
    for item in items:
        if entity_name.lower() in item.get('input_name', '').lower():
            target_item = item
            break
    
    if not target_item:
        print(f"❌ Entity '{entity_name}' not found")
        return
    
    item_id = target_item['id']
    print(f"✅ Found entity: {target_item['input_name']} (ID: {item_id}, CH: {target_item.get('company_number')})")
    
    # Get detailed info including ownership tree
    details_url = f"{RAILWAY_API_URL}/api/item/{item_id}/details"
    response = requests.get(details_url)
    
    if response.status_code != 200:
        print(f"❌ Failed to fetch details: {response.status_code}")
        return
    
    details = response.json()
    ownership_tree = details.get('ownership_tree')
    
    if not ownership_tree:
        print("❌ No ownership tree found")
        return
    
    print(f"\n{'='*80}")
    print("OWNERSHIP TREE ANALYSIS")
    print(f"{'='*80}")
    
    print(f"\nRoot Company:")
    print(f"  Name: {ownership_tree.get('company_name')}")
    print(f"  CH: {ownership_tree.get('company_number')}")
    
    shareholders = ownership_tree.get('shareholders', [])
    print(f"\nShareholders: {len(shareholders)}")
    
    for i, sh in enumerate(shareholders, 1):
        print(f"\n{i}. {sh.get('name')}")
        print(f"   is_company: {sh.get('is_company')}")
        print(f"   company_number: {sh.get('company_number', 'NOT SET')}")
        print(f"   percentage: {sh.get('percentage')}%")
        
        # Check if CH number matches root (BUG!)
        root_ch = ownership_tree.get('company_number')
        sh_ch = sh.get('company_number')
        
        if sh_ch and sh_ch == root_ch:
            print(f"   ❌ BUG: Shareholder has same CH as root company!")
        
        # Check officers
        officers = sh.get('officers', {}).get('items', [])
        if officers:
            print(f"   Officers: {len(officers)}")
            for off in officers[:3]:
                print(f"     - {off.get('name')}")

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python check_tree_ch_numbers.py <entity_name>")
        print("\nExample:")
        print("  python check_tree_ch_numbers.py 'Enterprise Limited'")
        sys.exit(1)
    
    entity_name = sys.argv[1]
    check_entity_tree(entity_name)
