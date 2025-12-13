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
    
    # Company suffixes (UK, US, and European)
    company_suffixes = [
        # UK suffixes
        'limited', 'ltd', 'ltd.', 
        'plc', 'p.l.c.', 'public limited company',
        'llp', 'l.l.p.', 'limited liability partnership',
        'lp', 'l.p.', 'limited partnership',
        # US suffixes
        'corporation', 'corp', 'corp.',
        'incorporated', 'inc', 'inc.',
        'company', 'co', 'co.',
        # European suffixes
        'se',  # Societas Europaea (European Company)
        's.e.',
        'sa',  # Société Anonyme (French/Spanish/Portuguese)
        's.a.',
        'sarl',  # Société à Responsabilité Limitée (French)
        's.a.r.l.',
        'gmbh',  # Gesellschaft mit beschränkter Haftung (German)
        'ag',  # Aktiengesellschaft (German/Swiss/Austrian)
        'a.g.',
        'nv',  # Naamloze Vennootschap (Dutch/Belgian)
        'n.v.',
        'bv',  # Besloten Vennootschap (Dutch)
        'b.v.',
        'spa',  # Società per Azioni (Italian)
        's.p.a.',
        'srl',  # Società a Responsabilità Limitata (Italian)
        's.r.l.',
        'ab',  # Aktiebolag (Swedish)
        'oy',  # Osakeyhtiö (Finnish)
        'as',  # Aksjeselskap (Norwegian)
        'a/s',  # Aktieselskab (Danish)
        # Generic terms
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


def get_country_from_suffix(company_name: str) -> Optional[str]:
    """
    Detect country from company legal suffix
    Returns country name if detected, None otherwise
    """
    name_upper = company_name.upper().strip()
    
    # Suffix to country mapping
    suffix_country_map = {
        # Netherlands
        'B.V.': 'NETHERLANDS',
        'N.V.': 'NETHERLANDS',
        
        # Germany
        'GMBH': 'GERMANY',
        'AG': 'GERMANY',  # Also Switzerland, Austria
        'UG': 'GERMANY',  # Unternehmergesellschaft (mini-GmbH)
        'KG': 'GERMANY',  # Kommanditgesellschaft
        
        # European Union
        'SE': 'EUROPEAN UNION',  # Societas Europaea (European Company)
        'SCE': 'EUROPEAN UNION',  # Societas Cooperativa Europaea
        
        # France
        'S.A.': 'FRANCE',  # Also Spain, Belgium, Luxembourg
        'S.A.R.L.': 'FRANCE',
        'SARL': 'FRANCE',
        'S.A.S.': 'FRANCE',  # Société par Actions Simplifiée
        'SAS': 'FRANCE',
        'S.C.A.': 'FRANCE',  # Société en Commandite par Actions
        
        # Italy
        'S.R.L.': 'ITALY',
        'SRL': 'ITALY',
        'S.P.A.': 'ITALY',
        'SPA': 'ITALY',
        
        # Spain
        'S.L.': 'SPAIN',  # Sociedad Limitada
        'S.A.': 'SPAIN',  # Sociedad Anónima (ambiguous with France)
        
        # Belgium
        'B.V.B.A.': 'BELGIUM',  # Besloten Vennootschap met Beperkte Aansprakelijkheid (old)
        'S.P.R.L.': 'BELGIUM',  # Société Privée à Responsabilité Limitée (old)
        'S.A./N.V.': 'BELGIUM',  # Bilingual
        
        # Luxembourg
        'S.À R.L.': 'LUXEMBOURG',
        'S.A.R.L.': 'LUXEMBOURG',  # Also France
        
        # Denmark / Norway
        'A.S.': 'DENMARK',  # Aktieselskab (Denmark), Aksjeselskap (Norway)
        'A/S': 'DENMARK',
        'APS': 'DENMARK',  # Anpartsselskab
        
        # Sweden
        'AB': 'SWEDEN',  # Aktiebolag
        
        # Finland
        'OY': 'FINLAND',  # Osakeyhtiö
        'OYJ': 'FINLAND',  # Julkinen Osakeyhtiö (public)
        
        # Switzerland
        'SA': 'SWITZERLAND',  # Société Anonyme
        'SARL': 'SWITZERLAND',  # Also France
        
        # Austria
        'GMBH': 'AUSTRIA',  # Also Germany
        
        # Poland
        'SP. Z O.O.': 'POLAND',  # Spółka z ograniczoną odpowiedzialnością
        'S.A.': 'POLAND',  # Also France, Spain
        
        # Czech Republic
        'S.R.O.': 'CZECH REPUBLIC',  # Společnost s ručením omezeným
        'A.S.': 'CZECH REPUBLIC',  # Also Denmark
        
        # Ireland
        'DAC': 'IRELAND',  # Designated Activity Company
        # NOTE: 'LTD' removed - too ambiguous with UK companies
        
        # USA
        'LLC': 'USA',
        'INC.': 'USA',
        'INC': 'USA',
        'CORP.': 'USA',
        'CORP': 'USA',
        'L.P.': 'USA',  # Limited Partnership
        'LP': 'USA',
        'L.L.P.': 'USA',  # Limited Liability Partnership
        'LLP': 'USA',
        
        # Canada
        'LTÉE': 'CANADA',  # Limitée (French)
        'INC.': 'CANADA',  # Also USA
        
        # Australia
        'PTY LTD': 'AUSTRALIA',
        'PTY. LTD.': 'AUSTRALIA',
        
        # New Zealand
        # NOTE: 'LIMITED' removed - too ambiguous with UK companies
        
        # Singapore
        'PTE LTD': 'SINGAPORE',
        'PTE. LTD.': 'SINGAPORE',
        
        # Hong Kong
        # NOTE: 'LIMITED' removed - too ambiguous with UK companies
        
        # Japan
        'K.K.': 'JAPAN',  # Kabushiki Kaisha
        'KK': 'JAPAN',
        'G.K.': 'JAPAN',  # Gōdō Kaisha
        
        # South Korea
        'CO., LTD.': 'SOUTH KOREA',
        
        # China
        'CO., LTD.': 'CHINA',
        
        # India
        'PVT LTD': 'INDIA',
        'PRIVATE LIMITED': 'INDIA',
        
        # UAE
        'L.L.C.': 'UAE',
        'LLC': 'UAE',  # Also USA (ambiguous)
        
        # South Africa
        'PTY LTD': 'SOUTH AFRICA',  # Also Australia (ambiguous)
        '(PTY) LTD': 'SOUTH AFRICA',
    }
    
    for suffix, country in suffix_country_map.items():
        if name_upper.endswith(' ' + suffix) or name_upper.endswith(suffix):
            return country
        if ' ' + suffix + ' ' in name_upper:
            return country
    
    return None


def is_foreign_company(company_name: str) -> bool:
    """
    Detect if a company is foreign (non-UK) based on legal suffixes
    Returns True if company appears to be registered outside the UK
    """
    country = get_country_from_suffix(company_name)
    if country:
        print(f"  🌍 Foreign company detected: {company_name} ({country})")
        return True
    return False


def search_company_by_name(company_name: str) -> Optional[Dict[str, Any]]:
    """
    Search Companies House for a company by name
    Returns company number if found, or None for foreign companies
    """
    # CRITICAL: Check if this is a foreign company first
    if is_foreign_company(company_name):
        print(f"  ⚠️  Foreign company detected, skipping Companies House search: {company_name}")
        return None
    
    try:
        print(f"  🔍 Searching Companies House for: {company_name}", flush=True)
        results = search_companies_house(company_name)
        
        print(f"  📊 Search returned {len(results) if results else 0} results", flush=True)
        
        if not results or len(results) == 0:
            print(f"  ❌ No results found for: {company_name}", flush=True)
            return None
        
        # Get the best match with improved logic
        # Priority: 1) Active companies, 2) Exact name match, 3) Roman numeral fix, 4) First result
        
        active_matches = [r for r in results if r.get('company_status', '').lower() == 'active']
        candidates = active_matches if active_matches else results
        
        # First try exact match (case-insensitive)
        search_lower = company_name.lower().strip()
        for candidate in candidates:
            candidate_name = candidate.get('title', '').lower().strip()
            if search_lower == candidate_name:
                print(f"  ✅ EXACT match: {candidate.get('title')} ({candidate.get('company_number')})")
                return {
                    'company_number': candidate.get('company_number'),
                    'company_name': candidate.get('title', ''),
                    'company_status': candidate.get('company_status', ''),
                    'match_quality': 'exact'
                }
        
        # Check for Roman numeral confusion (ITI vs III, IVI vs IV, etc.)
        # Common OCR errors: ITI (should be III), IVI (should be IV), I I I (should be III)
        roman_fixes = [
            (' ITI ', ' III '),  # Most common: ITI -> III
            (' IVI ', ' IV '),
            (' I I I ', ' III '),
            (' I I ', ' II '),
        ]
        
        for old_pattern, new_pattern in roman_fixes:
            if old_pattern in search_lower:
                fixed_search = search_lower.replace(old_pattern, new_pattern)
                print(f"  🔧 Trying Roman numeral fix: '{old_pattern.strip()}' -> '{new_pattern.strip()}'")
                
                for candidate in candidates:
                    candidate_name = candidate.get('title', '').lower().strip()
                    if fixed_search == candidate_name:
                        print(f"  ✅ ROMAN NUMERAL match: {candidate.get('title')} ({candidate.get('company_number')})")
                        return {
                            'company_number': candidate.get('company_number'),
                            'company_name': candidate.get('title', ''),
                            'company_status': candidate.get('company_status', ''),
                            'match_quality': 'roman_numeral_fix'
                        }
        
        # Check if first result is similar enough to be a match
        # Use similarity threshold to avoid false matches like "PROJECT ARDENT TOPCO LIMITED" -> "ARDENT PROJECTS LIMITED"
        best_match = candidates[0]
        best_match_name = best_match.get('title', '').lower().strip()
        
        # Calculate similarity score (simple word overlap)
        search_words = set(search_lower.split())
        match_words = set(best_match_name.split())
        
        # Remove common words that don't help matching
        stop_words = {'limited', 'ltd', 'plc', 'llp', 'lp', 'holdings', 'holding', 'group', 'company', 'co'}
        search_words_filtered = search_words - stop_words
        match_words_filtered = match_words - stop_words
        
        if search_words_filtered and match_words_filtered:
            # Calculate Jaccard similarity (intersection over union)
            intersection = len(search_words_filtered & match_words_filtered)
            union = len(search_words_filtered | match_words_filtered)
            similarity = intersection / union if union > 0 else 0
            
            print(f"  📊 Similarity score: {similarity:.2f} (threshold: 0.5)")
            
            # Require at least 50% word overlap to consider it a match
            if similarity >= 0.5:
                company_number = best_match.get('company_number')
                company_name_found = best_match.get('title', '')
                company_status = best_match.get('company_status', '')
                
                print(f"  ⚠️  Using first result (sufficient similarity): {company_name_found} ({company_number})", flush=True)
                
                return {
                    'company_number': company_number,
                    'company_name': company_name_found,
                    'company_status': company_status,
                    'match_quality': 'first_result',
                    'similarity': similarity
                }
            else:
                print(f"  ❌ First result similarity too low ({similarity:.2f}), likely not a match")
                print(f"     Searched for: {company_name}")
                print(f"     Found: {best_match.get('title')}")
                print(f"     → Treating as unmatched (likely foreign or dissolved)")
                return None
        else:
            # If no meaningful words to compare, return None
            print(f"  ❌ Cannot calculate similarity, treating as unmatched")
            return None
        
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
            # First check if this is a company limited by guarantee
            from resolver import get_company_bundle
            import signal
            
            try:
                bundle = get_company_bundle(company_number)
                profile = bundle.get("profile", {})
                company_type = (profile.get("type") or "").lower()
                is_guarantee = "guarant" in company_type
            except Exception as bundle_error:
                print(f"{indent}⚠️  Failed to get company bundle: {bundle_error}")
                # Assume it's a normal company if we can't fetch profile
                bundle = {"profile": {}, "pscs": {}}
                is_guarantee = False
            
            if is_guarantee:
                # Use PSC data for companies limited by guarantee
                print(f"{indent}🏛️  Company limited by guarantee - using PSC register")
                psc_data = bundle.get("pscs", {})
                all_shareholders = []
                
                if psc_data and psc_data.get("items"):
                    print(f"{indent}🔍 Processing {len(psc_data['items'])} PSCs for guarantee company {company_number}")
                    for psc in psc_data['items']:
                        psc_name_debug = psc.get("name", "Unknown")
                        ceased_on = psc.get("ceased_on")
                        print(f"{indent}   PSC: {psc_name_debug}, ceased_on: {ceased_on}")
                        
                        # Skip ceased PSCs
                        if ceased_on:
                            print(f"{indent}⏭️  SKIPPING CEASED PSC: {psc_name_debug} (ceased: {ceased_on})")
                            continue
                        
                        print(f"{indent}✅  Adding active PSC: {psc_name_debug}")
                        psc_name = psc.get("name", "Unknown")
                        psc_kind = psc.get("kind", "")
                        natures = psc.get("natures_of_control", [])
                        
                        # Extract control percentage
                        percentage = None
                        percentage_band = None
                        
                        if any("voting-rights-75-to-100" in n for n in natures):
                            percentage_band = "75-100% (voting rights)"
                            percentage = 87.5
                        elif any("voting-rights-50-to-75" in n for n in natures):
                            percentage_band = "50-75% (voting rights)"
                            percentage = 62.5
                        elif any("voting-rights-25-to-50" in n for n in natures):
                            percentage_band = "25-50% (voting rights)"
                            percentage = 37.5
                        elif any("right-to-appoint-and-remove-directors" in n for n in natures):
                            percentage_band = "Control (directors)"
                            percentage = 100
                        elif any("significant-influence-or-control" in n for n in natures):
                            percentage_band = "Significant influence/control"
                            percentage = 0  # Unknown percentage
                        else:
                            # Default for unrecognized natures
                            percentage_band = "Other control"
                            percentage = 0
                        
                        shareholder = {
                            "name": psc_name,
                            "shares_held": "N/A (guarantee company)",
                            "percentage": percentage,
                            "percentage_band": percentage_band,
                            "share_class": "N/A",
                            "source": "PSC Register",
                            "psc_natures": natures
                        }
                        all_shareholders.append(shareholder)
                
                extraction_status = "found_via_psc_guarantee"
                regular_shareholders = all_shareholders
                parent_shareholders = []
                shareholder_result = {
                    'total_shares': 0,
                    'extraction_status': extraction_status,
                    'regular_shareholders': regular_shareholders,
                    'parent_shareholders': parent_shareholders
                }
                
                print(f"{indent}📊 Extraction status: {extraction_status}")
                print(f"{indent}👥 Total PSCs: {len(all_shareholders)}")
            else:
                # Normal company - extract from filings (v1.0 behavior: always use CS01 for accuracy)
                shareholder_result = extract_shareholders_for_company(company_number)
                regular_shareholders = shareholder_result.get('regular_shareholders', [])
                parent_shareholders = shareholder_result.get('parent_shareholders', [])
                all_shareholders = regular_shareholders + parent_shareholders
                extraction_status = shareholder_result.get('extraction_status', 'unknown')
                
                # DEBUG: Log what shareholders are being used for this company
                print(f"{indent}🔍 DEBUG - Shareholders for {company_number} ({company_name}):")
                print(f"{indent}   Regular: {len(regular_shareholders)}, Parent: {len(parent_shareholders)}")
                print(f"{indent}   Extraction status: {extraction_status}")
                if all_shareholders:
                    print(f"{indent}   📋 Final shareholders list:")
                    for idx, sh in enumerate(all_shareholders, 1):
                        print(f"{indent}      {idx}. {sh.get('name', 'N/A')} - {sh.get('shares_held', 'N/A')} shares ({sh.get('percentage', 0)}%)")
                
                # Enrich individual shareholders with DoB/nationality from PSC register
                # This is critical for UBOs who are shareholders but whose DoB comes from PSC data
                if all_shareholders and bundle and bundle.get("pscs"):
                    psc_data = bundle.get("pscs", {})
                    pscs = psc_data.get("items", [])
                    if pscs:
                        print(f"{indent}🔍 Enriching {len(all_shareholders)} shareholders with PSC data...")
                        for shareholder in all_shareholders:
                            sh_name = shareholder.get("name", "").upper().strip()
                            # Skip if shareholder is a company
                            if shareholder.get("is_company") or is_company_name(sh_name):
                                continue
                            # Skip if already has DoB
                            if shareholder.get("date_of_birth") or shareholder.get("dob"):
                                continue
                            
                            # Try to match with PSC
                            for psc in pscs:
                                psc_name_raw = psc.get("name", "").upper().strip()
                                
                                # Normalize names for matching:
                                # Remove titles (MR, MRS, MS, MISS, DR, etc.)
                                # Remove middle names by comparing first + last name only
                                titles = ['MR ', 'MRS ', 'MS ', 'MISS ', 'DR ', 'PROF ', 'SIR ', 'LADY ', 'LORD ']
                                psc_name_normalized = psc_name_raw
                                for title in titles:
                                    if psc_name_normalized.startswith(title):
                                        psc_name_normalized = psc_name_normalized[len(title):].strip()
                                        break
                                
                                # Extract first and last name from both
                                # Shareholder: "EMMA CLOVES" -> first="EMMA", last="CLOVES"
                                # PSC: "EMMA LOUISE CLOVES" -> first="EMMA", last="CLOVES"
                                sh_parts = sh_name.split()
                                psc_parts = psc_name_normalized.split()
                                
                                if len(sh_parts) >= 2 and len(psc_parts) >= 2:
                                    # Compare first name and last name
                                    sh_first = sh_parts[0]
                                    sh_last = sh_parts[-1]
                                    psc_first = psc_parts[0]
                                    psc_last = psc_parts[-1]
                                    
                                    # Match if first and last names match
                                    if sh_first == psc_first and sh_last == psc_last:
                                        # Only enrich individuals, not corporate PSCs
                                        if psc.get("kind") != "corporate-entity-person-with-significant-control":
                                            shareholder["date_of_birth"] = psc.get("date_of_birth")
                                            shareholder["nationality"] = psc.get("nationality")
                                            print(f"{indent}   ✅ Enriched '{sh_name}' with DoB from PSC '{psc_name_raw}'")
                                            break
                                
                                # Fallback: exact match or substring match
                                elif sh_name == psc_name_normalized or sh_name in psc_name_normalized:
                                    if psc.get("kind") != "corporate-entity-person-with-significant-control":
                                        shareholder["date_of_birth"] = psc.get("date_of_birth")
                                        shareholder["nationality"] = psc.get("nationality")
                                        print(f"{indent}   ✅ Enriched '{sh_name}' with DoB from PSC '{psc_name_raw}'")
                                        break
                
                # If no shareholders found in filings, fall back to PSC register
                if len(all_shareholders) == 0:
                    print(f"{indent}📊 No shareholders in filings, trying PSC fallback...")
                    psc_data = bundle.get("pscs", {})
                    if psc_data and psc_data.get("items"):
                        print(f"{indent}📊 Found {len(psc_data['items'])} PSCs for {company_number}, converting to shareholders...")
                        for psc in psc_data['items']:
                            psc_name_debug = psc.get("name", "Unknown")
                            ceased_on = psc.get("ceased_on")
                            print(f"{indent}   PSC: {psc_name_debug}, ceased_on: {ceased_on}")
                            
                            # Skip ceased PSCs
                            if ceased_on:
                                print(f"{indent}⏭️  SKIPPING CEASED PSC: {psc_name_debug} (ceased: {ceased_on})")
                                continue
                            
                            print(f"{indent}✅  Adding active PSC: {psc_name_debug}")
                            psc_name = psc.get("name", "Unknown")
                            psc_kind = psc.get("kind", "")
                            natures = psc.get("natures_of_control", [])
                            
                            # Extract control percentage
                            percentage = 50  # Default for PSCs
                            percentage_band = "PSC Register"
                            
                            if any("ownership-of-shares-75-to-100" in n for n in natures):
                                percentage = 87.5
                                percentage_band = "75-100%"
                            elif any("ownership-of-shares-50-to-75" in n for n in natures):
                                percentage = 62.5
                                percentage_band = "50-75%"
                            elif any("ownership-of-shares-25-to-50" in n for n in natures):
                                percentage = 37.5
                                percentage_band = "25-50%"
                            elif any("voting-rights-75-to-100" in n for n in natures):
                                percentage = 87.5
                                percentage_band = "75-100% (voting)"
                            elif any("right-to-appoint-and-remove-directors" in n for n in natures):
                                percentage = 100
                                percentage_band = "Control (directors)"
                            elif any("significant-influence-or-control" in n for n in natures):
                                percentage = 0
                                percentage_band = "Significant influence/control"
                            else:
                                percentage = 0
                                percentage_band = "Other control"
                            
                            shareholder = {
                                "name": psc_name,
                                "shares_held": "Unknown (PSC)",
                                "percentage": percentage,
                                "percentage_band": percentage_band,
                                "share_class": "Ordinary",
                                "source": "PSC Register",
                                "psc_natures": natures
                            }
                            all_shareholders.append(shareholder)
                        
                        regular_shareholders = all_shareholders
                        parent_shareholders = []
                        extraction_status = "found_via_psc_fallback"
                        print(f"{indent}✅ PSC fallback: {len(all_shareholders)} controllers found")
                
                print(f"{indent}📊 Extraction status: {extraction_status}")
                print(f"{indent}👥 Total shareholders: {len(all_shareholders)}")
                if len(all_shareholders) > 0:
                    print(f"{indent}   - Regular: {len(regular_shareholders)}")
                    print(f"{indent}   - Corporate: {len(parent_shareholders)}")
        
        # Process each shareholder
        processed_shareholders = []
        
        # FIX ISSUE #3: If there's only one shareholder with 75-100% band, show 100%
        single_shareholder_100 = False
        if len(all_shareholders) == 1:
            only_shareholder = all_shareholders[0]
            band = only_shareholder.get('percentage_band', '')
            if '75-100' in band or '75%-100%' in band:
                single_shareholder_100 = True
                print(f"{indent}🔍 Single shareholder with 75-100% band detected - will show as 100%")
        
        for shareholder in all_shareholders:
            shareholder_name = shareholder.get('name', 'Unknown')
            shares_held = shareholder.get('shares_held', 0)
            percentage = shareholder.get('percentage', 0.0)
            percentage_band = shareholder.get('percentage_band', '')
            
            # If single shareholder with 75-100%, override to 100%
            if single_shareholder_100:
                percentage = 100.0
                percentage_band = '100%'
            
            shareholder_info = {
                'name': shareholder_name,
                'shares_held': shares_held,
                'percentage': percentage,
                'percentage_band': percentage_band,
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
                    
                    # CRITICAL FIX: Use official Companies House name instead of OCR'd CS01 name
                    # This ensures "HERTZ HOLDINGS ITI UK LIMITED" (OCR error) becomes "HERTZ HOLDINGS III UK LIMITED" (correct)
                    shareholder_info['name'] = child_company_name
                    
                    shareholder_info['company_number'] = child_company_number
                    shareholder_info['company_status'] = company_search.get('company_status', '')
                    shareholder_info['country'] = 'UNITED KINGDOM'  # Companies House = UK companies
                    
                    # CACHE officers and PSCs for this corporate shareholder
                    # This allows build_screening_list() to use cached data instead of making API calls
                    print(f"{indent}     📦 Caching officers/PSCs for screening list...")
                    try:
                        from resolver import get_company_bundle
                        entity_bundle = get_company_bundle(child_company_number)
                        shareholder_info['officers'] = entity_bundle.get('officers', {})
                        shareholder_info['pscs'] = entity_bundle.get('pscs', {})
                        shareholder_info['profile'] = entity_bundle.get('profile', {})
                        print(f"{indent}        ✅ Cached {len(entity_bundle.get('officers', {}).get('items', []))} officers, {len(entity_bundle.get('pscs', {}).get('items', []))} PSCs")
                    except Exception as cache_error:
                        print(f"{indent}        ⚠️  Failed to cache officers/PSCs: {cache_error}")
                        shareholder_info['officers'] = {}
                        shareholder_info['pscs'] = {}
                        shareholder_info['profile'] = {}
                    
                    # CRITICAL CHECK: Stop recursion if this is a PLC (publicly traded company)
                    # PLCs don't disclose individual shareholders, so no point recursing further
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
                        # Recursively get shareholders of this company
                        print(f"{indent}     🔄 Recursing into: {child_company_name}")
                        try:
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
                        except Exception as recursion_error:
                            # If recursion fails, still add the company but without children
                            print(f"{indent}     ⚠️  Recursion failed for {child_company_name}: {recursion_error}")
                            shareholder_info['children'] = []
                            shareholder_info['recursion_error'] = str(recursion_error)
                            shareholder_info['child_company'] = {
                                'company_number': child_company_number,
                                'company_name': child_company_name,
                                'company_status': company_search.get('company_status', '')
                            }
                else:
                    print(f"{indent}     ⚠️  Could not find company in Companies House")
                    shareholder_info['search_failed'] = True
                    
                    # If foreign company, add country information
                    country = get_country_from_suffix(shareholder_name)
                    if country:
                        shareholder_info['country'] = country
                        print(f"{indent}     🌍 Country detected: {country}")
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
