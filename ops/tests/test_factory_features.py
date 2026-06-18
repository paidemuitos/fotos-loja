import pytest
from typing import Any
from decimal import Decimal
from django.urls import reverse
from django.test import Client
from django.utils import timezone
from datetime import timedelta
from ops.models import (
    Tenant, CustomUser, Worker, ParentFactory, GlobalPart,
    MatrixShipment, MatrixShipmentItem, ReusableQRCode, NaishokuJobOrder,
    StockMovementLedger
)
from core.middleware.tenant import set_tenant_context, clear_tenant_context

pytestmark = pytest.mark.django_db

def test_worker_management_view() -> None:
    """Testa listagem e criação de trabalhadores vinculados ao tenant."""
    tenant = Tenant.objects.create(
        corporate_name="Test Factory",
        subscription_expires_at=timezone.now() + timedelta(days=10)
    )
    CustomUser.objects.create_user(username="factory_mgr", password="password", tenant_id=tenant)
    client = Client()
    client.login(username="factory_mgr", password="password")

    # 1. Test GET list
    url = reverse("worker_list")
    response = client.get(url)
    assert response.status_code == 200
    assert "Test Factory" in response.content.decode("utf-8")

    # 2. Test POST create worker
    response = client.post(url, {
        "name": "João da Silva",
        "phone": "090-1234-5678",
        "address_line": "Tokyo-to, Minato-ku 1-1",
        "geolocation": "35.6586,139.7454"
    })
    assert response.status_code == 302
    response_any: Any = response
    assert response_any.url == url

    # Verify database entry and tenant binding
    worker = Worker.objects.filter(name="João da Silva").first()
    assert worker is not None
    assert worker.tenant_id == tenant
    assert worker.geolocation == "35.6586,139.7454"

def test_shipment_receipt_and_stock_ledger() -> None:
    """Testa recebimento de lote da matriz e injeção automática no Ledger."""
    tenant = Tenant.objects.create(
        corporate_name="Test Factory",
        subscription_expires_at=timezone.now() + timedelta(days=10)
    )
    user = CustomUser.objects.create_user(username="factory_mgr", password="password", tenant_id=tenant)
    
    # Matriz e Peça global
    matriz = ParentFactory.objects.create(tenant_id=tenant, name="Matriz Toyota", industry_code="TOY-1")
    part = GlobalPart.objects.create(
        tenant_id=tenant,
        parent_factory=matriz,
        sku="SKU-PART-1",
        name="Engine Clip",
        unit_weight=Decimal("5.0")
    )

    client = Client()
    client.login(username="factory_mgr", password="password")

    url = reverse("shipment_list")
    # 1. GET page
    response = client.get(url)
    assert response.status_code == 200

    # 2. POST to receive shipment
    response = client.post(url, {
        "parent_factory": str(matriz.id),
        "shipment_number": "SHIP-TOYOTA-2026-X",
        "received_at": "2026-06-17T12:00",
        "global_part": str(part.id),
        "quantity_expected": 1000
    })
    assert response.status_code == 302
    response_any: Any = response
    assert response_any.url == url

    # Verify Shipment and Item creation
    shipment = MatrixShipment.objects.filter(shipment_number="SHIP-TOYOTA-2026-X").first()
    assert shipment is not None
    assert shipment.tenant_id == tenant

    item = MatrixShipmentItem.objects.filter(matrix_shipment=shipment).first()
    assert item is not None
    assert item.global_part == part
    assert item.quantity_expected == 1000

    # Verify Ledger automatic entry (MATRIZ_IN)
    ledger_entry = StockMovementLedger.objects.filter(
        global_part=part,
        movement_type=StockMovementLedger.MovementType.MATRIZ_IN
    ).first()
    assert ledger_entry is not None
    assert ledger_entry.quantity == 1000
    assert ledger_entry.processed_by == user

