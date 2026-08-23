"""Configuration management using Pydantic."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    """Configuration from environment variables."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )
    hubstaff_organization_id: str
    hubstaff_api_base_url: str = "https://api.hubstaff.com"
    hubstaff_tasks_base_url: str = "https://tasks.hubstaff.com"
    port: int = 8000

    # --- OAuth broker (zero-paste per-user auth) ---------------------------
    # Credentials for the single upstream Hubstaff OAuth app that every user is
    # federated through. Required — OAuth is the only supported auth method.
    hubstaff_client_id: str | None = None
    hubstaff_client_secret: str | None = None

    # Public, externally-visible HTTPS origin of THIS server. All OAuth metadata
    # URLs, the resource identifier, and our upstream redirect_uri are built from
    # this — never derive them from the incoming request URL (which would be the
    # internal container host behind the proxy).
    public_base_url: str = "https://hubstaff.hbai.dev"

    # OAuth upstream endpoints (Hubstaff account server).
    hubstaff_account_base_url: str = "https://account.hubstaff.com"

    # Persistence for the OAuth broker (sqlite). Lives on a directory bind-mount
    # so WAL sidecar files work — see docker-compose.yml.
    oauth_db_path: str = "/app/data/oauth.db"

    # Token/code lifetimes (seconds).
    access_token_ttl_seconds: int = 3600            # our issued access tokens
    refresh_token_ttl_seconds: int = 30 * 24 * 3600  # bounded by Hubstaff's window
    auth_code_ttl_seconds: int = 60
    pending_auth_ttl_seconds: int = 600

    # OAuth scopes requested from Hubstaff. Must include tasks:* so the Tasks
    # tools keep working through a brokered token.
    hubstaff_scopes: str = (
        "openid profile email hubstaff:read hubstaff:write tasks:read tasks:write"
    )

    @property
    def resource_uri(self) -> str:
        """Canonical MCP resource identifier (RFC8707 audience)."""
        return f"{self.public_base_url.rstrip('/')}/mcp"

    @property
    def hubstaff_redirect_uri(self) -> str:
        """Our registered upstream redirect URI at Hubstaff."""
        return f"{self.public_base_url.rstrip('/')}/oauth/hubstaff/callback"

    @property
    def oauth_configured(self) -> bool:
        """True when the upstream Hubstaff OAuth app credentials are present."""
        return bool(self.hubstaff_client_id and self.hubstaff_client_secret)
    
    @property
    def hubstaff_org_id(self) -> str:
        return self.hubstaff_organization_id

    @property
    def base_url(self) -> str:
        return self.hubstaff_api_base_url


config = Config()
