import pytest
from typing import Any
from django.urls import reverse
from django.test import Client
from django.utils import timezone
from datetime import timedelta
from ops.models import Tenant, CustomUser

pytestmark = pytest.mark.django_db

def test_superadmin_dashboard_routing() -> None:
    """Testa o caso E1: Superadmin redirecionado para a página de controle SaaS."""
    admin_user = CustomUser.objects.create_superuser(username="admin", password="password")
    client = Client()
    client.login(username="admin", password="password")
    
    url = reverse("dashboard_redirect")
    response = client.get(url)
    assert response.status_code == 302
    response_any: Any = response
    assert response_any.url == reverse("saas_admin_dashboard")

def test_factory_user_dashboard_routing() -> None:
    """Testa o caso E2: Funcionário de fábrica ativa redirecionado para o dashboard operacional."""
    tenant = Tenant.objects.create(
        corporate_name="Factory A",
        subscription_expires_at=timezone.now() + timedelta(days=30)
    )
    # Criamos o usuário e logamos
    user = CustomUser.objects.create_user(username="employee", password="password", tenant_id=tenant)
    client = Client()
    client.login(username="employee", password="password")
    
    url = reverse("dashboard_redirect")
    response = client.get(url)
    assert response.status_code == 302
    response_any: Any = response
    assert response_any.url == reverse("factory_dashboard")

def test_suspended_tenant_routing() -> None:
    """Testa o caso E3: Redirecionamento de funcionários com assinatura suspensa/expirada."""
    tenant = Tenant.objects.create(
        corporate_name="Factory A",
        subscription_expires_at=timezone.now() - timedelta(days=1)  # Expirada
    )
    user = CustomUser.objects.create_user(username="employee", password="password", tenant_id=tenant)
    client = Client()
    client.login(username="employee", password="password")
    
    # Tenta acessar o dashboard operacional
    url = reverse("factory_dashboard")
    response = client.get(url)
    assert response.status_code == 302
    response_any: Any = response
    assert response_any.url == reverse("suspended_page")

def test_saas_admin_crud_and_subscription_credit() -> None:
    """Testa o caso E4: CRUD de fábricas e acréscimo de dias de assinatura pelo Superadmin."""
    admin_user = CustomUser.objects.create_superuser(username="admin", password="password")
    client = Client()
    client.login(username="admin", password="password")
    
    # 1. Criação (C no CRUD) de um Tenant
    url = reverse("saas_admin_dashboard")
    response = client.post(url, {
        "action": "create_tenant",
        "corporate_name": "New Factory Ltd",
        "postal_code": "430-0911",
        "prefecture": "Shizuoka"
    })
    assert response.status_code == 302
    
    new_tenant = Tenant.objects.filter(corporate_name="New Factory Ltd").first()
    assert new_tenant is not None
    assert new_tenant.postal_code == "430-0911"
    
    # Define uma data inicial (ou nula) e credita 15 dias de assinatura
    initial_expiry = new_tenant.subscription_expires_at or timezone.now()
    response = client.post(url, {
        "action": "credit_subscription",
        "tenant_uuid": str(new_tenant.id),
        "days": 15
    })
    assert response.status_code == 302
    
    new_tenant.refresh_from_db()
    assert new_tenant.subscription_expires_at is not None
    # Verifica se a nova validade aumentou cerca de 15 dias em relação à data original
    delta = new_tenant.subscription_expires_at - initial_expiry
    assert delta.days >= 14
