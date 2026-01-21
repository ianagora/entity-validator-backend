# API Key Management Routes - Add to app.py after admin_users route

# ==============================================================================
# API KEY MANAGEMENT ENDPOINTS (ADMIN ONLY)
# ==============================================================================

@app.get("/admin/api-keys")
async def admin_api_keys_page(request: Request, current_user: dict = Depends(get_current_admin_user)):
    """Admin API key management page."""
    keys = list_api_keys(include_inactive=True)
    return templates.TemplateResponse(
        "admin_api_keys.html",
        {"request": request, "api_keys": keys, "current_user": current_user}
    )


@app.post("/api/admin/api-keys/create")
@limiter.limit("10/minute")
async def create_new_api_key(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    expires_in_days: Optional[int] = Form(None),
    current_user: dict = Depends(get_current_admin_user)
):
    """Create a new API key (admin only)."""
    try:
        key_info = create_api_key(
            name=name,
            description=description,
            scopes="api:read,api:write,batch:upload",
            created_by=current_user['id'],
            expires_in_days=expires_in_days
        )
        
        log_audit_event(
            action="api_key_created",
            status="success",
            user_email=current_user['email'],
            ip_address=get_real_ip(request),
            details=f"Created API key: {name}"
        )
        
        return {
            "success": True,
            "message": "API key created successfully",
            "key_info": key_info  # Contains the full key - ONLY TIME IT'S SHOWN
        }
    except Exception as e:
        log_audit_event(
            action="api_key_creation_failed",
            status="error",
            user_email=current_user['email'],
            ip_address=get_real_ip(request),
            details=str(e)
        )
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": f"Failed to create API key: {str(e)}"}
        )


@app.get("/api/admin/api-keys/list")
async def list_all_api_keys(
    request: Request,
    include_inactive: bool = Query(False),
    current_user: dict = Depends(get_current_admin_user)
):
    """List all API keys (admin only)."""
    keys = list_api_keys(include_inactive=include_inactive)
    return {"success": True, "api_keys": keys}


@app.post("/api/admin/api-keys/{key_id}/revoke")
@limiter.limit("20/minute")
async def revoke_existing_api_key(
    request: Request,
    key_id: str,
    current_user: dict = Depends(get_current_admin_user)
):
    """Revoke an API key (admin only)."""
    try:
        success = revoke_api_key(key_id)
        
        if success:
            log_audit_event(
                action="api_key_revoked",
                status="success",
                user_email=current_user['email'],
                ip_address=get_real_ip(request),
                details=f"Revoked API key: {key_id}"
            )
            return {"success": True, "message": "API key revoked successfully"}
        else:
            return JSONResponse(
                status_code=404,
                content={"success": False, "message": "API key not found"}
            )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": f"Failed to revoke API key: {str(e)}"}
        )
