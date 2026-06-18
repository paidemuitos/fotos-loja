import uuid
from typing import Any
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.contrib.auth import get_user_model
from datetime import timedelta, date
from decimal import Decimal

from ops.models import (
    Tenant, ParentFactory, GlobalPart,
    MatrixShipment, MatrixShipmentItem, Worker, ReusableQRCode,
    NaishokuJobOrder, StockMovementLedger
)
from core.middleware.tenant import set_tenant_context, clear_tenant_context

class Command(BaseCommand):
    help = "Populates the database with realistic demo data for Tenants, Workers, and Job Orders."

    def handle(self, *args: Any, **options: Any) -> None:
        self.stdout.write("Starting database population...")

        # 1. Clean existing data (except superuser)
        StockMovementLedger.objects.all().delete()
        NaishokuJobOrder.objects.all().delete()
        ReusableQRCode.objects.all().delete()
        Worker.objects.all().delete()
        MatrixShipmentItem.objects.all().delete()
        MatrixShipment.objects.all().delete()
        GlobalPart.objects.all().delete()
        ParentFactory.objects.all().delete()

        User = get_user_model()
        User.objects.exclude(is_superuser=True).delete()
        Tenant.objects.all().delete()

        self.stdout.write("Cleaned old demo data.")

        # 2. Create Tenant A (Active Subscription)
        tenant_a = Tenant.objects.create(
            corporate_name="Costura & Cia",
            postal_code="100-0001",
            prefecture="Tokyo",
            subscription_expires_at=timezone.now() + timedelta(days=30)
        )
        # Create User for Tenant A
        user_a = User.objects.create_user(
            username="costura",
            email="costura@example.com",
            password="123",
            tenant_id=tenant_a
        )

        # Set tenant context to create tenant-specific records
        token = set_tenant_context(tenant_a.id)
        try:
            # Create Parent Factories (Tenant-Specific)
            toyota = ParentFactory.objects.create(
                name="Toyota Motors Japan",
                industry_code="IND-TOYOTA-001"
            )
            sony = ParentFactory.objects.create(
                name="Sony Electronics",
                industry_code="IND-SONY-002"
            )

            # Create Global Parts (Tenant-Specific)
            bumper = GlobalPart.objects.create(
                parent_factory=toyota,
                sku="TOY-BUMP-992",
                name="Front Bumper Clip",
                unit_weight=Decimal("15.500")
            )
            switch = GlobalPart.objects.create(
                parent_factory=sony,
                sku="SNY-SW-401",
                name="Micro Switch Controller",
                unit_weight=Decimal("2.350")
            )

            # Create Workers
            worker1 = Worker.objects.create(
                name="Maria Silva",
                phone="090-1111-2222",
                address_line="Shibuya-ku, 1-2-3",
                geolocation="35.658034,139.701636",
                geo_updated_at=timezone.now()
            )
            worker2 = Worker.objects.create(
                name="Sato Kenji",
                phone="080-3333-4444",
                address_line="Shinjuku-ku, 4-5-6",
                geolocation="35.693825,139.703491",
                geo_updated_at=timezone.now()
            )

            # Create Reusable QR Codes
            qr1 = ReusableQRCode.objects.create(code="QR-COSTURA-001", is_active=False)
            qr2 = ReusableQRCode.objects.create(code="QR-COSTURA-002", is_active=False)

            # Create Shipment & Item
            shipment_toy = MatrixShipment.objects.create(
                parent_factory=toyota,
                shipment_number="SHIP-TOY-2026-06",
                status=MatrixShipment.Status.RECEIVED,
                received_at=timezone.now()
            )
            item_toy = MatrixShipmentItem.objects.create(
                matrix_shipment=shipment_toy,
                global_part=bumper,
                quantity_expected=500
            )

            # Record Ledger Entry for Stock Input
            StockMovementLedger.objects.create(
                global_part=bumper,
                quantity=500,
                movement_type=StockMovementLedger.MovementType.MATRIZ_IN,
                processed_by=user_a
            )

            # Create Job Orders
            job1 = NaishokuJobOrder.objects.create(
                reusable_qr_code=qr1,
                matrix_shipment_item=item_toy,
                worker=worker1,
                quantity_assigned=200,
                payout_per_unit=Decimal("12.5"),
                status=NaishokuJobOrder.Status.ASSIGNED,
                assigned_at=timezone.now(),
                deadline=(timezone.now() + timedelta(days=5)).date()
            )
            
            # Record Ledger Entry for Distribution
            StockMovementLedger.objects.create(
                global_part=bumper,
                quantity=-200,
                movement_type=StockMovementLedger.MovementType.WORKER_DISTRIBUTION,
                processed_by=user_a,
                worker=worker1
            )

            job2 = NaishokuJobOrder.objects.create(
                reusable_qr_code=qr2,
                matrix_shipment_item=item_toy,
                worker=worker2,
                quantity_assigned=150,
                payout_per_unit=Decimal("12.5"),
                status=NaishokuJobOrder.Status.ASSIGNED,
                assigned_at=timezone.now(),
                deadline=(timezone.now() + timedelta(days=7)).date()
            )

            # Record Ledger Entry for Distribution
            StockMovementLedger.objects.create(
                global_part=bumper,
                quantity=-150,
                movement_type=StockMovementLedger.MovementType.WORKER_DISTRIBUTION,
                processed_by=user_a,
                worker=worker2
            )

        finally:
            clear_tenant_context(token)

        # 5. Create Tenant B (Suspended Subscription)
        tenant_b = Tenant.objects.create(
            corporate_name="Montagens Express",
            postal_code="200-0002",
            prefecture="Osaka",
            subscription_expires_at=timezone.now() - timedelta(days=2)  # Expired
        )
        # Create User for Tenant B
        user_b = User.objects.create_user(
            username="montagens",
            email="montagens@example.com",
            password="123",
            tenant_id=tenant_b
        )

        token_b = set_tenant_context(tenant_b.id)
        try:
            worker3 = Worker.objects.create(
                name="Takahashi Hana",
                phone="070-5555-6666",
                address_line="Osaka-shi, Namba 1-1",
                geolocation="34.668045,135.501345",
                geo_updated_at=timezone.now()
            )
        finally:
            clear_tenant_context(token_b)

        self.stdout.write(self.style.SUCCESS("Successfully populated demo data!"))
