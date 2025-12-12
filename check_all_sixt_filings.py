#!/usr/bin/env python3
"""
Check all CS01 filings for SIXT RENT A CAR LIMITED to find one with shareholder data.
"""

from resolver import get_cs01_filings_for_company, download_cs01_pdf
from shareholder_information import extract_text_with_ocr
import re
import tempfile
import os

def check_filing_has_shareholders(document_id, filing_date):
    """Check if a filing has shareholder information"""
    
    # Download PDF
    pdf_content = download_cs01_pdf(document_id)
    if not pdf_content:
        return False, "Download failed"
    
    # Save to temp file
    temp_fd, pdf_path = tempfile.mkstemp(suffix='.pdf')
    os.write(temp_fd, pdf_content)
    os.close(temp_fd)
    
    # Extract OCR text
    ocr_text = extract_text_with_ocr(pdf_path)
    
    # Cleanup
    os.unlink(pdf_path)
    
    if not ocr_text:
        return False, "OCR failed"
    
    # Check for shareholder sections
    has_shareholding = bool(re.search(r'Shareholding\s+\d+:', ocr_text, re.IGNORECASE))
    has_shares_held = bool(re.search(r'shares\s+held', ocr_text, re.IGNORECASE))
    
    # Count patterns
    shareholding_count = len(re.findall(r'Shareholding\s+\d+:', ocr_text, re.IGNORECASE))
    
    if has_shareholding and has_shares_held:
        return True, f"Found {shareholding_count} shareholding entries"
    else:
        return False, f"No shareholder data (shares_held={has_shares_held}, shareholding={has_shareholding})"

def main():
    company_number = "00440897"
    company_name = "SIXT RENT A CAR LIMITED"
    
    print(f"\n{'='*80}")
    print(f"🔍 Checking all CS01 filings for {company_name} ({company_number})")
    print(f"{'='*80}\n")
    
    # Get all CS01 filings
    filings = get_cs01_filings_for_company(company_number)
    
    if not filings:
        print(f"❌ No CS01 filings found")
        return
    
    print(f"✅ Found {len(filings)} CS01 filings\n")
    
    # Check each filing
    for i, filing in enumerate(filings, 1):
        filing_date = filing.get('date', 'unknown')
        document_id = filing.get('document_id', '')
        
        if not document_id:
            print(f"{i}. {filing_date}: ❌ No document ID")
            continue
        
        print(f"{i}. {filing_date}: Checking...", end=" ")
        
        has_data, reason = check_filing_has_shareholders(document_id, filing_date)
        
        if has_data:
            print(f"✅ {reason}")
        else:
            print(f"❌ {reason}")
    
    print(f"\n{'='*80}")
    print(f"💡 INSIGHT")
    print(f"{'='*80}\n")
    print("If ALL filings show 'No shareholder data', this means:")
    print("1. SIXT RENT A CAR LIMITED files CS01s without shareholder updates")
    print("2. Shareholder information must be obtained from:")
    print("   - AR01 (Annual Returns) - filed before October 2016")
    print("   - IN01 (Incorporation documents) - original company setup")
    print("   - SH01 (Share allotment returns) - when shares are issued")
    print()

if __name__ == "__main__":
    main()
