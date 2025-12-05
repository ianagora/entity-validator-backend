#!/usr/bin/env python3
"""
Script to query Companies House filing history for a specific company and category,
then save the results to a JSON file.
"""

import os
import json
import requests
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
API_BASE_URL = "http://localhost:8000"  # Adjust if your app runs on a different port/host

def main():
    # Query parameters
    company_number = "06765787"
    category = "confirmation-statement"

    print(f"Querying filing history for company {company_number}...")
    print(f"Category filter: {category}")
    print(f"API endpoint: {API_BASE_URL}/api/company/{company_number}/filing-history")

    try:
        # Make API request with query parameters
        url = f"{API_BASE_URL}/api/company/{company_number}/filing-history"
        params = {"category": category}

        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        result = response.json()

        # Save to JSON file
        filename = f"filing_history_{company_number}_{category}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        print(f"✅ Results saved to {filename}")

        # Get the filing history data
        filing_history = result.get('filing_history', {})
        print(f"Total items: {filing_history.get('total_count', 'unknown')}")

        # Print summary
        items = filing_history.get('items', [])
        if items:
            print(f"Found {len(items)} filing history items:")
            for i, item in enumerate(items[:5], 1):  # Show first 5
                print(f"  {i}. {item.get('date', 'unknown')} - {item.get('description', 'no description')}")

            if len(items) > 5:
                print(f"  ... and {len(items) - 5} more items")
        else:
            print("No filing history items found.")

    except requests.exceptions.RequestException as e:
        print(f"❌ API request failed: {e}")
        print("Make sure the FastAPI app is running with: python -m uvicorn app:app --reload")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    main()
