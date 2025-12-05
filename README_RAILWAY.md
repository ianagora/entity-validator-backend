# Entity Validator Backend - Railway Production Version

## Overview

This is the **Python FastAPI backend** for the Entity Validation & Enrichment Platform, optimized for Railway deployment.

## Architecture

```
┌──────────────────────────────────────┐
│  Cloudflare Pages (Frontend)        │
│  - User Interface                   │
│  - File Upload                      │
│  - Dashboard                        │
└────────────┬─────────────────────────┘
             │
             │ HTTPS + Bearer Token Auth
             │
┌────────────▼─────────────────────────┐
│  Railway (This Backend)              │
│  ┌────────────────────────────────┐  │
│  │ FastAPI Application (4 workers)│  │
│  ├────────────────────────────────┤  │
│  │ Background Enrichment Workers  │  │
│  ├────────────────────────────────┤  │
│  │ Redis Job Queue (Upstash)      │  │
│  ├────────────────────────────────┤  │
│  │ PostgreSQL DB (Supabase)       │  │
│  └────────────────────────────────┘  │
└──────────────────────────────────────┘
             │
             ├──→ Companies House API
             ├──→ Charity Commission API
             └──→ OpenAI GPT-4o API
```

## Features

### Core Entity Resolution
- ✅ UK Companies House entity matching
- ✅ Charity Commission entity matching
- ✅ Fuzzy name matching with confidence scores
- ✅ Auto vs Manual routing logic

### Data Enrichment
- ✅ Company profiles (status, incorporation, address, SIC codes)
- ✅ Officers and directors
- ✅ Persons with Significant Control (PSCs)
- ✅ Charges and mortgages
- ✅ Filing history
- ✅ Charity trustees and financial data

### AI-Powered Shareholder Extraction
- ✅ PDF download from Companies House (CS01, AR01, IN01 filings)
- ✅ OCR processing with Tesseract
- ✅ GPT-4o/GPT-4o-mini for shareholder data extraction
- ✅ Parent company detection and recursive extraction
- ✅ Intelligent fallback (CS01 → AR01 → IN01)

### Background Processing
- ✅ Redis-based job queue
- ✅ Multi-threaded enrichment workers
- ✅ Real-time progress tracking
- ✅ Automatic retry on failures

### Production Features
- ✅ Bearer token API authentication
- ✅ PostgreSQL support (via Supabase)
- ✅ SQLite support (development/testing)
- ✅ Health check endpoints
- ✅ Structured logging
- ✅ Error handling and recovery

## API Endpoints

### Authentication Required (Bearer Token)

All endpoints require `Authorization: Bearer <BACKEND_API_KEY>` header.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/batch-upload` | POST | Upload Excel/CSV batch file |
| `/api/batches` | GET | List all batches |
| `/api/batch/:id/status` | GET | Get batch status and progress |
| `/api/batch/:id/items` | GET | Get all items in a batch |
| `/api/company/:number/shareholders` | GET | Extract shareholder info |
| `/api/company/:number/filing-history` | GET | Get company filing history |

### Public Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check (includes DB and Redis status) |

## Environment Variables

### Required

```env
# API Keys
CH_API_KEY=your-companies-house-api-key        # From https://developer.company-information.service.gov.uk
OPENAI_API_KEY=sk-proj-...                      # From https://platform.openai.com
BACKEND_API_KEY=generate-random-32-char-string  # Your secure API key

# Database (Supabase)
DATABASE_URL=postgresql://user:pass@host:5432/db

# Redis (Upstash)
REDIS_URL=redis://default:pass@host:6379
```

### Optional

```env
# App Configuration
PORT=8000                           # Server port (Railway sets automatically)
WORKERS=4                           # Number of Uvicorn workers
ENVIRONMENT=production              # production | development
LOG_LEVEL=INFO                      # DEBUG | INFO | WARNING | ERROR

# Cache & Performance
CACHE_TTL_SECONDS=86400            # API response cache duration (24h default)
REQUEST_TIMEOUT_SECONDS=15          # External API timeout
MAX_RETRIES=3                       # API retry attempts

# File Storage (optional - for S3)
AWS_ACCESS_KEY_ID=your-key
AWS_SECRET_ACCESS_KEY=your-secret
S3_BUCKET_NAME=entity-validator-files
```

## Development

### Local Setup

```bash
cd /home/user/entity-validator-backend

# Install dependencies
pip install -r requirements.txt

# Install Tesseract OCR (required for shareholder extraction)
# Ubuntu/Debian:
sudo apt-get install tesseract-ocr

# macOS:
brew install tesseract

# Windows:
# Download from: https://github.com/UB-Mannheim/tesseract/wiki

# Create .env file
cat > .env << 'EOF'
CH_API_KEY=your-key-here
OPENAI_API_KEY=your-key-here
BACKEND_API_KEY=dev-key-12345
DATABASE_URL=sqlite:///entity_workflow.db
ENVIRONMENT=development
EOF

