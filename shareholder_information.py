#!/usr/bin/env python3
"""
Test script for CS01 PDF retrieval functionality
"""

from resolver import get_cs01_filings_for_company, get_ar01_filings_for_company, get_in01_filings_for_company, get_document_metadata, download_cs01_pdf, download_ar01_pdf, download_in01_pdf
import os
import pdfplumber
import re
import json
from openai import OpenAI
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()
try:
    import pytesseract
    from PIL import Image
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

def extract_text_with_ocr(pdf_path):
    """Extract text from PDF using OCR"""
    full_text = ""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            # Convert page to image
            page_image = page.to_image(resolution=300).original

            # Convert to PIL Image if needed
            if not isinstance(page_image, Image.Image):
                page_image = Image.fromarray(page_image)

            # Extract text with OCR
            text = pytesseract.image_to_string(page_image)
            if text:
                full_text += text + "\n"

    return full_text

def extract_shareholder_info_with_openai(pdf_path):
    """Extract shareholder information from CS01 PDF - Tesseract first, OpenAI fallback"""
    full_text = ""
    extraction_method = "unknown"
    
    # PRIORITY 1: Try Tesseract OCR first (more accurate, no hallucinations)
    if OCR_AVAILABLE:
        try:
            print("   Attempting Tesseract OCR extraction (primary method)...")
            full_text = extract_text_with_ocr(pdf_path)
            if full_text.strip():
                extraction_method = "tesseract_ocr"
                print(f"   ✅ Tesseract OCR successful: {len(full_text)} characters extracted")
        except Exception as e:
            print(f"   ⚠️ Tesseract OCR failed: {e}")
    else:
        print("   ⚠️ Tesseract OCR not available (pytesseract not installed)")
    
    # PRIORITY 2: If OCR failed or unavailable, try pdfplumber text extraction
    if not full_text.strip():
        print("   Attempting pdfplumber text extraction (fallback method)...")
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    full_text += text + "\n"
        
        if full_text.strip():
            extraction_method = "pdfplumber"
            print(f"   ✅ pdfplumber extraction successful: {len(full_text)} characters extracted")
        else:
            print("   ⚠️ pdfplumber extraction failed: no text found")

    if not full_text.strip():
        print("   ❌ No text extracted from PDF (both OCR and pdfplumber failed)")
        return []
    
    print(f"   Using extraction method: {extraction_method}")
    print(f"   DEBUG: Extracted text preview (first 500 chars):\n{full_text[:500]}\n")
    print(f"   DEBUG: Extracted text preview (last 500 chars):\n{full_text[-500:]}\n")

    # Initialize OpenAI client with timeout
    try:
        client = OpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            timeout=60.0  # 60 second timeout for API calls
        )
    except Exception as e:
        print(f"   OpenAI client initialization failed: {e}")
        print("   Please ensure OPENAI_API_KEY is set in the .env file")
        return []

    prompt = f"""
You are an expert at extracting shareholder information from UK company filings (CS01 forms).

Please analyze the following text from a CS01 PDF and extract all shareholder information. Return ONLY a valid JSON object with the following structure:

{{
  "shareholders": [
    {{
      "name": "FULL SHAREHOLDER NAME",
      "shares_held": NUMBER_OF_SHARES,
      "share_class": "SHARE_CLASS_TYPE",
      "transfers": [
        {{ "amount": TRANSFER_AMOUNT, "date": "YYYY-MM-DD" }}
      ]
    }}
  ]
}}

CRITICAL Rules:
- Extract ALL shareholders mentioned in the document
- For the "name" field: Extract ONLY the text that appears after "Name:" in each shareholding section
- DO NOT include trust names, settlement names, or discretionary trust references in the "name" field
- Trust references like "RE W C ROSE DISCRETIONARY TRUST" or "RE. WC ROSE SETTLEMENT" should be IGNORED
- The shareholder name is the legal entity that holds the shares, not the trust they represent
- Example: If you see "Name: S W J ROSE" followed by "S W ROSE RE W C ROSE DISCRETIONARY TRUST", extract only "S W J ROSE"
- Example: If you see "Name: GREENE & GREENE TRUSTEES LIMITED" followed by "SWJ ROSE RE. WC ROSE SETTLEMENT", extract only "GREENE & GREENE TRUSTEES LIMITED"

IMPORTANT - Multiple Shareholders Per Shareholding:
- Sometimes a SINGLE shareholding line lists MULTIPLE separate shareholders separated by commas or ampersands
- Example: "Name: ANDREW P COOPER LIMITED, WAYNE PERRIN LIMITED, STUART D HUGHES LIMITED & JONATHAN MATHERS LIMITED"
- These are SEPARATE shareholders who should be extracted as INDIVIDUAL entries
- When splitting, you MUST preserve the exact company names (including "LIMITED", "LTD", "PLC", etc.)
- For shares_held: The total shares for that shareholding apply to ALL shareholders listed together
- When there's no way to determine individual shareholdings, use the TOTAL shares for EACH shareholder
- This allows downstream processing to identify and enrich each company separately
- Look for separators: commas (,), ampersands (&), and "AND"
- Common pattern: "COMPANY A, COMPANY B, COMPANY C & COMPANY D" should become 4 separate shareholder entries
- Each entry should have: same shares_held value, same share_class, but different name

Other Rules:
- For transfers array: include any transfer information found (amount and date), or leave as empty array [] if no transfers mentioned
- shares_held should be a number (integer) - this is the number of shares held AS AT THE DATE OF THIS CONFIRMATION STATEMENT
- If shareholding shows "0 ORDINARY shares held as at the date of this confirmation statement", set shares_held to 0
- share_class is typically "ORDINARY" but could be other types
- If no shareholders are found, return {{"shareholders": []}}
- Make sure names are properly capitalized and complete
- Look for sections like "Full details of Shareholders" or similar

Text from PDF:
{full_text}
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a precise data extraction assistant. Return only valid JSON."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            max_tokens=2000
        )

        result_text = response.choices[0].message.content.strip()

        # Clean up the response (remove markdown code blocks if present)
        if result_text.startswith("```json"):
            result_text = result_text[7:]
        if result_text.startswith("```"):
            result_text = result_text[3:]
        if result_text.endswith("```"):
            result_text = result_text[:-3]

        result_text = result_text.strip()

        # Parse the JSON
        result = json.loads(result_text)
        print(f"   Raw JSON response: {json.dumps(result, indent=2)}")
        
        shareholders_found = result.get("shareholders", [])
        if not shareholders_found:
            print(f"   ⚠️ WARNING: OpenAI returned empty shareholders list despite {len(full_text)} chars of text")
            print(f"   This usually means:")
            print(f"     - CS01 filing has 'no updates' (no shareholder changes)")
            print(f"     - Text quality is poor (check DEBUG output above)")
            print(f"     - Shareholder info is in a different section or format")
        
        return shareholders_found

    except TimeoutError as e:
        print(f"   ⚠️ OpenAI API timeout after 60 seconds: {e}")
        print(f"   Skipping this filing and trying next one...")
        return []
    except Exception as e:
        print(f"   Error extracting with OpenAI: {e}")
        return []

def process_filing_type(company_number, filing_type):
    """Process a specific filing type and return filing status and shareholder data"""
    shareholders = []
    filing_found = False

    try:
        if filing_type == "CS01":
            print(f"Getting CS01 filings...")
            filings = get_cs01_filings_for_company(company_number)
            download_func = download_cs01_pdf
        elif filing_type == "AR01":
            print(f"Getting AR01 filings...")
            filings = get_ar01_filings_for_company(company_number)
            download_func = download_ar01_pdf
        elif filing_type == "IN01":
            print(f"Getting IN01 filings...")
            filings = get_in01_filings_for_company(company_number)
            download_func = download_in01_pdf
        else:
            print(f"Unknown filing type: {filing_type}")
            return False, []

        print(f"   Found {len(filings)} {filing_type} filings")

        if filings:
            filing_found = True
            shareholders = []

            # Limit filings to check, but use a higher limit for finding corporate shareholders
            # The optimization (commit 22ec091) sorts "with updates" first, so we're more likely to find meaningful data early
            # However, for corporate shareholders (holdings companies), we may need to check older filings (e.g., 2017)
            MAX_FILINGS_TO_CHECK = 7  # Balanced: enough to find older holdings companies, but not too slow
            filings_to_process = filings[:MAX_FILINGS_TO_CHECK]
            
            # 🐛 DEBUG: Log filings being checked to verify sorting
            print(f"   📋 DEBUG: First {min(len(filings), MAX_FILINGS_TO_CHECK)} filings after sorting:")
            for idx, f in enumerate(filings[:MAX_FILINGS_TO_CHECK]):
                desc = f.get('description', 'NO DESCRIPTION')
                date = f.get('date', 'NO DATE')
                print(f"      {idx+1}. {date} - {desc}")
            if len(filings) > MAX_FILINGS_TO_CHECK:
                print(f"   ⚠️ {len(filings) - MAX_FILINGS_TO_CHECK} older filings NOT checked:")
                for idx, f in enumerate(filings[MAX_FILINGS_TO_CHECK:]):
                    desc = f.get('description', 'NO DESCRIPTION')
                    date = f.get('date', 'NO DATE')
                    print(f"      SKIPPED {idx+MAX_FILINGS_TO_CHECK+1}. {date} - {desc}")
            
            if len(filings) > MAX_FILINGS_TO_CHECK:
                print(f"   Found {len(filings)} filings, limiting to {MAX_FILINGS_TO_CHECK} most recent (prioritized by 'with updates' first)")

            # Process filings in order (most recent first) until we find shareholders
            # IMPORTANT: We prefer filings with corporate/parent shareholders over individual shareholders
            # This ensures we find holdings companies even if the most recent filing shows individuals
            has_parent_shareholders = False
            individual_shareholders_backup = None  # Store individual shareholders as fallback
            
            for i, filing in enumerate(filings_to_process):
                # If we already found shareholders with parent companies, we're done
                if has_parent_shareholders:
                    break
                
                # If we found individual shareholders but no parent companies, keep looking
                # (corporate shareholders are more useful for ownership trees)

                doc_id = filing.get('document_id')
                filing_date = filing.get('date', 'unknown')

                if doc_id:
                    print(f"   Processing {filing_type} filing {i+1}/{len(filings_to_process)} ({filing_date}): {doc_id}")

                    # Get document metadata
                    try:
                        metadata = get_document_metadata(doc_id)
                        print(f"   Document size: {metadata.get('document_metadata', {}).get('size', 'unknown')} bytes")
                    except Exception as e:
                        print(f"   Warning: Could not get metadata: {e}")

                    # Download PDF
                    print(f"   Downloading {filing_type} PDF...")
                    try:
                        pdf_content = download_func(doc_id)
                        print(f"   Successfully downloaded {len(pdf_content)} bytes")

                        # Save PDF to file
                        os.makedirs('shareholder_information_pdfs', exist_ok=True)
                        pdf_filename = f"{filing_type}_{company_number}_{doc_id}.pdf"
                        pdf_path = os.path.join('shareholder_information_pdfs', pdf_filename)
                        with open(pdf_path, 'wb') as f:
                            f.write(pdf_content)
                        print(f"   PDF saved to: {pdf_path}")

                        # Extract shareholder information using OpenAI
                        print(f"   Extracting shareholder information using OpenAI GPT-4o...")
                        extracted_shareholders = extract_shareholder_info_with_openai(pdf_path)

                        if extracted_shareholders:
                            print(f"   ✅ Successfully extracted {len(extracted_shareholders)} shareholders from {filing_type} ({filing_date})")
                            
                            # DEBUG: Log ALL extracted shareholders BEFORE filtering
                            print(f"   📋 DEBUG - RAW EXTRACTION from {filing_type} ({filing_date}, doc_id: {doc_id}):")
                            for idx, sh in enumerate(extracted_shareholders, 1):
                                sh_name = sh.get('name', 'N/A')
                                sh_shares = sh.get('shares_held', 'N/A')
                                sh_class = sh.get('share_class', 'N/A')
                                print(f"      {idx}. {sh_name} - {sh_shares} shares ({sh_class})")
                            
                            # Check if we found parent/corporate shareholders (companies, not individuals)
                            # Look for company suffixes like LIMITED, LTD, PLC, LLP, etc.
                            parent_suffixes = ['limited', 'ltd', 'holdings', 'plc', 'llp', 'lp', 'trust']
                            has_corporate = False
                            corporate_names = []
                            for sh in extracted_shareholders:
                                name = sh.get('name', '').lower()
                                if any(suffix in name for suffix in parent_suffixes):
                                    has_corporate = True
                                    corporate_names.append(sh.get('name', 'N/A'))
                            
                            if has_corporate:
                                # Found corporate shareholders - use these and stop
                                shareholders = extracted_shareholders
                                has_parent_shareholders = True
                                print(f"   🏢 Found corporate shareholders: {', '.join(corporate_names)}")
                                print(f"   🛑 STOP: Will use this filing ({filing_type} {filing_date}) and stop processing")
                                break  # Early exit - no need to check more filings
                            else:
                                # Only individuals - save as backup but keep looking
                                if not individual_shareholders_backup:
                                    individual_shareholders_backup = extracted_shareholders
                                    print(f"   ⚠️ Only individual shareholders found - saving as backup, will check older filings for corporate shareholders...")
                                else:
                                    print(f"   ⚠️ Only individual shareholders found - continuing to check older filings...")
                        else:
                            print(f"   No shareholders found in this {filing_type} filing, trying next one...")

                    except Exception as e:
                        print(f"   Error processing {filing_type} document {doc_id}: {e}")
                        continue  # Try next filing
                else:
                    print(f"   No document ID found for {filing_type} filing {i+1}, skipping...")
                    continue

            # If no corporate shareholders found, use individual shareholders as fallback
            if not shareholders and individual_shareholders_backup:
                shareholders = individual_shareholders_backup
                print(f"   ℹ️ No corporate shareholders found in any filing, using individual shareholders as fallback")
            
            if not shareholders:
                print(f"   No shareholders found in the {len(filings_to_process)} {filing_type} filings checked (out of {len(filings)} total)")
        else:
            print(f"   No {filing_type} filings found for this company")
            filing_found = False

    except Exception as e:
        print(f"Error processing {filing_type}: {e}")
        filing_found = False

    return filing_found, shareholders

def calculate_shareholder_percentages(shareholders):
    """Calculate percentage ownership for each shareholder"""
    # Calculate total shares
    total_shares = 0
    for shareholder in shareholders:
        try:
            shares_held = shareholder.get('shares_held', 0)
            if isinstance(shares_held, str):
                # Remove commas and convert to int
                shares_held = int(shares_held.replace(',', ''))
            total_shares += int(shares_held)
        except (ValueError, TypeError):
            continue

    # Filter out shareholders with 0 shares and calculate percentages
    filtered_shareholders = []
    for shareholder in shareholders:
        try:
            shares_held = shareholder.get('shares_held', 0)
            if isinstance(shares_held, str):
                shares_held = int(shares_held.replace(',', ''))
            
            # CRITICAL FIX: Skip shareholders with 0 shares
            if int(shares_held) == 0:
                print(f"  ⚠️ FILTERING OUT 0-share shareholder: {shareholder.get('name', 'N/A')}")
                continue

            if total_shares > 0:
                percentage = (int(shares_held) / total_shares) * 100
                shareholder['percentage'] = round(percentage, 2)
            else:
                shareholder['percentage'] = 0.0
            
            filtered_shareholders.append(shareholder)
        except (ValueError, TypeError):
            shareholder['percentage'] = 0.0
            filtered_shareholders.append(shareholder)

    return filtered_shareholders, total_shares

def identify_parent_companies(shareholders):
    """Identify shareholders that are parent companies and separate them"""
    parent_shareholders = []
    regular_shareholders = []

    parent_suffixes = ['limited', 'ltd', 'trust', 'plc', 'llp', 'lp']

    for shareholder in shareholders:
        name = shareholder.get('name', '').lower().strip()
        is_parent = any(name.endswith(' ' + suffix) for suffix in parent_suffixes)

        if is_parent:
            parent_shareholders.append(shareholder)
        else:
            regular_shareholders.append(shareholder)

    return regular_shareholders, parent_shareholders


def extract_shareholders_for_company(company_number):
    """Main function to extract shareholders using intelligent CS01 -> AR01 fallback"""
    print(f"Extracting shareholder information for company {company_number}")
    print("=" * 70)

    status = {
        "regular_shareholders": [],
        "parent_shareholders": [],
        "total_shares": 0,
        "extraction_status": "",
        "cs01_found": False,
        "cs01_has_shareholders": False,
        "ar01_found": False,
        "ar01_has_shareholders": False,
        "in01_found": False,
        "in01_has_shareholders": False
    }

    # Step 1: Try CS01 first
    print("Step 1: Attempting CS01 extraction...")
    cs01_found, cs01_shareholders = process_filing_type(company_number, "CS01")
    status["cs01_found"] = cs01_found
    status["cs01_has_shareholders"] = len(cs01_shareholders) > 0

    if cs01_found and cs01_shareholders:
        print("\n✅ SUCCESS: Shareholder information found in CS01")
        print(f"📊 DEBUG - CS01 shareholders BEFORE filtering (count: {len(cs01_shareholders)}):")
        for idx, sh in enumerate(cs01_shareholders, 1):
            print(f"   {idx}. {sh.get('name', 'N/A')} - {sh.get('shares_held', 'N/A')} shares")
        
        # Process shareholders: calculate percentages and separate into regular vs parent
        shareholders_with_percentages, total_shares = calculate_shareholder_percentages(cs01_shareholders)
        
        print(f"📊 DEBUG - CS01 shareholders AFTER 0-share filtering (count: {len(shareholders_with_percentages)}):")
        for idx, sh in enumerate(shareholders_with_percentages, 1):
            print(f"   {idx}. {sh.get('name', 'N/A')} - {sh.get('shares_held', 'N/A')} shares ({sh.get('percentage', 0)}%)")
        
        regular_shareholders, parent_shareholders = identify_parent_companies(shareholders_with_percentages)

        status["regular_shareholders"] = regular_shareholders
        status["parent_shareholders"] = parent_shareholders
        status["total_shares"] = total_shares
        status["extraction_status"] = "found"

        if parent_shareholders:
            print(f"Identified {len(parent_shareholders)} parent company shareholders")

        return status

    # Step 2: If CS01 failed or no shareholders, try AR01
    print("\n" + "=" * 70)
    print("Step 2: CS01 failed or no shareholders found, trying AR01...")
    ar01_found, ar01_shareholders = process_filing_type(company_number, "AR01")
    status["ar01_found"] = ar01_found
    status["ar01_has_shareholders"] = len(ar01_shareholders) > 0

    if ar01_found and ar01_shareholders:
        print("\n✅ SUCCESS: Shareholder information found in AR01")
        # Process shareholders: calculate percentages and separate into regular vs parent
        shareholders_with_percentages, total_shares = calculate_shareholder_percentages(ar01_shareholders)
        regular_shareholders, parent_shareholders = identify_parent_companies(shareholders_with_percentages)

        status["regular_shareholders"] = regular_shareholders
        status["parent_shareholders"] = parent_shareholders
        status["total_shares"] = total_shares
        status["extraction_status"] = "found"

        if parent_shareholders:
            print(f"Identified {len(parent_shareholders)} parent company shareholders")

        return status

    # Step 3: If AR01 failed or no shareholders, try IN01
    print("\n" + "=" * 70)
    print("Step 3: AR01 failed or no shareholders found, trying IN01...")
    in01_found, in01_shareholders = process_filing_type(company_number, "IN01")
    status["in01_found"] = in01_found
    status["in01_has_shareholders"] = len(in01_shareholders) > 0

    if in01_found and in01_shareholders:
        print("\n✅ SUCCESS: Shareholder information found in IN01")
        # Process shareholders: calculate percentages and separate into regular vs parent
        shareholders_with_percentages, total_shares = calculate_shareholder_percentages(in01_shareholders)
        regular_shareholders, parent_shareholders = identify_parent_companies(shareholders_with_percentages)

        status["regular_shareholders"] = regular_shareholders
        status["parent_shareholders"] = parent_shareholders
        status["total_shares"] = total_shares
        status["extraction_status"] = "found"

        if parent_shareholders:
            print(f"Identified {len(parent_shareholders)} parent company shareholders")

        return status

    # All three failed - determine the specific failure reason
    print("\n❌ FAILURE: No shareholder information found in CS01, AR01, or IN01")

    if status["cs01_found"] and not status["cs01_has_shareholders"]:
        if status["ar01_found"] and not status["ar01_has_shareholders"]:
            if status["in01_found"] and not status["in01_has_shareholders"]:
                status["extraction_status"] = "cs01_ar01_in01_found_no_shareholders"
            elif not status["in01_found"]:
                status["extraction_status"] = "cs01_ar01_found_no_shareholders_in01_not_found"
            else:
                status["extraction_status"] = "cs01_ar01_found_no_shareholders_in01_unknown"
        elif not status["ar01_found"]:
            if status["in01_found"] and not status["in01_has_shareholders"]:
                status["extraction_status"] = "cs01_found_no_shareholders_ar01_in01_found_no_shareholders"
            elif not status["in01_found"]:
                status["extraction_status"] = "cs01_found_no_shareholders_no_ar01_or_in01_filings"
            else:
                status["extraction_status"] = "cs01_found_no_shareholders_ar01_not_found_in01_unknown"
    elif not status["cs01_found"]:
        if status["ar01_found"] and not status["ar01_has_shareholders"]:
            if status["in01_found"] and not status["in01_has_shareholders"]:
                status["extraction_status"] = "cs01_not_found_ar01_in01_found_no_shareholders"
            elif not status["in01_found"]:
                status["extraction_status"] = "cs01_not_found_ar01_found_no_shareholders_in01_not_found"
            else:
                status["extraction_status"] = "cs01_not_found_ar01_found_no_shareholders_in01_unknown"
        elif not status["ar01_found"]:
            if status["in01_found"] and not status["in01_has_shareholders"]:
                status["extraction_status"] = "no_cs01_or_ar01_filings_in01_found_no_shareholders"
            elif not status["in01_found"]:
                status["extraction_status"] = "no_cs01_ar01_or_in01_filings"
            else:
                status["extraction_status"] = "no_cs01_or_ar01_filings_in01_unknown"
    else:
        status["extraction_status"] = "unknown_failure"

    return status

def test_shareholder_extraction():
    # INPUT: Company number - CHANGE THIS VALUE as needed
    company_number = '16386380'

    try:
        result = extract_shareholders_for_company(company_number)

        print("\n" + "=" * 70)
        print("FINAL RESULTS:")
        print("=" * 70)
        print(f"Company: {company_number}")
        print(f"Extraction Status: {result.get('extraction_status', 'unknown')}")
        print(f"CS01 Found: {result.get('cs01_found', False)}, Has Shareholders: {result.get('cs01_has_shareholders', False)}")
        print(f"AR01 Found: {result.get('ar01_found', False)}, Has Shareholders: {result.get('ar01_has_shareholders', False)}")
        print(f"IN01 Found: {result.get('in01_found', False)}, Has Shareholders: {result.get('in01_has_shareholders', False)}")

        all_shareholders = result.get('regular_shareholders', []) + result.get('parent_shareholders', [])

        if all_shareholders:
            print(f"Total shareholders found: {len(all_shareholders)}")
            print(f"Total shares: {result.get('total_shares', 0)}")
            print("\nShareholder Details:")

            for i, shareholder in enumerate(all_shareholders, 1):
                print(f"\n{i}. Name: {shareholder.get('name', 'Unknown')}")
                print(f"   Shares Held: {shareholder.get('shares_held', 'Unknown')}")
                print(f"   Percentage: {shareholder.get('percentage', 'Unknown')}%")
                print(f"   Share Class: {shareholder.get('share_class', 'Unknown')}")
                transfers = shareholder.get('transfers', [])
                if transfers:
                    transfer_strs = [f"{t.get('amount', 0)} shares on {t.get('date', 'unknown')}" for t in transfers]
                    print(f"   Transfers: {', '.join(transfer_strs)}")
                else:
                    print("   Transfers: None")
        else:
            print("No shareholder information found")

    except Exception as e:
        print(f"Error in shareholder extraction: {e}")

def test_company_filings():
    """Test function to check what filings a company has"""
    from resolver import get_company_filing_history

    company_number = '16386380'
    result = get_company_filing_history(company_number)

    print(f"Company: {company_number}")
    print(f"Total filings: {len(result.get('filing_history', {}).get('items', []))}")

    items = result.get('filing_history', {}).get('items', [])
    for item in items[:20]:  # Show first 20 filings
        print(f"  {item.get('type', 'Unknown')}: {item.get('date', 'Unknown')} - {item.get('description', 'No description')}")

if __name__ == "__main__":
    # test_shareholder_extraction()
    test_company_filings()
