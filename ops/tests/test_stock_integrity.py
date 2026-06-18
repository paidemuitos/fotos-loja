import pytest
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from ops.models import Tenant, CustomUser, ParentFactory, GlobalPart, StockMovementLedger
from core.middleware.tenant import set_tenant_context, clear_tenant_context

pytestmark = pytest.mark.django_db

def test_stock_movement_ledger_calculation() -> None:
    """Testa o caso B1: Cálculo de saldo acumulado no ledger de estoque."""
    tenant = Tenant.objects.create(corporate_name="Factory A")
    token = set_tenant_context(tenant.id)
    
    try:
        user = CustomUser.objects.create(username="operator", tenant_id=tenant)
        parent = ParentFactory.objects.create(name="Suzuki", industry_code="SZ-1")
        part = GlobalPart.objects.create(
            parent_factory=parent,
            sku="SKU-123",
            name="Bolt",
            blueprint_url="http://example.com",
            unit_weight=Decimal("15.5")
        )
        
        # Saldo inicial deve ser 0
        assert StockMovementLedger.get_balance(part, tenant) == 0
        
        # Movimentação positiva (MATRIZ_IN)
        StockMovementLedger.objects.create(
            tenant_id=tenant,
            global_part=part,
            quantity=100,
            movement_type=StockMovementLedger.MovementType.MATRIZ_IN,
            processed_by=user
        )
        assert StockMovementLedger.get_balance(part, tenant) == 100
        
        # Movimentação negativa (WORKER_DISTRIBUTION)
        StockMovementLedger.objects.create(
            tenant_id=tenant,
            global_part=part,
            quantity=-30,
            movement_type=StockMovementLedger.MovementType.WORKER_DISTRIBUTION,
            processed_by=user
        )
        assert StockMovementLedger.get_balance(part, tenant) == 70
        
        # Retorno de peças para a Matriz (MATRIZ_RETURN)
        StockMovementLedger.objects.create(
            tenant_id=tenant,
            global_part=part,
            quantity=-10,
            movement_type=StockMovementLedger.MovementType.MATRIZ_RETURN,
            processed_by=user
        )
        assert StockMovementLedger.get_balance(part, tenant) == 60
    finally:
        clear_tenant_context(token)

def test_stock_movement_requires_user_traceability() -> None:
    """Testa o caso B2: processed_by é mandatório no ledger, barrando anônimos."""
    tenant = Tenant.objects.create(corporate_name="Factory A")
    token = set_tenant_context(tenant.id)
    try:
        parent = ParentFactory.objects.create(name="Suzuki", industry_code="SZ-1")
        part = GlobalPart.objects.create(
            parent_factory=parent,
            sku="SKU-123",
            name="Bolt",
            unit_weight=Decimal("15.5")
        )
        
        # Tentativa de criar sem operador/usuário responsável
        with pytest.raises((IntegrityError, ValidationError)):
            StockMovementLedger.objects.create(
                tenant_id=tenant,
                global_part=part,
                quantity=100,
                movement_type=StockMovementLedger.MovementType.MATRIZ_IN,
                processed_by=None
            )
    finally:
        clear_tenant_context(token)
