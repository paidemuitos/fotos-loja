import uuid
import hmac
import hashlib
from typing import Any
from decimal import Decimal
from django.db import models
from django.conf import settings
from django.contrib.auth.models import AbstractUser, UserManager
from django.core.exceptions import ValidationError
from core.middleware.tenant import get_tenant_context

def calculate_checksum(base_code: str) -> str:
    """Calcula um dígito verificador alfabético (A-Z) para o código base usando HMAC-SHA256."""
    key = settings.SECRET_KEY.encode()
    h = hmac.new(key, base_code.encode(), hashlib.sha256).hexdigest()
    val = int(h[:8], 16)
    return chr(65 + (val % 26))  # A-Z

def verify_short_code(short_code: str) -> bool:
    """Valida matematicamente se o shortcode possui um checksum válido antes de buscar no DB."""
    try:
        parts = short_code.rsplit('-', 1)
        if len(parts) != 2:
            return False
        base_code, checksum = parts
        expected = calculate_checksum(base_code)
        return hmac.compare_digest(checksum.upper(), expected)
    except Exception:
        return False

class Tenant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    corporate_name = models.CharField(max_length=255)
    postal_code = models.CharField(max_length=20, blank=True)
    prefecture = models.CharField(max_length=100, blank=True)
    subscription_expires_at = models.DateTimeField(null=True, blank=True)

    def is_subscription_active(self) -> bool:
        from django.utils import timezone
        if self.subscription_expires_at is None:
            return False
        return self.subscription_expires_at > timezone.now()

    def __str__(self) -> str:
        return self.corporate_name

class TenantManager(models.Manager): # type: ignore
    def get_queryset(self) -> models.QuerySet: # type: ignore
        qs = super().get_queryset()
        current_tenant = get_tenant_context()
        if current_tenant:
            return qs.filter(tenant_id=current_tenant)
        return qs

class TenantModel(models.Model):
    tenant_id = models.ForeignKey(Tenant, on_delete=models.PROTECT, editable=False)

    objects = TenantManager()

    class Meta:
        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        current_tenant = get_tenant_context()
        if current_tenant:
            self.tenant_id_id = current_tenant
        elif not self.tenant_id_id:
            raise ValidationError("Tenant_id ausente.")
        super().save(*args, **kwargs)

class CustomUserManager(TenantManager, UserManager): # type: ignore
    pass

class CustomUser(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.ForeignKey(Tenant, on_delete=models.PROTECT, null=True, blank=True)
    
    objects = CustomUserManager() # type: ignore

    def save(self, *args: Any, **kwargs: Any) -> None:
        current_tenant = get_tenant_context()
        if current_tenant:
            self.tenant_id_id = current_tenant
        super().save(*args, **kwargs)

class ParentFactory(TenantModel):
    """Matrizes Industriais (Tenant-Specific)"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    industry_code = models.CharField(max_length=255)

    def __str__(self) -> str:
        return self.name

class GlobalPart(TenantModel):
    """Catálogo de Peças (Tenant-Specific)"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    parent_factory = models.ForeignKey(ParentFactory, on_delete=models.PROTECT)
    sku = models.CharField(max_length=100)
    name = models.CharField(max_length=255)
    blueprint_url = models.URLField(blank=True, null=True)
    blueprint_file = models.FileField(upload_to='blueprints/', blank=True, null=True)
    part_photo = models.FileField(upload_to='part_photos/', blank=True, null=True)
    unit_weight = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)  # Em gramas (ex: 15.500)

    def __str__(self) -> str:
        return f"{self.sku} - {self.name}"

class MatrixShipment(TenantModel):
    """Lotes de Entrada da Matriz"""
    class Status(models.TextChoices):
        RECEIVED = 'RECEIVED', 'Recebido'
        FRACTIONED = 'FRACTIONED', 'Fracionado'
        RETURNED = 'RETURNED', 'Retornado'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    parent_factory = models.ForeignKey(ParentFactory, on_delete=models.PROTECT)
    shipment_number = models.CharField(max_length=100)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RECEIVED)
    received_at = models.DateTimeField()

    def __str__(self) -> str:
        return self.shipment_number

