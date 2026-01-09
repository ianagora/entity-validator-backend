"""
Diagnostic script to check Companies House numbers in screening output
"""
import sys
import json

# Expected correct CH numbers from structure chart
CORRECT_CH_NUMBERS = {
    "Amey Limited": "01074442",
    "Enterprise Limited": "02444040",
    "United Kenning Rental Group Limited": "02942541",
    "HERTZ (U.K.) LIMITED": "02928091",
    "Hertz Holdings Iii Uk Limited": "08045035"
}

def diagnose_screening_data(screening_json_path):
    """Load screening JSON and check for CH number issues"""
    with open(screening_json_path, 'r') as f:
        data = json.load(f)
    
    print("=" * 80)
    print("COMPANIES HOUSE NUMBER DIAGNOSTIC")
    print("=" * 80)
    
    # Check each entity in ownership_chain
    ownership_chain = data.get("ownership_chain", [])
    
    # Group entries by entity name
    from collections import defaultdict
    by_entity = defaultdict(list)
    
    for entry in ownership_chain:
        name = entry.get("name", "Unknown")
        by_entity[name].append(entry)
    
    # Check problem entities
    issues_found = []
    
    for entity_name, expected_ch in CORRECT_CH_NUMBERS.items():
        print(f"\n{'=' * 80}")
        print(f"Checking: {entity_name}")
        print(f"Expected CH: {expected_ch}")
        print(f"{'=' * 80}")
        
        # Find all entries for this entity
        entity_entries = by_entity.get(entity_name, [])
        
        if not entity_entries:
            print(f"⚠️  Entity '{entity_name}' not found in screening data")
            continue
        
        for entry in entity_entries:
            role = entry.get("role", "Unknown")
            actual_ch = entry.get("company_number", "N/A")
            category = entry.get("category", "Unknown")
            
            status = "✅" if actual_ch == expected_ch else "❌"
            
            print(f"{status} Role: {role}")
            print(f"   Category: {category}")
            print(f"   Actual CH: {actual_ch}")
            print(f"   Expected CH: {expected_ch}")
            
            if actual_ch != expected_ch and actual_ch != "N/A":
                issues_found.append({
                    "entity": entity_name,
                    "role": role,
                    "expected": expected_ch,
                    "actual": actual_ch,
                    "category": category
                })
    
    # Check for duplicate CH numbers assigned to different entities
    print(f"\n{'=' * 80}")
    print("CHECKING FOR DUPLICATE CH NUMBERS")
    print(f"{'=' * 80}")
    
    ch_to_entities = defaultdict(set)
    for entry in ownership_chain:
        ch = entry.get("company_number")
        name = entry.get("name")
        if ch and name and entry.get("is_company"):  # Only check company entries
            ch_to_entities[ch].add(name)
    
    for ch, entity_names in ch_to_entities.items():
        if len(entity_names) > 1:
            print(f"❌ CH {ch} assigned to multiple entities:")
            for name in entity_names:
                print(f"   - {name}")
            
            # Check if any of these are our problem entities
            for name in entity_names:
                if name in CORRECT_CH_NUMBERS:
                    issues_found.append({
                        "entity": name,
                        "issue": "Duplicate CH number",
                        "ch": ch,
                        "shared_with": list(entity_names - {name})
                    })
    
    # Summary
    print(f"\n{'=' * 80}")
    print("SUMMARY")
    print(f"{'=' * 80}")
    if issues_found:
        print(f"❌ Found {len(issues_found)} issue(s):")
        for i, issue in enumerate(issues_found, 1):
            print(f"\n{i}. {issue}")
    else:
        print("✅ All Companies House numbers are correct!")
    
    return issues_found

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python diagnose_ch_numbers.py <screening_json_path>")
        print("\nExample:")
        print("  python diagnose_ch_numbers.py screening_output.json")
        sys.exit(1)
    
    screening_path = sys.argv[1]
    issues = diagnose_screening_data(screening_path)
    sys.exit(0 if not issues else 1)
