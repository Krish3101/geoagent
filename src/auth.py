from fastapi import Header, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import os
import logging

logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=False)

def verify_token(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    x_auth_token: str = Header(None, alias="X-Auth-Token")
):
    # Retrieve token from environment variables
    # Default token to use if not specified in environment
    secret_token = os.getenv("GEO_AGENT_SECRET_TOKEN", "default_secret_token_123")
    
    token = None
    if credentials:
        token = credentials.credentials
    elif x_auth_token:
        token = x_auth_token
        
    if not token:
        logger.warning("Authentication failed: Missing token")
        raise HTTPException(
            status_code=401,
            detail="Missing authentication token. Use Authorization Bearer header or X-Auth-Token."
        )
        
    if token != secret_token:
        logger.warning("Authentication failed: Invalid token")
        raise HTTPException(
            status_code=403,
            detail="Invalid authentication token."
        )
        
    return token
