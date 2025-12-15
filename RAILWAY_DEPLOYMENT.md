# Railway Deployment Guide

## 🚂 Quick Deploy to Railway

### **Prerequisites**
- Railway account (sign up at https://railway.app)
- GitHub account
- This repository pushed to GitHub

### **Step 1: Push to GitHub**

If you haven't already, push this repository to GitHub:

```bash
cd /home/user/entity-validator-backend
git add .
git commit -m "Prepare for Railway deployment"
git push origin main
```

### **Step 2: Deploy to Railway**

1. Go to https://railway.app
2. Click **"New Project"**
3. Select **"Deploy from GitHub repo"**
4. Choose your `entity-validator-backend` repository
5. Railway will automatically:
   - Detect the `Dockerfile`
   - Build the Docker image
   - Deploy your backend
   - Provide a public URL (e.g., `https://your-app.railway.app`)

### **Step 3: Set Environment Variables**

In Railway dashboard:

1. Click on your deployed service
2. Go to **"Variables"** tab
3. Add these environment variables:

```bash
CH_API_KEY=your-companies-house-api-key-here
OPENAI_API_KEY=your-openai-api-key-here
```

**Note**: Get your actual API keys from:
- Companies House API: https://developer.company-information.service.gov.uk/
- OpenAI API: https://platform.openai.com/api-keys

4. Click **"Deploy"** to restart with new variables

### **Step 4: Get Your Backend URL**

After deployment completes:

1. Railway will provide a public URL like: `https://entity-validator-backend-production.up.railway.app`
2. Copy this URL
3. You'll use it to configure your frontend

### **Step 5: Test Your Backend**

Test that your backend is running:

```bash
curl https://your-backend-url.railway.app/
```

You should see the backend homepage.

### **Step 6: Connect Frontend to Backend**

1. Go to Cloudflare Pages dashboard
2. Navigate to **project-a4de28cf** > **Settings** > **Environment Variables**
3. Add:
   - **Name**: `BACKEND_API_URL`
   - **Value**: `https://your-backend-url.railway.app`
   - **Environment**: Production
4. Save and redeploy

---

## 🎯 Expected Result

After completing these steps:

✅ Backend running on Railway 24/7  
✅ Frontend on Cloudflare Pages connected to backend  
✅ Full application accessible worldwide  
✅ Database persists on Railway's volume storage  

---

## 📋 Configuration Files

This repository includes:

- ✅ `Dockerfile` - Docker configuration
- ✅ `railway.toml` - Railway-specific configuration
- ✅ `requirements.txt` - Python dependencies
- ✅ `main.py` - Entry point that respects Railway's PORT env var
- ✅ `.dockerignore` - Files to exclude from Docker build

---

## 🔧 Troubleshooting

### Backend won't start
- Check Railway logs for errors
- Verify environment variables are set
- Ensure Dockerfile builds successfully

### Can't connect from frontend
- Verify BACKEND_API_URL is set correctly in Cloudflare
- Check Railway service is running and has a public URL
- Test backend URL directly in browser

### Database not persisting
- Railway provides ephemeral storage by default
- Consider using Railway's PostgreSQL plugin for persistent storage
- Or use SQLite with Railway's volume mount

---

## 💰 Cost

Railway offers:
- **Free tier**: $5 credit per month
- **Hobby plan**: $5/month + usage
- This backend should fit comfortably in free tier for development

---

## 🔗 Useful Links

- Railway Dashboard: https://railway.app/dashboard
- Railway Docs: https://docs.railway.app
- Cloudflare Pages: https://dash.cloudflare.com
