"""
Corporate Structure Builder
Recursively builds multi-layer ownership trees by looking up corporate shareholders
"""

import re
from typing import Dict, List, Optional, Any
from resolver import resolve_company, search_companies_house
from shareholder_information import extract_shareholders_for_company

def is_company_name(name: str) -> bool:
    """
    Determine if a shareholder name is a company (not an individual)
    """
    if not name:
        return False
    
    name_lower = name.lower().strip()
    
    # Company suffixes
    company_suffixes = [
        'limited', 'ltd', 'ltd.', 
        'plc', 'p.l.c.', 'public limited company',
        'llp', 'l.l.p.', 'limited liability partnership',
        'lp', 'l.p.', 'limited partnership',
        'corporation', 'corp', 'corp.',
        'incorporated', 'inc', 'inc.',
        'company', 'co', 'co.',
        'holdings', 'holding',
        'group',
        'trust',
        'partnership',
        'partners',
        'investments',
        'capital',
        'ventures',
        'fund',
        'estate'
    ]
    
    # Check for company suffixes
    for suffix in company_suffixes:
        # Match suffix at end of name (with optional punctuation)
        pattern = r'\b' + re.escape(suffix) + r'\.?\s*$'
        if re.search(pattern, name_lower):
            return True
    
    # Check for corporate indicators in the name
    corporate_indicators = [
        'holdings', 'holding', 'group', 'trust', 'investment', 
        'ventures', 'capital', 'fund', 'partners', 'partnership'
    ]
    
    for indicator in corporate_indicators:
        if indicator in name_lower:
            return True
    
    return False


def search_company_by_name(company_name: str) -> Optional[Dict[str, Any]]:
    """
    Search Companies House for a company by name
    Returns company number if found
    """
    try:
        print(f"  🔍 Searching Companies House for: {company_name}", flush=True)
        results = search_companies_house(company_name)
        
        print(f"  📊 Search returned {len(results) if results else 0} results", flush=True)
        
        if not results or len(results) == 0:
            print(f"  ❌ No results found for: {company_name}", flush=True)
            return None
        
        # Get the best match (first result, usually most relevant)
        best_match = results[0]
        company_number = best_match.get('company_number')
        company_name_found = best_match.get('title', '')
        company_status = best_match.get('company_status', '')
        
        print(f"  ✅ Found: {company_name_found} ({company_number}) - {company_status}", flush=True)
        
        return {
            'company_number': company_number,
            'company_name': company_name_found,
            'company_status': company_status,
            'match_quality': 'exact' if company_name.lower() == company_name_found.lower() else 'partial'
        }
        
    except Exception as e:
        import traceback
        print(f"  ⚠️  Error searching for {company_name}: {e}", flush=True)
        print(f"  Traceback: {traceback.format_exc()}", flush=True)
        return None


