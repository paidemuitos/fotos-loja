from contextvars import ContextVar
import uuid
from typing import Any

_current_tenant: ContextVar[uuid.UUID | None] = ContextVar("current_tenant", default=None)

def set_tenant_context(tenant_id: uuid.UUID) -> Any:
    """Sets the tenant context and returns a token for cleanup."""
    return _current_tenant.set(tenant_id)

def get_tenant_context() -> uuid.UUID | None:
    """Gets the active tenant ID from the context."""
    return _current_tenant.get()

def clear_tenant_context(token: Any) -> None:
    """Clears the tenant context using the token."""
    _current_tenant.reset(token)

from django.shortcuts import redirect

class TenantMiddleware:
    """
    Middleware that extracts the tenant_id from the authenticated CustomUser,
    sets it in the ContextVar for the duration of the request,
    and enforces active subscription checks.
    """
    def __init__(self, get_response: Any) -> None:
        self.get_response = get_response

    def __call__(self, request: Any) -> Any:
        token = None
        user = getattr(request, 'user', None)

        if user and user.is_authenticated:
            # Se for usuário com tenant vinculado (não superadmin)
            if not user.is_superuser and user.tenant_id:
                # Checa validade da assinatura
                if not user.tenant_id.is_subscription_active():
                    allowed_paths = ['/suspended/', '/logout/', '/admin/logout/']
                    if request.path not in allowed_paths:
                        return redirect('suspended_page')
                
                # Define o contexto caso esteja ativo
                token = set_tenant_context(user.tenant_id_id)

        response = self.get_response(request)

        if token is not None:
            clear_tenant_context(token)

        return response
