#!/usr/bin/env python3
"""
Debug script to analyze HERTZ (U.K.) LIMITED CS01 format
"""
import os
from resolver import get_cs01_filings_for_company, download_cs01_pdf
from shareholder_information import extract_text_with_ocr

# Set env var for API key (should already be in environment)
print("=" * 80)
print("HERTZ (U.K.) LIMITED - CS01 Format Analysis")
print("=" * 80)

company_number = "00597994"  # Correct company number
company_name = "HERTZ (U.K.) LIMITED"

print(f"\n1. Fetching CS01 filings for {company_name} ({company_number})...")
cs01_filings = get_cs01_filings_for_company(company_number)

if not cs01_filings:
    print("  ❌ No CS01 filings found")
    exit(1)

print(f"  ✅ Found {len(cs01_filings)} CS01 filing(s)")

# Analyze the most recent filing
filing = cs01_filings[0]
print(f"\n2. Analyzing most recent CS01 filing:")
print(f"   Date: {filing['date']}")
print(f"   Document ID: {filing['document_id']}")

# Download and extract text
print(f"\n3. Downloading CS01 PDF...")
pdf_content = download_cs01_pdf(filing['document_id'])
print(f"   ✅ Downloaded {len(pdf_content)} bytes")

# Save to temp file
import tempfile
with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
    tmp.write(pdf_content)
    tmp_path = tmp.name

print(f"\n4. Extracting text with OCR...")
ocr_text = extract_text_with_ocr(tmp_path)
print(f"   ✅ Extracted {len(ocr_text)} characters")

# Cleanup
import os
os.unlink(tmp_path)

# Analyze content
print(f"\n5. Analyzing OCR text structure:")
print(f"   - Contains 'Shareholding'? {('Shareholding' in ocr_text or 'shareholding' in ocr_text)}")
print(f"   - Contains 'Name field'? {'Name field' in ocr_text}")
print(f"   - Contains 'Share class'? {'Share class' in ocr_text}")
print(f"   - Contains 'HERTZ'? {'HERTZ' in ocr_text}")
print(f"   - Contains 'HOLDINGS'? {'HOLDINGS' in ocr_text}")

# Check for "no changes" indicators
no_changes_indicators = [
    'no changes',
    'no change',
    'not applicable',
    'n/a',
    'unchanged',
    'as before'
]

print(f"\n6. Checking for 'no changes' indicators:")
for indicator in no_changes_indicators:
    if indicator.lower() in ocr_text.lower():
        print(f"   ⚠️  Found: '{indicator}'")

# Show sample of OCR text
print(f"\n7. Sample OCR text (first 1500 characters):")
print("=" * 80)
print(ocr_text[:1500])
print("=" * 80)

# Search for shareholder patterns
print(f"\n8. Searching for shareholder patterns:")
import re

# Pattern 1: Standard CS01 format
pattern1 = r'Shareholding\s+\d+:\s*(\d+).*?Name:\s*([^\n]+)'
matches1 = re.findall(pattern1, ocr_text, re.IGNORECASE | re.DOTALL)
print(f"   - Standard format (Shareholding N: ... Name:): {len(matches1)} matches")
if matches1:
    for shares, name in matches1[:3]:
        print(f"     • {name.strip()} ({shares} shares)")

# Pattern 2: Alternative format
pattern2 = r'Name[:\s]+([^\n]+)\s+.*?(\d+)\s+shares'
matches2 = re.findall(pattern2, ocr_text, re.IGNORECASE | re.DOTALL)
print(f"   - Alternative format (Name: ... N shares): {len(matches2)} matches")
if matches2:
    for name, shares in matches2[:3]:
        print(f"     • {name.strip()} ({shares} shares)")

print(f"\n9. RECOMMENDED NEXT STEPS:")
if not matches1 and not matches2:
    print("   ❌ No standard shareholder patterns found")
    print("   → This is likely a 'no updates' filing OR shareholders are in a different format")
    print("   → Check the OCR text above to see actual structure")
else:
    print(f"   ✅ Found {len(matches1) + len(matches2)} potential shareholder entries")
    print("   → OpenAI/regex should be able to extract these")
    print("   → If extraction still fails, check OpenAI prompt or regex patterns")

print("\n" + "=" * 80)
