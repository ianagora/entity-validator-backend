# CRITICAL: Database Persistence Issue on Railway

## Problem
The SQLite database (`entity_workflow.db`) is stored in the local filesystem, which is **ephemeral on Railway**. This means:

1. ✅ During enrichment, SVG paths ARE saved to the database
2. ✅ SVG files ARE created in `svg_exports/`
3. ❌ **BUT**: Every Railway deployment wipes both the database AND svg_exports directory
4. ❌ Result: `svg_path` field is always NULL in the database for users

## Evidence
```bash
# Database shows NULL even though SVGs exist
curl "https://entity-validator-backend-production-6962.up.railway.app/api/batch/1/items"
# Returns: svg_path: null

# But SVG files DO exist
curl "https://entity-validator-backend-production-6962.up.railway.app/api/svgs/list"
# Returns: 9 SVG files

# Regenerating SVGs works temporarily
curl -X POST ".../api/batch/1/svgs/generate"
# Success! But database changes don't survive deployment
```

## Root Cause
```python
# app.py line 133
DB_PATH = "entity_workflow.db"  # ← Stored in ephemeral filesystem

# app.py line 4995
conn.execute("UPDATE items SET svg_path=? WHERE id=?", (filepath, item_id))
# ← This update works in memory but doesn't persist across deployments
```

## Solutions

### Option 1: Railway Volume (RECOMMENDED)
Mount a persistent volume for both database and SVG exports:

1. **Create Railway volume**:
   ```bash
   # In Railway dashboard, add a volume at /data
   ```

2. **Update paths**:
   ```python
   DB_PATH = "/data/entity_workflow.db"
   SVG_DIR = "/data/svg_exports"
   ```

3. **Benefits**:
   - Simple SQLite database persists
   - SVG files persist
   - No external dependencies

### Option 2: External Database (PRODUCTION-READY)
Use PostgreSQL/MySQL on Railway or external service:

1. **Add Railway PostgreSQL**
2. **Update to use `psycopg2` or `mysql-connector`**
3. **Store SVGs in Cloudflare R2 or S3**

### Option 3: Hybrid (QUICK FIX)
Keep SQLite local but store SVGs in Cloudflare R2:

1. **SVG generation**: Save to R2 instead of local disk
2. **Database**: Still tracks R2 URLs in `svg_path`
3. **Benefit**: SVG downloads work even after deployments

## Current Workaround
Users must click "Generate SVGs" button after each deployment to recreate the files. This is NOT a long-term solution.

## Impact
- ❌ ZIP downloads are empty unless user views items first (triggers frontend save)
- ❌ Auto-generated SVGs during enrichment are lost on deployment
- ❌ Database loses all enrichment data on deployment
- ✅ Frontend auto-save (when viewing items) works temporarily until next deployment

## Next Steps
1. **Immediate**: Add Railway volume at `/data`
2. **Update**: Change `DB_PATH` and `SVG_DIR` to use `/data`
3. **Deploy**: Redeploy backend with persistent storage
4. **Test**: Run enrichment and verify persistence

## Files Affected
- `app.py` (lines 133, 1927, 4927, 4876)
- Railway configuration (needs volume mount)

---
**Created**: 2026-01-10  
**Status**: CRITICAL - Blocks production use
