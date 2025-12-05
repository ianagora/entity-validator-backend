# Entity Validation App - Shareholder Extraction Enhancement

This document outlines the comprehensive shareholder extraction enhancements added to the Entity Validation App.

## 🚀 Overview

The application now includes intelligent shareholder information extraction from UK company filings using AI-powered text analysis. The system automatically downloads and processes CS01 and AR01 filings, extracts shareholder data using OpenAI GPT-4o, and provides detailed status tracking.

## 📦 New Dependencies

Add the following to your `requirements.txt`:

```txt
# OpenAI API
openai==1.54.0

# PDF processing and OCR
pdfplumber==0.11.4
pytesseract==0.3.13
Pillow==10.4.0
```

### Installation

```bash
pip install openai==1.54.0 pytesseract==0.3.13 Pillow==10.4.0
```

### Tesseract OCR Setup

For Windows, download and install Tesseract OCR from:
https://github.com/UB-Mannheim/tesseract/wiki

Add Tesseract to your system PATH, or set the path in code:

```python
import pytesseract
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
```

## 🔧 Configuration

### OpenAI API Key

Add your OpenAI API key to the `.env` file:

```env
OPENAI_API_KEY=your_api_key_here
```

The application will automatically load this key and use GPT-4o for shareholder extraction.

## 🗄️ Database Schema Changes

### New Columns Added

Two new columns have been added to the `items` table:

```sql
shareholders_json TEXT      -- Stores shareholder data as JSON
shareholders_status TEXT    -- Stores extraction status
```

### Automatic Migration

The database schema automatically updates when the app starts. The migration adds the new columns if they don't exist.

## 🔍 Shareholder Extraction Logic

### Intelligent CS01 → AR01 Fallback with Historical Search

1. **Historical CS01 Search**: Checks CS01 filings from most recent to oldest until shareholder data is found
2. **OCR Processing**: Uses Tesseract OCR to extract text from scanned PDFs
3. **AI Analysis**: Sends extracted text to OpenAI GPT-4o for shareholder information extraction
4. **AR01 Fallback**: If CS01 fails or contains no shareholder data, tries AR01 (annual return) filings
5. **Percentage Calculation**: Automatically calculates ownership percentages for each shareholder
6. **Parent Company Detection**: Identifies and extracts shareholders from parent companies (Limited/Ltd/Trust/PLC/etc.)
7. **Status Tracking**: Records detailed status information for transparency

### Status Codes

The system tracks 5 possible extraction statuses:

- **`found`**: Shareholder information successfully extracted ✅
- **`cs01_found_no_shareholders`**: CS01 filing exists but contains no shareholder data
- **`cs01_not_found_ar01_no_shareholders`**: CS01 not found, AR01 found but no shareholder data
- **`no_cs01_or_ar01_filings`**: No CS01 or AR01 filings found in company records
- **`extraction_error`**: Error occurred during extraction process

### Shareholder Data Format

Extracted shareholder data follows this JSON structure:

```json
{
  "shareholders": [
    {
      "name": "Shareholder Full Name",
      "shares_held": 1000,
      "percentage": 45.5,
      "share_class": "ORDINARY",
      "transfers": [
        {
          "amount": 500,
          "date": "2024-01-15"
        }
      ]
    }
  ],
  "parent_shareholders": {
    "12345678": {
      "parent_name": "Parent Company Limited",
      "parent_company_number": "12345678",
      "shareholders": [...]
    }
  },
  "total_shares": 2200,
  "extraction_status": "found"
}
```

## 🎨 UI Enhancements

### Company Detail Pages

The auto detail pages (`/auto/{item_id}`) now include a **Shareholders** section that displays:

- **Shareholder Table**: Name, shares held, ownership percentage, share class, and transfer history
- **Total Shares**: Shows total share count at the top
- **Parent Company Sections**: Separate tables for shareholders of parent companies (Limited/Ltd/Trust/PLC)
- **Status Messages**: When no shareholders found, displays specific reasons:
  - "CS01 filing found but contains no shareholder information."
  - "CS01 filing not found. AR01 filing found but contains no shareholder information."
  - "No CS01 or AR01 filings found in company records."
  - "Error occurred during shareholder extraction."
- **Unidentified Parents**: Lists parent companies that require manual company number lookup

### Test Interface

New test page at `/shareholders` for manual testing of shareholder extraction for any company number.

## 🔌 API Enhancements

### Enhanced Shareholder Endpoint

`GET /api/company/{company_number}/shareholders`

Returns comprehensive extraction results:

```json
{
  "company_number": "08834134",
  "shareholders": [...],
  "count": 1,
  "extraction_status": "found",
  "cs01_found": true,
  "cs01_has_shareholders": false,
  "ar01_found": true,
  "ar01_has_shareholders": true
}
```

## 🛠️ Code Changes

### New Files Created

- `shareholder_information.py`: Core shareholder extraction logic
- `templates/shareholder_test.html`: Test interface template

### Modified Files

#### `app.py`

- Added shareholder extraction import
- Enhanced enrichment process with shareholder extraction
- Added database columns for shareholder storage
- Updated API endpoints
- Added shareholder test route

#### `resolver.py`

- Added `get_ar01_filings_for_company()` function
- Added `download_ar01_pdf()` function

#### `templates/auto_detail.html`

- Added shareholder display section with status messages

#### `requirements.txt`

- Added OpenAI, OCR, and image processing dependencies

## 🚀 Usage

### Automatic Processing

Shareholder extraction now runs automatically during the enrichment process for all Companies House entities. Results are stored in the database and displayed in the UI.

### Manual Testing

1. Visit `/shareholders` in the web interface
2. Enter any UK company number
3. Click "Extract Shareholders" to see real-time extraction results

### API Access

```bash
curl "http://localhost:8000/api/company/08834134/shareholders"
```

## 📊 Data Flow

```
Company Enrichment Request
        ↓
    CS01 Download & OCR Processing
        ↓ (if CS01 fails/no shareholders)
    AR01 Download & OCR Processing
        ↓
    OpenAI GPT-4o Analysis
        ↓
    Shareholder Data Extraction
        ↓
    Database Storage + UI Display
```

## 🔧 Troubleshooting

### Common Issues

1. **Tesseract not found**: Ensure Tesseract OCR is installed and in PATH
2. **OpenAI API errors**: Check API key in `.env` file and account credits
3. **Database errors**: Run database migration by restarting the app

### Debug Information

The system provides detailed logging for each extraction step, including:

- Filing discovery status
- Download success/failure
- OCR text extraction results
- OpenAI API responses
- Final extraction status

## 📈 Benefits

- **Historical Search**: Checks all CS01 filings from recent to oldest for complete coverage
- **Percentage Calculations**: Automatic ownership percentage calculations
- **Parent Company Tracing**: Recursive shareholder extraction for corporate ownership structures
- **Comprehensive Coverage**: Handles both CS01 and AR01 filings with OCR support
- **AI-Powered Accuracy**: Uses GPT-4o for intelligent text analysis from scanned documents
- **Status Transparency**: Detailed status tracking explains exactly why data is/isn't available
- **Persistent Storage**: All shareholder data survives app restarts
- **API Integration**: Full programmatic access to extraction results and status

## 🔄 Future Enhancements

- Support for additional filing types (AA01, etc.)
- Batch processing capabilities
- Enhanced OCR accuracy with preprocessing
- Integration with additional AI models
- Historical shareholder tracking over time

---

**Note**: This enhancement significantly improves the Entity Validation App's ability to provide comprehensive company information, including previously inaccessible shareholder data from official filings.