def test_job_order_allocation_rules() -> None:
    """Testa criação de OS, trava de alocação de peças e dedução de estoque."""
    tenant = Tenant.objects.create(
        corporate_name="Test Factory",
        subscription_expires_at=timezone.now() + timedelta(days=10)
    )
    user = CustomUser.objects.create_user(username="factory_mgr", password="password", tenant_id=tenant)
    
    matriz = ParentFactory.objects.create(tenant_id=tenant, name="Matriz Toyota", industry_code="TOY-1")
    part = GlobalPart.objects.create(
        tenant_id=tenant,
        parent_factory=matriz,
        sku="SKU-PART-1",
        name="Engine Clip",
        unit_weight=Decimal("5.0")
    )

    token = set_tenant_context(tenant.id)
    try:
        worker = Worker.objects.create(name="Maria", phone="123", address_line="Shibuya")
        qr = ReusableQRCode.objects.create(code="QR-TEST-1", is_active=False)
        shipment = MatrixShipment.objects.create(
            parent_factory=matriz,
            shipment_number="SHIP-1",
            received_at=timezone.now()
        )
        item = MatrixShipmentItem.objects.create(
            matrix_shipment=shipment,
            global_part=part,
            quantity_expected=500
        )
    finally:
        clear_tenant_context(token)

    client = Client()
    client.login(username="factory_mgr", password="password")

    url = reverse("job_order_create")

    # 1. Tenta alocar quantidade maior que a disponível no lote (501 > 500)
    response = client.post(url, {
        "reusable_qr_code": str(qr.id),
        "matrix_shipment_item": str(item.id),
        "worker": str(worker.id),
        "quantity_assigned": 501,
        "payout_per_unit": "10.0",
        "deadline": "2026-06-25"
    })
    assert response.status_code == 200 # Re-renders form due to error
    assert "A quantidade total alocada excede o esperado" in response.content.decode("utf-8")

    # 2. Aloca quantidade válida (300)
    response = client.post(url, {
        "reusable_qr_code": str(qr.id),
        "matrix_shipment_item": str(item.id),
        "worker": str(worker.id),
        "quantity_assigned": 300,
        "payout_per_unit": "10.0",
        "deadline": "2026-06-25"
    })
    assert response.status_code == 302
    response_any: Any = response
    assert response_any.url == reverse("factory_dashboard")

    # Verify database state
    job = NaishokuJobOrder.objects.filter(worker=worker).first()
    assert job is not None
    assert job.quantity_assigned == 300
    assert job.reusable_qr_code == qr
    assert job.reusable_qr_code.is_active is True

    # Verify Stock Ledger negative movement
    ledger_entry = StockMovementLedger.objects.filter(
        global_part=part,
        movement_type=StockMovementLedger.MovementType.WORKER_DISTRIBUTION
    ).first()
    assert ledger_entry is not None
    assert ledger_entry.quantity == -300
    assert ledger_entry.processed_by == user
    assert ledger_entry.worker == worker

def test_physical_inspection_and_finalization() -> None:
    """Testa o balanço matemático da conferência física de OS e devolução de estoque."""
    tenant = Tenant.objects.create(
        corporate_name="Test Factory",
        subscription_expires_at=timezone.now() + timedelta(days=10)
    )
    user = CustomUser.objects.create_user(username="factory_mgr", password="password", tenant_id=tenant)
    
    matriz = ParentFactory.objects.create(tenant_id=tenant, name="Matriz Toyota", industry_code="TOY-1")
    part = GlobalPart.objects.create(
        tenant_id=tenant,
        parent_factory=matriz,
        sku="SKU-PART-1",
        name="Engine Clip",
        unit_weight=Decimal("5.0")
    )

    token = set_tenant_context(tenant.id)
    try:
        worker = Worker.objects.create(name="Maria", phone="123", address_line="Shibuya")
        qr = ReusableQRCode.objects.create(code="QR-TEST-1", is_active=True)
        shipment = MatrixShipment.objects.create(
            parent_factory=matriz,
            shipment_number="SHIP-1",
            received_at=timezone.now()
        )
        item = MatrixShipmentItem.objects.create(
            matrix_shipment=shipment,
            global_part=part,
            quantity_expected=500
        )
        job = NaishokuJobOrder.objects.create(
            reusable_qr_code=qr,
            matrix_shipment_item=item,
            worker=worker,
            quantity_assigned=200,
            payout_per_unit=Decimal("15.0"),
            status=NaishokuJobOrder.Status.ASSIGNED,
            assigned_at=timezone.now(),
            deadline=(timezone.now() + timedelta(days=5)).date()
        )
    finally:
        clear_tenant_context(token)

    client = Client()
    client.login(username="factory_mgr", password="password")

    url = reverse("job_order_inspect", kwargs={"pk": job.id})

    # 1. Tenta encerrar com discrepância (180 + 10 + 5 = 195 != 200)
    response = client.post(url, {
        "quantity_approved": 180,
        "quantity_refused": 10,
        "quantity_lost": 5
    })
    assert response.status_code == 200 # Re-renders due to error
    assert "Inconsistência matemática" in response.content.decode("utf-8")

    # 2. Encerra com balanço correto (180 + 15 + 5 = 200)
    response = client.post(url, {
        "quantity_approved": 180,
        "quantity_refused": 15,
        "quantity_lost": 5
    })
    assert response.status_code == 302
    response_any: Any = response
    assert response_any.url == reverse("factory_dashboard")

    # Verify OS finalization
    job.refresh_from_db()
    assert job.status == NaishokuJobOrder.Status.FINALIZED
    assert job.quantity_approved == 180
    assert job.quantity_refused == 15
    assert job.quantity_lost == 5
    assert job.total_payout == Decimal("2700.0") # 180 approved * 15.0 payout

    # Verify QR Code release
    qr.refresh_from_db()
    assert qr.is_active is False

    # Verify ledger positive returns
    return_app = StockMovementLedger.objects.filter(
        global_part=part,
        movement_type=StockMovementLedger.MovementType.WORKER_RETURN_APPROVED
    ).first()
    assert return_app is not None
    assert return_app.quantity == 180

    return_ref = StockMovementLedger.objects.filter(
        global_part=part,
        movement_type=StockMovementLedger.MovementType.WORKER_RETURN_REFUSE
    ).first()
    assert return_ref is not None
    assert return_ref.quantity == 15

    return_lost = StockMovementLedger.objects.filter(
        global_part=part,
        movement_type=StockMovementLedger.MovementType.LOSS_DIVIDEND
    ).first()
    assert return_lost is not None
    assert return_lost.quantity == 0 # Loss logged but no stock re-added


