import pytest
from django.urls import reverse
from django.test import Client
from django.core import signing
from decimal import Decimal
import datetime
from ops.models import (
    Tenant, ParentFactory, GlobalPart, MatrixShipment,
    MatrixShipmentItem, Worker, NaishokuJobOrder, ReusableQRCode
)
from core.middleware.tenant import set_tenant_context, clear_tenant_context

pytestmark = pytest.mark.django_db

def test_signed_url_tampering() -> None:
    """Testa o caso D1: Violação de assinatura de token nas URLs públicas (HTTP 403)."""
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
    
    token_context = set_tenant_context(tenant.id)
    try:
        job = NaishokuJobOrder.objects.create(
            tenant_id=tenant,
            matrix_shipment_item=shipment_item,
            worker=worker,
            short_audit_code="SUZU-1-K",
            quantity_assigned=50,
            payout_per_unit=Decimal("10.5"),
            status=NaishokuJobOrder.Status.ASSIGNED,
            assigned_at=datetime.datetime.now(),
            deadline=datetime.date.today()
        )
        job_id = str(job.id)
        
        # Gera token criptografado válido contendo o UUID
        valid_token = signing.dumps(job_id)
        
        client = Client()
        
        # Teste 1: Acesso com token válido deve retornar HTTP 200
        url_valid = reverse("go_endpoint", kwargs={"token": valid_token})
        response = client.get(url_valid)
        assert response.status_code == 200
        
        # Teste 2: Alterar caractere final do token deve falhar com HTTP 403
        invalid_token = valid_token[:-1] + ("A" if valid_token[-1] != "A" else "B")
        url_invalid = reverse("go_endpoint", kwargs={"token": invalid_token})
        response = client.get(url_invalid)
        assert response.status_code == 403
    finally:
        clear_tenant_context(token_context)

def test_shortcode_checksum_validation() -> None:
    """Testa o caso D2: Rejeição instantânea de shortcodes corrompidos na validação do formulário."""
    client = Client()
    url = reverse("contingency_go_endpoint")
    
    # Tenta submeter um código com formato ou checksum corrompido
    # Ex: "SUZU-1452-A" (vamos assumir que a letra A não seja o checksum do hash HMAC do Django)
    response = client.post(url, {"short_code": "SUZU-1452-A"})
    assert response.status_code == 200
    assert "form" in response.context
    assert not response.context["form"].is_valid()
    assert "short_code" in response.context["form"].errors

def test_reusable_qr_code_routing() -> None:
    """Testa o caso D3: Mapeamento de links de QR Codes Reutilizáveis (HTTP 200 vs HTTP 404)."""
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
    
    token_context = set_tenant_context(tenant.id)
    try:
        qr = ReusableQRCode.objects.create(code="QR-001")
        # Gera o token criptografado para o ID do QR Code
        qr_token = signing.dumps(str(qr.id))
        client = Client()
        
        # Caso 1: QR Code não associado a nenhuma ordem -> Deve retornar 404
        url = reverse("go_endpoint", kwargs={"token": qr_token})
        response = client.get(url)
        assert response.status_code == 404
        
        # Caso 2: Associado a uma ordem de serviço ativa -> Deve retornar 200 e injetar a ordem no contexto
        job = NaishokuJobOrder.objects.create(
            tenant_id=tenant,
            matrix_shipment_item=shipment_item,
            worker=worker,
            reusable_qr_code=qr,
            short_audit_code="SUZU-2-L",
            quantity_assigned=50,
            payout_per_unit=Decimal("10.5"),
            status=NaishokuJobOrder.Status.ASSIGNED,
            assigned_at=datetime.datetime.now(),
            deadline=datetime.date.today()
        )
        
        response = client.get(url)
        assert response.status_code == 200
        assert response.context["job_order"] == job
        
        # Caso 3: Ordem finalizada -> QR Code é liberado, então novo acesso deve dar 404
        job.status = NaishokuJobOrder.Status.FINALIZED
        job.quantity_approved = 50
        job.save()
        
        response = client.get(url)
        assert response.status_code == 404
    finally:
        clear_tenant_context(token_context)

from ops.models import CustomUser

def test_resolve_qr_code_ajax() -> None:
    """Testa o endpoint AJAX que resolve uma URL de QR Code para a entidade do banco."""
    from django.utils import timezone
    from datetime import timedelta
    tenant = Tenant.objects.create(
        corporate_name="Factory A",
        subscription_expires_at=timezone.now() + timedelta(days=30)
    )
    user = CustomUser.objects.create_user(username="factory_mgr", password="password", tenant_id=tenant)
    client = Client()
    client.login(username="factory_mgr", password="password")

    token_context = set_tenant_context(tenant.id)
    try:
        qr = ReusableQRCode.objects.create(tenant_id=tenant, code="QR-002")
        qr_token = signing.dumps(str(qr.id))
        
        # Caso 1: Fornecendo a URL contendo o token correto -> Deve retornar 200
        url = reverse("resolve_qr_code_ajax")
        response = client.get(f"{url}?url=http://localhost:8000/go/{qr_token}")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["id"] == str(qr.id)
        assert data["code"] == "QR-002"

        # Caso 2: URL com token inválido -> Deve retornar 400
        response = client.get(f"{url}?url=http://localhost:8000/go/invalidtoken")
        assert response.status_code == 400
        assert response.json()["success"] is False
    finally:
        clear_tenant_context(token_context)
