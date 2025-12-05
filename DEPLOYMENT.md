# Entity Validator Backend - Railway Deployment Guide

## Prerequisites

1. **Railway Account**: Sign up at https://railway.app
2. **GitHub Account**: For repository hosting
3. **API Keys**:
   - Companies House API Key
   - OpenAI API Key  
   - Backend API Key (generate a secure random string)

## Step 1: Setup External Services (Free Tiers)

### A. Supabase PostgreSQL (Free)

1. Go to https://supabase.com
2. Click "New Project"
3. Fill in details:
   - Name: entity-validator-db
   - Database Password: (generate strong password)
   - Region: (closest to you)
4. Wait for provisioning (~2 minutes)
5. Go to Project Settings → Database
6. Copy the "Connection string" (URI mode)
   - Format: `postgresql://postgres:[YOUR-PASSWORD]@[HOST]:5432/postgres`
7. Save this as `DATABASE_URL`

### B. Upstash Redis (Free)

1. Go to https://upstash.com
2. Sign up / Login
3. Click "Create Database"
4. Choose:
   - Name: entity-validator-queue
   - Type: Regional
   - Region: (same as Supabase for low latency)
5. Click "Create"
6. Copy the "UPSTASH_REDIS_REST_URL" and "UPSTASH_REDIS_REST_TOKEN"
7. Save these values

**Free Tier Limits**:
- Supabase: 500MB database, unlimited API requests
- Upstash: 10,000 commands/day (sufficient for testing)

## Step 2: Prepare GitHub Repository

### Push Backend Code

```bash
cd /home/user/entity-validator-backend

# Initialize git if not already
git init

# Create comprehensive .gitignore
cat > .gitignore << 'EOF'
# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
env/
venv/
ENV/

# Database
*.db
*.sqlite
*.sqlite3

# Logs
*.log
logs/

# Environment
.env
.env.local
.dev.vars

# IDE
.vscode/
.idea/
*.swp

# OS
.DS_Store
Thumbs.db

# Results & temp files
results/
shareholder_information_pdfs/
*.tmp
*.backup

# Test files
test_*.py
*_testing.db
EOF

# Add all files
git add .

# Commit
git commit -m "Initial commit: Entity validator backend for Railway"

# Push to GitHub (after calling setup_github_environment)
# git remote add origin https://github.com/yourusername/entity-validator-backend.git
# git push -u origin main
```

## Step 3: Deploy to Railway

### A. Create Railway Project

1. Go to https://railway.app/dashboard
2. Click "New Project"
3. Select "Deploy from GitHub repo"
4. Choose your `entity-validator-backend` repository
5. Railway will auto-detect Python and start building

### B. Configure Environment Variables

In Railway dashboard → Your Service → Variables tab, add:

```env
# API Keys
CH_API_KEY=your-companies-house-api-key
OPENAI_API_KEY=your-openai-api-key
BACKEND_API_KEY=generate-a-secure-random-string-here

# Database (from Supabase)
DATABASE_URL=postgresql://postgres:password@host:5432/postgres

# Redis (from Upstash)
REDIS_URL=redis://default:password@host:6379
UPSTASH_REDIS_REST_URL=https://your-redis.upstash.io
UPSTASH_REDIS_REST_TOKEN=your-token

# App Config
PORT=8000
ENVIRONMENT=production
LOG_LEVEL=INFO
WORKERS=4

# Optional: For file storage
# AWS_ACCESS_KEY_ID=your-aws-key
# AWS_SECRET_ACCESS_KEY=your-aws-secret
# S3_BUCKET_NAME=entity-validator-files
```

### C. Configure Service Settings

**Settings → General**:
- Service Name: `entity-validator-backend`
- Start Command: `uvicorn app:app --host 0.0.0.0 --port $PORT --workers 4`

**Settings → Deployment**:
- Auto-deploy: ON (deploy on every push to main)
- Health Check Path: `/health`
- Health Check Timeout: 30 seconds

**Settings → Networking**:
- Enable Public Networking: ON
- Copy the public URL: `https://entity-validator-backend-production.up.railway.app`

## Step 4: Database Migration

### Create Tables

Railway will create tables on first run, but you can manually run migrations:

```bash
# Using Railway CLI (install: npm install -g @railway/cli)
railway login
railway link
railway run python -c "from app import init_db; init_db()"
```

Or use the Railway console:
1. Go to your service → Settings → Console
2. Run: `python -c "from app import init_db; init_db()"`

## Step 5: Deploy Frontend to Cloudflare

Now that backend is running, deploy the frontend:

```bash
cd /home/user/webapp

# Set production secrets
npx wrangler pages secret put BACKEND_API_URL
# Enter: https://entity-validator-backend-production.up.railway.app

npx wrangler pages secret put BACKEND_API_KEY
# Enter: (same value as in Railway environment variables)

# Build and deploy
npm run build
npx wrangler pages deploy dist --project-name entity-validator
```