class MatrixShipmentItem(TenantModel):
    """Itens do Lote da Matriz"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    matrix_shipment = models.ForeignKey(MatrixShipment, on_delete=models.CASCADE)
    global_part = models.ForeignKey(GlobalPart, on_delete=models.PROTECT)
    quantity_expected = models.IntegerField()

    def __str__(self) -> str:
        return f"{self.global_part.sku} ({self.quantity_expected})"

class Worker(TenantModel):
    """Trabalhador Doméstico / Naishokusha"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=50)
    address_line = models.CharField(max_length=500)
    geolocation = models.CharField(max_length=100, blank=True, null=True)
    geo_updated_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return self.name

class ReusableQRCode(TenantModel):
    """QR Codes Reutilizáveis/Plastificados"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=50)
    is_active = models.BooleanField(default=False)

    def __str__(self) -> str:
        return self.code

class NaishokuJobOrder(TenantModel):
    """Ordem de Serviço Doméstica"""
    class Status(models.TextChoices):
        ASSIGNED = 'ASSIGNED', 'Alocado'
        IN_PRODUCTION = 'IN_PRODUCTION', 'Em Produção'
        READY_DELIVERING = 'READY_DELIVERING', 'Pronto para Entrega'
        READY_PICKUP = 'READY_PICKUP', 'Pronto para Coleta'
        COLLECTING = 'COLLECTING', 'Coletando'
        IN_INSPECTION = 'IN_INSPECTION', 'Em Inspeção'
        FINALIZED = 'FINALIZED', 'Finalizado'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reusable_qr_code = models.ForeignKey(ReusableQRCode, on_delete=models.SET_NULL, null=True, blank=True)
    matrix_shipment_item = models.ForeignKey(MatrixShipmentItem, on_delete=models.PROTECT)
    worker = models.ForeignKey(Worker, on_delete=models.PROTECT)
    incremental_id = models.IntegerField(null=True, blank=True)
    short_audit_code = models.CharField(max_length=100)
    quantity_assigned = models.IntegerField()
    payout_per_unit = models.DecimalField(max_digits=10, decimal_places=1)  # 1 casa decimal conforme feedback do usuário
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ASSIGNED)
    assigned_at = models.DateTimeField()
    deadline = models.DateField()

    # Campos de inspeção física
    quantity_approved = models.IntegerField(default=0)
    quantity_refused = models.IntegerField(default=0)
    quantity_lost = models.IntegerField(default=0)

    @property
    def total_payout(self) -> Decimal:
        return Decimal(self.quantity_approved) * self.payout_per_unit

    def clean(self) -> None:
        super().clean()
        
        # C1: Trava de alocação (Soma das ordens ativas não pode exceder o item de lote)
        from django.db.models import Sum
        qs = NaishokuJobOrder.objects.filter(matrix_shipment_item=self.matrix_shipment_item)
        if self.pk:
            qs = qs.exclude(pk=self.pk)
        total_assigned = qs.aggregate(total=Sum('quantity_assigned'))['total'] or 0
        if self.quantity_assigned is not None and self.matrix_shipment_item is not None:
            if total_assigned + self.quantity_assigned > self.matrix_shipment_item.quantity_expected:
                raise ValidationError("A quantidade total alocada excede o esperado pelo lote da Matriz.")

        # C2: Equação de retorno e fechamento
        if self.status in [self.Status.IN_INSPECTION, self.Status.FINALIZED]:
            total_physical = self.quantity_approved + self.quantity_refused + self.quantity_lost
            if self.quantity_assigned is not None and total_physical != self.quantity_assigned:
                raise ValidationError(
                    f"Inconsistência matemática: Quantidade Distribuída ({self.quantity_assigned}) "
                    f"deve ser igual à soma de Aprovadas ({self.quantity_approved}), "
                    f"Refugos ({self.quantity_refused}) e Perdas ({self.quantity_lost}). Total atual: {total_physical}."
                )

        # Regra de antiduplicação para QR Codes reutilizáveis ativos
        if self.reusable_qr_code:
            active_orders = NaishokuJobOrder.objects.filter(
                reusable_qr_code=self.reusable_qr_code
            ).exclude(status=self.Status.FINALIZED)
            if self.pk:
                active_orders = active_orders.exclude(pk=self.pk)
            if active_orders.exists():
                raise ValidationError("Este QR Code já está associado a uma ordem de serviço ativa.")

    def save(self, *args: Any, **kwargs: Any) -> None:
        # Garante que tenant_id_id está setado antes de qualquer processamento
        if not getattr(self, "tenant_id_id", None):
            current_tenant = get_tenant_context()
            if current_tenant:
                self.tenant_id_id = current_tenant

        # Gera o incremental_id e o short_audit_code caso não existam
        if not self.short_audit_code:
            tenant_name = "TEMP"
            if getattr(self, "tenant_id_id", None):
                try:
                    tenant_name = self.tenant_id.corporate_name
                except Exception:
                    pass
            prefix = "".join([c for c in tenant_name if c.isalnum()]).upper()[:4]
            if not prefix:
                prefix = "NMS"
            
            from django.db.models import Max
            max_val = NaishokuJobOrder.objects.filter(tenant_id_id=self.tenant_id_id).aggregate(Max('incremental_id'))['incremental_id__max'] or 0
            self.incremental_id = max_val + 1
            
            base_code = f"{prefix}-{self.incremental_id}"
            checksum = calculate_checksum(base_code)
            self.short_audit_code = f"{base_code}-{checksum}"

        old_qr_code = None
        if self.pk:
            try:
                old_self = NaishokuJobOrder.objects.get(pk=self.pk)
                if old_self.reusable_qr_code != self.reusable_qr_code:
                    old_qr_code = old_self.reusable_qr_code
            except NaishokuJobOrder.DoesNotExist:
                pass

        self.clean()
        super().save(*args, **kwargs)

        # Atualiza a ativação do QR Code atual
        if self.reusable_qr_code:
            self.reusable_qr_code.is_active = (self.status != self.Status.FINALIZED)
            self.reusable_qr_code.save()

        # Libera o QR Code antigo
        if old_qr_code:
            has_active = NaishokuJobOrder.objects.filter(
                reusable_qr_code=old_qr_code
            ).exclude(status=self.Status.FINALIZED).exists()
            old_qr_code.is_active = has_active
            old_qr_code.save()

class StockMovementLedger(TenantModel):
    """Diário de Bordo do Estoque - Auditoria Absoluta"""
    class MovementType(models.TextChoices):
        MATRIZ_IN = 'MATRIZ_IN', 'Entrada da Matriz'
        WORKER_DISTRIBUTION = 'WORKER_DISTRIBUTION', 'Saída para Trabalhador'
        WORKER_RETURN_APPROVED = 'WORKER_RETURN_APPROVED', 'Retorno Trabalhador (Aprovado)'
        WORKER_RETURN_REFUSE = 'WORKER_RETURN_REFUSE', 'Retorno Trabalhador (Refugado)'
        LOSS_DIVIDEND = 'LOSS_DIVIDEND', 'Perda Física / Dividendo'
        MATRIZ_RETURN = 'MATRIZ_RETURN', 'Retorno para a Matriz'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    global_part = models.ForeignKey(GlobalPart, on_delete=models.PROTECT)
    quantity = models.IntegerField()  # Positivo para entradas, negativo para saídas
    movement_type = models.CharField(max_length=30, choices=MovementType.choices)
    processed_by = models.ForeignKey(CustomUser, on_delete=models.PROTECT)
    worker = models.ForeignKey(Worker, on_delete=models.PROTECT, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @classmethod
    def get_balance(cls, part: GlobalPart, tenant: Tenant) -> int:
        from django.db.models import Sum
        res = cls.objects.filter(global_part=part).aggregate(total=Sum('quantity'))
        return int(res['total'] or 0)
