import pytest
import uuid
from typing import Generator
from ops.models import Tenant, CustomUser
from core.middleware.tenant import set_tenant_context, clear_tenant_context

pytestmark = pytest.mark.django_db

def test_orm_injects_tenant_id_automatically() -> None:
    """Testa o caso A2: Isolamento automático no get_queryset e save()"""
    tenant_a = Tenant.objects.create(id=uuid.uuid4(), corporate_name="Tenant A")
    tenant_b = Tenant.objects.create(id=uuid.uuid4(), corporate_name="Tenant B")
    
    # Simulando criação bruta para burlar o manager durante o setup
    CustomUser.objects.bulk_create([
        CustomUser(username="usera", tenant_id=tenant_a),
        CustomUser(username="userb", tenant_id=tenant_b)
    ])
    
    # 1. Simula Middleware ativando contexto do Tenant A
    token = set_tenant_context(tenant_a.id)
    
    try:
        # 2. Leitura: objects.all() deve injetar 'WHERE tenant_id = tenant_a.id'
        users = CustomUser.objects.all()
        assert users.count() == 1
        first_user = users.first()
        assert first_user is not None
        assert first_user.username == "usera"
        
        # 3. Escrita: save() deve interceptar e forçar o tenant_id do contexto
        new_user = CustomUser(username="hacker", tenant_id=tenant_b)
        new_user.save()
        
        # Recarrega e verifica se o tenant_id foi corrigido
        new_user.refresh_from_db()
        assert new_user.tenant_id_id == tenant_a.id
    finally:
        clear_tenant_context(token)
