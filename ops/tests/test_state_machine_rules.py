import pytest
from decimal import Decimal
import datetime
from django.core.exceptions import ValidationError
from ops.models import (
    Tenant, ParentFactory, GlobalPart,
    MatrixShipment, MatrixShipmentItem, Worker, NaishokuJobOrder, ReusableQRCode
)
from core.middleware.tenant import set_tenant_context, clear_tenant_context

pytestmark = pytest.mark.django_db

def setup_base_data() -> tuple[Tenant, ParentFactory, GlobalPart, MatrixShipment, MatrixShipmentItem, Worker]:
    tenant = Tenant.objects.create(corporate_name="Factory A")
    parent = ParentFactory.objects.create(tenant_id=tenant, name="Suzuki", industry_code="SZ-1")
    part = GlobalPart.objects.create(
        tenant_id=tenant,
        parent_factory=parent,
        sku="SKU-123",
        name="Bolt",
        unit_weight=Decimal("15.5")
    )
    shipment = MatrixShipment.objects.create(
        tenant_id=tenant,
        parent_factory=parent,
        shipment_number="SH-999",
        status=MatrixShipment.Status.RECEIVED,
        received_at=datetime.datetime.now()
    )
    shipment_item = MatrixShipmentItem.objects.create(
        tenant_id=tenant,
        matrix_shipment=shipment,
        global_part=part,
        quantity_expected=100
    )
    worker = Worker.objects.create(
        tenant_id=tenant,
        name="John Doe",
        phone="080-1234-5678",
        address_line="Tokyo"
    )
    return tenant, parent, part, shipment, shipment_item, worker

def test_job_order_allocation_lock() -> None:
    """Testa o caso C1: Trava de Alocação garantindo que ordens não passem do esperado."""
    tenant, parent, part, shipment, shipment_item, worker = setup_base_data()
    token = set_tenant_context(tenant.id)
    try:
        # Criação de ordem que ocupa parte do esperado (60/100)
        NaishokuJobOrder.objects.create(
            tenant_id=tenant,
            matrix_shipment_item=shipment_item,
            worker=worker,
            short_audit_code="CODE-1",
            quantity_assigned=60,
            payout_per_unit=Decimal("10.5"),
            status=NaishokuJobOrder.Status.ASSIGNED,
            assigned_at=datetime.datetime.now(),
            deadline=datetime.date.today()
        )
        
        # Tentativa de criar outra ordem que ultrapassa o teto (50 + 60 = 110 > 100)
        with pytest.raises(ValidationError):
            NaishokuJobOrder.objects.create(
                tenant_id=tenant,
                matrix_shipment_item=shipment_item,
                worker=worker,
                short_audit_code="CODE-2",
                quantity_assigned=50,
                payout_per_unit=Decimal("10.5"),
                status=NaishokuJobOrder.Status.ASSIGNED,
                assigned_at=datetime.datetime.now(),
                deadline=datetime.date.today()
            )
    finally:
        clear_tenant_context(token)

def test_job_order_return_equation_validation() -> None:
    """Testa o caso C2: Validação da Equação de Retorno e Fechamento em IN_INSPECTION ou FINALIZED."""
    tenant, parent, part, shipment, shipment_item, worker = setup_base_data()
    token = set_tenant_context(tenant.id)
    try:
        job = NaishokuJobOrder.objects.create(
            tenant_id=tenant,
            matrix_shipment_item=shipment_item,
            worker=worker,
            short_audit_code="CODE-1",
            quantity_assigned=100,
            payout_per_unit=Decimal("10.5"),
            status=NaishokuJobOrder.Status.ASSIGNED,
            assigned_at=datetime.datetime.now(),
            deadline=datetime.date.today()
        )
        
        # Mudança para IN_INSPECTION com a equação batendo (80 aprovados + 15 refugos + 5 perdidos = 100)
        job.status = NaishokuJobOrder.Status.IN_INSPECTION
        job.quantity_approved = 80
        job.quantity_refused = 15
        job.quantity_lost = 5
        job.save()
        
        # Tentativa de salvar com valores incorretos (80 + 10 + 5 = 95 != 100)
        job.quantity_refused = 10
        with pytest.raises(ValidationError):
            job.save()
            
    finally:
        clear_tenant_context(token)

def test_reusable_qr_code_lifecycle() -> None:
    """Testa a regra de negócio do QR Code reutilizável: vinculação, ativação e bloqueio de duplicatas."""
    tenant, parent, part, shipment, shipment_item, worker = setup_base_data()
    token = set_tenant_context(tenant.id)
    try:
        qr = ReusableQRCode.objects.create(code="QR-001")
        assert not qr.is_active
        
        # Cria ordem associando ao QR Code
        job1 = NaishokuJobOrder.objects.create(
            tenant_id=tenant,
            matrix_shipment_item=shipment_item,
            worker=worker,
            reusable_qr_code=qr,
            short_audit_code="CODE-1",
            quantity_assigned=50,
            payout_per_unit=Decimal("10.5"),
            status=NaishokuJobOrder.Status.ASSIGNED,
            assigned_at=datetime.datetime.now(),
            deadline=datetime.date.today()
        )
        
        # QR Code deve estar ativo agora
        qr.refresh_from_db()
        assert qr.is_active
        
        # Tentativa de criar outra ordem associando ao mesmo QR Code (deve falhar por estar ativo)
        job2 = NaishokuJobOrder(
            tenant_id=tenant,
            matrix_shipment_item=shipment_item,
            worker=worker,
            reusable_qr_code=qr,
            short_audit_code="CODE-2",
            quantity_assigned=30,
            payout_per_unit=Decimal("10.5"),
            status=NaishokuJobOrder.Status.ASSIGNED,
            assigned_at=datetime.datetime.now(),
            deadline=datetime.date.today()
        )
        with pytest.raises(ValidationError):
            job2.save()
            
        # Finaliza a primeira ordem -> QR Code deve ser liberado (is_active = False)
        job1.status = NaishokuJobOrder.Status.FINALIZED
        job1.quantity_approved = 50
        job1.save()
        
        qr.refresh_from_db()
        assert not qr.is_active
        
        # Agora deve ser possível salvar a segunda ordem com esse QR Code
        job2.save()
        qr.refresh_from_db()
        assert qr.is_active
    finally:
        clear_tenant_context(token)