def test_job_order_creation_with_generated_qr() -> None:
    """Testa a criação de OS com QR Code vazio que dispara a geração automática e redirecionamento para impressão."""
    tenant = Tenant.objects.create(
        corporate_name="Test Factory",
        subscription_expires_at=timezone.now() + timedelta(days=10)
    )
    user = CustomUser.objects.create_user(username="factory_mgr", password="password", tenant_id=tenant)
    
    matriz = ParentFactory.objects.create(tenant_id=tenant, name="Matriz Toyota", industry_code="TOY-1")
    part = GlobalPart.objects.create(
        tenant_id=tenant,
        parent_factory=matriz,
        sku="SKU-PART-1",
        name="Engine Clip",
        unit_weight=Decimal("5.0")
    )

    token = set_tenant_context(tenant.id)
    try:
        worker = Worker.objects.create(name="Maria", phone="123", address_line="Shibuya")
        shipment = MatrixShipment.objects.create(
            parent_factory=matriz,
            shipment_number="SHIP-1",
            received_at=timezone.now()
        )
        item = MatrixShipmentItem.objects.create(
            matrix_shipment=shipment,
            global_part=part,
            quantity_expected=500
        )
    finally:
        clear_tenant_context(token)

    client = Client()
    client.login(username="factory_mgr", password="password")

    url = reverse("job_order_create")

    # 1. Envia formulário sem QR Code e sem action_generate_print (deve falhar com erro de validação)
    response = client.post(url, {
        "reusable_qr_code": "",
        "matrix_shipment_item": str(item.id),
        "worker": str(worker.id),
        "quantity_assigned": 100,
        "payout_per_unit": "15.0",
        "deadline": "2026-06-25",
        "action_generate_print": "false"
    })
    assert response.status_code == 200
    assert "QR Code é obrigatório ou selecione gerar e imprimir." in response.content.decode("utf-8")

    # 2. Envia formulário sem QR Code, mas com action_generate_print="true" (deve criar OS e QR Code e redirecionar para impressão)
    response = client.post(url, {
        "reusable_qr_code": "",
        "matrix_shipment_item": str(item.id),
        "worker": str(worker.id),
        "quantity_assigned": 100,
        "payout_per_unit": "15.0",
        "deadline": "2026-06-25",
        "action_generate_print": "true"
    })
    assert response.status_code == 302
    
    # Verifica a OS criada e o QR Code gerado
    job = NaishokuJobOrder.objects.filter(worker=worker).first()
    assert job is not None
    assert job.reusable_qr_code is not None
    # O prefixo de "Test Factory" deve ser "TEST" -> "QR-TEST-001"
    assert job.reusable_qr_code.code == "QR-TEST-001"
    assert job.reusable_qr_code.is_active is True
    
    # Redirecionamento deve apontar para print_job_qr
    print_url = reverse("print_job_qr", kwargs={"pk": job.id})
    response_any: Any = response
    assert response_any.url == print_url

    # 3. Testa o acesso à tela de impressão
    response_print = client.get(print_url)
    assert response_print.status_code == 200
    content_decoded = response_print.content.decode("utf-8")
    assert "TEST-1" in content_decoded
    assert "QR-TEST-001" in content_decoded
    assert "https://api.qrserver.com" in content_decoded
    assert "window.print()" in content_decoded