## Step 6: Test End-to-End

### 1. Test Backend Health

```bash
curl https://entity-validator-backend-production.up.railway.app/health
```

Expected response:
```json
{
  "status": "ok",
  "timestamp": "2024-12-05T13:49:00Z",
  "database": "connected",
  "redis": "connected"
}
```

### 2. Test Frontend

Visit: `https://entity-validator.pages.dev`

- Dashboard should load
- Upload a test Excel file (with 2-3 entities)
- Monitor batch processing
- Verify enrichment results

## Monitoring & Maintenance

### Railway Monitoring

**View Logs**:
- Railway Dashboard → Your Service → Deployments → View Logs

**View Metrics**:
- CPU usage, memory, network traffic in Railway dashboard

**Cost Monitoring**:
- Railway Dashboard → Usage → Current month spend

### Set Budget Alerts

Railway Dashboard → Project Settings → Usage Alerts:
- Set alert at $100/month
- Set hard limit at $150/month (prevents overcharges)

### Scaling Configuration

**Auto-Scaling** (Railway Pro plan):

In `railway.json`:
```json
{
  "deploy": {
    "numReplicas": 1,
    "autoscaling": {
      "minReplicas": 1,
      "maxReplicas": 4,
      "targetCPUPercent": 70,
      "targetMemoryPercent": 80
    }
  }
}
```

**Manual Scaling**:
- Railway Dashboard → Service → Settings → Resources
- Adjust vCPU and RAM as needed

## Cost Optimization Tips

1. **Use GPT-4o-mini instead of GPT-4o**:
   - Edit `shareholder_information.py`
   - Change `model="gpt-4o"` to `model="gpt-4o-mini"`
   - 80% cost savings on AI API calls

2. **Enable Railway Sleep**:
   - For development: Put workers to sleep during off-hours
   - Settings → Sleep when inactive (after 1 hour)

3. **Optimize Database Queries**:
   - Add indexes to frequently queried columns
   - Use connection pooling

4. **Cache API Responses**:
   - Use Redis for Companies House API responses (they change infrequently)

## Troubleshooting

### Issue: Workers Not Starting

**Symptom**: Batches stuck in "queued" status

**Solution**:
1. Check Railway logs for errors
2. Verify `REDIS_URL` is correct
3. Restart the service: Railway Dashboard → Service → Redeploy

### Issue: Database Connection Errors

**Symptom**: 500 errors, logs show "database connection failed"

**Solution**:
1. Verify `DATABASE_URL` format in Railway variables
2. Check Supabase project is active (not paused)
3. Test connection: `railway run python -c "import psycopg2; psycopg2.connect('$DATABASE_URL')"`

### Issue: High API Costs

**Symptom**: OpenAI charges higher than expected

**Solution**:
1. Check how many entities are being processed
2. Review `shareholder_information.py` - ensure it's not calling API multiple times per entity
3. Switch to GPT-4o-mini model
4. Implement result caching

### Issue: Slow Performance

**Symptom**: Batches take too long to process

**Solution**:
1. Increase Railway instance size (Settings → Resources)
2. Add more workers (change `--workers` in start command)
3. Scale horizontally (add more Railway instances)

## Backup & Restore

### Database Backup

```bash
# Using Railway CLI
railway run pg_dump $DATABASE_URL > backup.sql

# Or use Supabase dashboard: Database → Backups → Create backup
```

### Restore Database

```bash
railway run psql $DATABASE_URL < backup.sql
```

## Security Checklist

- [ ] All API keys stored in Railway environment variables (not in code)
- [ ] `BACKEND_API_KEY` is a strong random string (32+ characters)
- [ ] Database password is strong (16+ characters)
- [ ] Railway service is not publicly accessible without authentication
- [ ] CORS is properly configured
- [ ] Rate limiting enabled for API endpoints
- [ ] Regular backups configured

## Production Launch Checklist

- [ ] Backend deployed to Railway
- [ ] Frontend deployed to Cloudflare Pages
- [ ] Database migrated and tested
- [ ] Redis queue working
- [ ] End-to-end test passed (upload → process → download)
- [ ] Monitoring and alerts configured
- [ ] Budget limits set
- [ ] Documentation updated
- [ ] Team members have access to Railway/Cloudflare dashboards

## Support

For issues:
- Railway: https://docs.railway.app or Railway Discord
- Cloudflare: https://developers.cloudflare.com/pages
- Supabase: https://supabase.com/docs
- Upstash: https://docs.upstash.com

---

**Deployment completed!** 🎉

Your entity validation platform is now live and ready for commercial use.