# Initialize database
python -c "from database_config import init_db; init_db()"

# Run development server
uvicorn app:app --reload --host 0.0.0.0 --port 8000
```

### Testing

```bash
# Health check
curl http://localhost:8000/health

# Test with authentication
curl -H "Authorization: Bearer dev-key-12345" \
     http://localhost:8000/api/batches

# Upload test batch
curl -X POST \
     -H "Authorization: Bearer dev-key-12345" \
     -F "file=@test_batch.xlsx" \
     http://localhost:8000/batch-upload
```

## Deployment to Railway

See [DEPLOYMENT.md](./DEPLOYMENT.md) for complete step-by-step guide.

**Quick version**:

1. Push code to GitHub
2. Create Railway project from GitHub repo
3. Add PostgreSQL (Supabase) and Redis (Upstash)
4. Set environment variables
5. Railway auto-deploys!

## Database Schema

### Tables

**batches**:
- id (primary key)
- filename
- upload_path
- status (pending, running, done, failed)
- total_items
- processed_items
- created_at, updated_at

**items**:
- id (primary key)
- batch_id (foreign key)
- input_name
- entity_name
- company_number, charity_number
- resolved_registry
- pipeline_status, enrich_status
- match_type, confidence, reason
- resolved_data (JSON)
- enriched_data (JSON)
- shareholders_json (JSON)
- shareholders_status
- created_at, updated_at

**reviews**:
- id (primary key)
- item_id (foreign key)
- status
- l1/l2/l3 review fields
- QC fields
- created_at, updated_at

**users**:
- id (primary key)
- username, email
- role
- created_at

## Performance Optimization

### Current Throughput

With default configuration (4 workers):
- **10-20 entities/minute** (with shareholder extraction)
- **40-80 entities/minute** (without shareholder extraction)

### Scaling Options

**Vertical Scaling** (increase Railway instance size):
```
2GB RAM, 2 vCPU  → ~15 entities/min
4GB RAM, 4 vCPU  → ~30 entities/min
8GB RAM, 8 vCPU  → ~60 entities/min
```

**Horizontal Scaling** (multiple Railway instances):
- Use shared PostgreSQL + Redis
- Add more worker instances
- Load balancer distributes work

**Code Optimizations**:
1. Switch to GPT-4o-mini (80% faster, 80% cheaper)
2. Cache Companies House API responses (Redis)
3. Batch API calls where possible
4. Optimize SQL queries with indexes

## Cost Breakdown

### Infrastructure (Railway + External Services)

| Service | Configuration | Monthly Cost |
|---------|--------------|--------------|
| Railway Pro Plan | Base fee | $20 |
| Railway Instance | 2GB RAM, 2 vCPU @ 24/7 | $60-80 |
| Supabase PostgreSQL | Free tier (500MB) | $0 |
| Upstash Redis | Free tier (10K cmds/day) | $0 |
| **Total Infrastructure** | | **$80-100/month** |

### API Costs (50,000 entities/month)

| Service | Usage | Cost |
|---------|-------|------|
| Companies House API | Unlimited free | $0 |
| Charity Commission API | Unlimited free | $0 |
| OpenAI GPT-4o | 50K calls @ $0.01-0.03 | $500-1,500 |
| OpenAI GPT-4o-mini | 50K calls @ $0.003-0.006 | $150-300 |

**Total: $230-1,600/month** (depending on GPT model choice)

**Recommended**: Use GPT-4o-mini for production → **~$250-400/month total**

## Monitoring

### Railway Dashboard

- Real-time logs
- CPU/Memory metrics
- Request counts
- Error rates

### Health Checks

```bash
# Basic health
curl https://your-backend.railway.app/health

# Returns:
{
  "status": "ok",
  "database": "connected",
  "redis": "connected",
  "timestamp": "2024-12-05T13:49:00Z"
}
```

### Alerts

Set up in Railway:
- Budget alerts (>$100/month)
- Error rate alerts
- CPU/Memory alerts

## Troubleshooting

### Common Issues

**Issue**: Workers not processing batches
- Check Redis connection in `/health`
- Verify REDIS_URL environment variable
- Check Railway logs for errors

**Issue**: Shareholder extraction failing
- Verify OPENAI_API_KEY is set
- Check OpenAI account has credits
- Review logs for specific API errors

**Issue**: Database connection errors
- Verify DATABASE_URL format
- Check Supabase project is active
- Test connection from Railway console

## Security

- ✅ API key authentication on all endpoints
- ✅ No sensitive data in logs
- ✅ PostgreSQL SSL connections
- ✅ Environment variables for secrets
- ✅ Input validation and sanitization
- ⚠️ Add rate limiting (recommended)
- ⚠️ Add CORS restrictions (recommended)

## Support

- **Railway**: https://docs.railway.app
- **FastAPI**: https://fastapi.tiangolo.com
- **Companies House API**: https://developer.company-information.service.gov.uk
- **OpenAI API**: https://platform.openai.com/docs

---

**Ready for production deployment** 🚀
