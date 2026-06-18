from django import forms
from django.core.exceptions import ValidationError
from django.db import models
from typing import Any, TYPE_CHECKING
from ops.models import (
    verify_short_code, Worker, MatrixShipment, MatrixShipmentItem,
    NaishokuJobOrder, ReusableQRCode, ParentFactory, GlobalPart
)

if TYPE_CHECKING:
    WorkerModelForm = forms.ModelForm[Worker]
    ShipmentModelForm = forms.ModelForm[MatrixShipment]
    ShipmentItemModelForm = forms.ModelForm[MatrixShipmentItem]
    JobOrderModelForm = forms.ModelForm[NaishokuJobOrder]
    ParentFactoryModelForm = forms.ModelForm[ParentFactory]
    GlobalPartModelForm = forms.ModelForm[GlobalPart]
else:
    WorkerModelForm = forms.ModelForm
    ShipmentModelForm = forms.ModelForm
    ShipmentItemModelForm = forms.ModelForm
    JobOrderModelForm = forms.ModelForm
    ParentFactoryModelForm = forms.ModelForm
    GlobalPartModelForm = forms.ModelForm


class ContingencyForm(forms.Form):
    short_code = forms.CharField(
        max_length=100,
        label="Código Curto",
        widget=forms.TextInput(attrs={"placeholder": "Ex: SUZU-1452-K"})
    )

    def clean_short_code(self) -> str:
        code = str(self.cleaned_data["short_code"]).strip()
        if not verify_short_code(code):
            raise ValidationError("Código inválido ou dígito verificador corrompido.")
        return code

class WorkerForm(WorkerModelForm):
    class Meta:
        model = Worker
        fields = ['name', 'phone', 'address_line', 'geolocation']
        labels = {
            'name': 'Nome do Trabalhador',
            'phone': 'Telefone',
            'address_line': 'Endereço',
            'geolocation': 'Geolocalização (Lat, Long)',
        }
        widgets = {
            'geolocation': forms.TextInput(attrs={'placeholder': 'Ex: -23.550520,-46.633308'}),
        }

    def clean_geolocation(self) -> str:
        val = self.cleaned_data.get("geolocation")
        if not val:
            return ""
        val = str(val).strip()
        parts = val.split(",")
        if len(parts) != 2:
            raise ValidationError("Formato inválido. Use 'latitude,longitude' (ex: -23.550520,-46.633308).")
        try:
            float(parts[0].strip())
            float(parts[1].strip())
        except ValueError:
            raise ValidationError("Coordenadas devem ser números válidos.")
        return val

class ParentFactoryForm(ParentFactoryModelForm):
    class Meta:
        model = ParentFactory
        fields = ['name', 'industry_code']
        labels = {
            'name': 'Nome da Matriz',
            'industry_code': 'Código de Registro Industrial',
        }

class GlobalPartForm(GlobalPartModelForm):
    class Meta:
        model = GlobalPart
        fields = ['parent_factory', 'sku', 'name', 'blueprint_file', 'part_photo']
        labels = {
            'parent_factory': 'Matriz Relacionada',
            'sku': 'SKU da Peça',
            'name': 'Nome do Componente',
            'blueprint_file': 'Desenho Técnico / Manual (Foto/Arquivo)',
            'part_photo': 'Foto da Peça (Foto/Arquivo)',
        }
        widgets = {
            'blueprint_file': forms.FileInput(attrs={'accept': 'image/*,application/pdf'}),
            'part_photo': forms.FileInput(attrs={'accept': 'image/*'}),
        }

class MatrixShipmentForm(ShipmentModelForm):
    class Meta:
        model = MatrixShipment
        fields = ['parent_factory', 'shipment_number', 'received_at']
        labels = {
            'parent_factory': 'Matriz Industrial',
            'shipment_number': 'Número do Lote (Kanban)',
            'received_at': 'Data e Hora de Recebimento',
        }
        widgets = {
            'received_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
        }

class MatrixShipmentItemForm(ShipmentItemModelForm):
    class Meta:
        model = MatrixShipmentItem
        fields = ['global_part', 'quantity_expected']
        labels = {
            'global_part': 'Componente / Peça Global',
            'quantity_expected': 'Quantidade Esperada',
        }

class NaishokuJobOrderForm(JobOrderModelForm):
    class Meta:
        model = NaishokuJobOrder
        fields = ['reusable_qr_code', 'matrix_shipment_item', 'worker', 'quantity_assigned', 'payout_per_unit', 'deadline']
        labels = {
            'reusable_qr_code': 'QR Code Reutilizável',
            'matrix_shipment_item': 'Item de Lote Matriz',
            'worker': 'Trabalhador Doméstico',
            'quantity_assigned': 'Quantidade Distribuída',
            'payout_per_unit': 'Tarifa Unitária (Ienes)',
            'deadline': 'Prazo',
        }
        widgets = {
            'deadline': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        qr_qs = ReusableQRCode.objects.filter(is_active=False)
        if self.instance and self.instance.pk and self.instance.reusable_qr_code:
            qr_qs = ReusableQRCode.objects.filter(
                models.Q(is_active=False) | models.Q(pk=self.instance.reusable_qr_code.pk)
            )
        qr_field = self.fields['reusable_qr_code']
        if isinstance(qr_field, forms.ModelChoiceField):
            qr_field.queryset = qr_qs

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean()
        if cleaned_data is None:
            cleaned_data = {}
        quantity_assigned = cleaned_data.get('quantity_assigned')
        item = cleaned_data.get('matrix_shipment_item')

        if quantity_assigned is not None and item is not None:
            from django.db.models import Sum
            qs = NaishokuJobOrder.objects.filter(matrix_shipment_item=item)
            if self.instance and self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            total_assigned = qs.aggregate(total=Sum('quantity_assigned'))['total'] or 0
            if total_assigned + quantity_assigned > item.quantity_expected:
                self.add_error('quantity_assigned', "A quantidade total alocada excede o esperado pelo lote da Matriz.")

        return cleaned_data

class JobInspectionForm(forms.Form):
    quantity_approved = forms.IntegerField(min_value=0, label="Peças Aprovadas")
    quantity_refused = forms.IntegerField(min_value=0, label="Refugo (Defeito)")
    quantity_lost = forms.IntegerField(min_value=0, label="Perda Física")

    def __init__(self, quantity_assigned: int, *args: Any, **kwargs: Any) -> None:
        self.quantity_assigned = quantity_assigned
        super().__init__(*args, **kwargs)

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean()
        if cleaned_data is None:
            cleaned_data = {}
        approved = cleaned_data.get('quantity_approved', 0)
        refused = cleaned_data.get('quantity_refused', 0)
        lost = cleaned_data.get('quantity_lost', 0)

        if (approved + refused + lost) != self.quantity_assigned:
            raise ValidationError(
                f"Inconsistência matemática: A soma de Aprovadas ({approved}), "
                f"Refugadas ({refused}) e Perdidas ({lost}) deve ser igual a "
                f"distribuída original ({self.quantity_assigned}). Total atual: {approved + refused + lost}."
            )
        return cleaned_data