def build_ownership_tree(
    company_number: str, 
    company_name: str,
    depth: int = 0, 
    max_depth: int = 50,  # Effectively unlimited (circular refs prevented by visited set)
    visited: Optional[set] = None,
    initial_shareholders: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """
    Recursively build corporate ownership tree until reaching end of chain
    
    Args:
        company_number: Companies House number
        company_name: Company name (for display)
        depth: Current depth in tree (0 = root)
        max_depth: Maximum depth to recurse (default 50 = effectively unlimited, circular refs handled by visited set)
        visited: Set of already visited company numbers (prevent circular references)
        initial_shareholders: Pre-extracted shareholders for root company (avoids re-extraction)
    
    Returns:
        Dictionary with company info and nested shareholder tree
    """
    if visited is None:
        visited = set()
    
    # Prevent circular references
    if company_number in visited:
        print(f"{'  ' * depth}⚠️  Circular reference detected: {company_name} ({company_number})")
        return {
            'company_number': company_number,
            'company_name': company_name,
            'circular_reference': True,
            'shareholders': []
        }
    
    # Prevent infinite recursion
    if depth >= max_depth:
        print(f"{'  ' * depth}⏹️  Max depth ({max_depth}) reached for: {company_name}")
        return {
            'company_number': company_number,
            'company_name': company_name,
            'max_depth_reached': True,
            'shareholders': []
        }
    
    visited.add(company_number)
    
    indent = '  ' * depth
    print(f"\n{indent}{'='*60}")
    print(f"{indent}🏢 Processing: {company_name} ({company_number}) [Depth: {depth}]")
    print(f"{indent}{'='*60}")
    
    # Extract shareholders for this company (or use pre-extracted for root)
    shareholder_result = {}  # Initialize to prevent variable scope errors
    
    try:
        if depth == 0 and initial_shareholders is not None:
            # Use pre-extracted shareholders for root company (from PSC or filings)
            all_shareholders = initial_shareholders
            extraction_status = 'pre-extracted'
            shareholder_result = {
                'total_shares': 0,  # Total shares unknown when using PSC data
                'extraction_status': 'pre-extracted'
            }
            print(f"{indent}📊 Using pre-extracted shareholders (from PSC or filings)")
            print(f"{indent}👥 Total shareholders: {len(all_shareholders)}")
            print(f"{indent}DEBUG: initial_shareholders = {initial_shareholders}")
            print(f"{indent}DEBUG: all_shareholders = {all_shareholders}")
        else:
            # Extract shareholders normally for child companies
            shareholder_result = extract_shareholders_for_company(company_number)
            regular_shareholders = shareholder_result.get('regular_shareholders', [])
            parent_shareholders = shareholder_result.get('parent_shareholders', [])
            all_shareholders = regular_shareholders + parent_shareholders
            extraction_status = shareholder_result.get('extraction_status', 'unknown')
            
            print(f"{indent}📊 Extraction status: {extraction_status}")
            print(f"{indent}👥 Total shareholders: {len(all_shareholders)}")
            print(f"{indent}   - Regular: {len(regular_shareholders)}")
            print(f"{indent}   - Corporate: {len(parent_shareholders)}")
        
        # Process each shareholder
        processed_shareholders = []
        
        for shareholder in all_shareholders:
            shareholder_name = shareholder.get('name', 'Unknown')
            shares_held = shareholder.get('shares_held', 0)
            percentage = shareholder.get('percentage', 0.0)
            
            shareholder_info = {
                'name': shareholder_name,
                'shares_held': shares_held,
                'percentage': percentage,
                'share_class': shareholder.get('share_class', ''),
                'is_company': is_company_name(shareholder_name),
                'children': []
            }
            
            print(f"{indent}  └─ {shareholder_name} ({percentage}% - {shares_held} shares)")
            
            # If this shareholder is a company, recursively look it up
            if shareholder_info['is_company']:
                print(f"{indent}     🏢 Corporate shareholder detected")
                
                # Search for this company
                company_search = search_company_by_name(shareholder_name)
                
                if company_search:
                    child_company_number = company_search['company_number']
                    child_company_name = company_search['company_name']
                    
                    shareholder_info['company_number'] = child_company_number
                    shareholder_info['company_status'] = company_search.get('company_status', '')
                    
                    # Recursively get shareholders of this company
                    print(f"{indent}     🔄 Recursing into: {child_company_name}")
                    child_tree = build_ownership_tree(
                        child_company_number,
                        child_company_name,
                        depth + 1,
                        max_depth,
                        visited
                    )
                    
                    shareholder_info['children'] = child_tree.get('shareholders', [])
                    shareholder_info['child_company'] = {
                        'company_number': child_company_number,
                        'company_name': child_company_name,
                        'company_status': company_search.get('company_status', '')
                    }
                else:
                    print(f"{indent}     ⚠️  Could not find company in Companies House")
                    shareholder_info['search_failed'] = True
            else:
                print(f"{indent}     👤 Individual shareholder")
            
            processed_shareholders.append(shareholder_info)
        
        return {
            'company_number': company_number,
            'company_name': company_name,
            'depth': depth,
            'extraction_status': extraction_status,
            'total_shares': shareholder_result.get('total_shares', 0),
            'shareholders': processed_shareholders
        }
        
    except Exception as e:
        print(f"{indent}❌ Error extracting shareholders: {e}")
        return {
            'company_number': company_number,
            'company_name': company_name,
            'depth': depth,
            'error': str(e),
            'shareholders': []
        }


def flatten_ownership_tree(tree: Dict[str, Any], result: Optional[List] = None, parent_chain: Optional[List] = None) -> List[Dict[str, Any]]:
    """
    Flatten the ownership tree into a list for easier display
    Each entry shows the full ownership chain
    """
    if result is None:
        result = []
    if parent_chain is None:
        parent_chain = []
    
    shareholders = tree.get('shareholders', [])
    
    for shareholder in shareholders:
        chain = parent_chain + [{
            'name': shareholder['name'],
            'percentage': shareholder.get('percentage', 0),
            'shares_held': shareholder.get('shares_held', 0),
            'is_company': shareholder.get('is_company', False),
            'company_number': shareholder.get('company_number')
        }]
        
        if shareholder.get('is_company') and shareholder.get('children'):
            # Recurse into children
            flatten_ownership_tree({'shareholders': shareholder['children']}, result, chain)
        else:
            # Leaf node (individual or company with no further info)
            result.append({
                'ultimate_owner': chain[-1]['name'],
                'ownership_chain': chain,
                'chain_length': len(chain),
                'total_percentage': chain[-1]['percentage']
            })
    
    return result


if __name__ == "__main__":
    # Test with BARLEYFIELDS
    company_number = "10315716"
    company_name = "BARLEYFIELDS (WEELEY) MANAGEMENT COMPANY LIMITED"
    
    print(f"Building ownership tree for: {company_name}")
    tree = build_ownership_tree(company_number, company_name, max_depth=3)
    
    print("\n" + "="*70)
    print("FLATTENED OWNERSHIP CHAINS:")
    print("="*70)
    
    flattened = flatten_ownership_tree(tree)
    for i, chain_info in enumerate(flattened, 1):
        print(f"\n{i}. {chain_info['ultimate_owner']} ({chain_info['total_percentage']}%)")
        print("   Ownership chain:")
        for level, owner in enumerate(chain_info['ownership_chain']):
            indent = "   " + ("  " * level)
            icon = "🏢" if owner['is_company'] else "👤"
            print(f"{indent}{icon} {owner['name']} ({owner['percentage']}%)")
