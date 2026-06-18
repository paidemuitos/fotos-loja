import uuid
import urllib.parse
from typing import Any
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden, HttpResponseRedirect, HttpResponseBadRequest, Http404
from django.core import signing
from django.contrib.auth.decorators import login_required, user_passes_test
from django.utils import timezone
from datetime import timedelta
from ops.models import (
    Tenant, NaishokuJobOrder, ReusableQRCode, Worker,
    ParentFactory, GlobalPart, MatrixShipment, MatrixShipmentItem,
    StockMovementLedger
)
from ops.forms import (
    ContingencyForm, WorkerForm, MatrixShipmentForm,
    MatrixShipmentItemForm, NaishokuJobOrderForm, JobInspectionForm,
    ParentFactoryForm, GlobalPartForm
)

def go_endpoint(request: HttpRequest, token: str) -> HttpResponse:
    """
    Endpoint público do QR Code. 
    Unsigna o token e mapeia para a ordem de serviço correspondente.
    """
    try:
        resolved_id_str = signing.loads(token)
        resolved_uuid = uuid.UUID(resolved_id_str)
    except (signing.BadSignature, ValueError, TypeError):
        return HttpResponseForbidden("Assinatura inválida")

    # Busca se é um ID direto de Ordem de Serviço
    job = NaishokuJobOrder.objects.filter(id=resolved_uuid).first()
    if job:
        return render(request, "ops/job_dashboard.html", {"job_order": job})

    # Busca se é um ID de QR Code Reutilizável
    qr = ReusableQRCode.objects.filter(id=resolved_uuid).first()
    if qr:
        # Resolve para a ordem de serviço ativa associada a esse QR Code
        active_job = NaishokuJobOrder.objects.filter(
            reusable_qr_code=qr
        ).exclude(status=NaishokuJobOrder.Status.FINALIZED).first()
        
        if active_job:
            return render(request, "ops/job_dashboard.html", {"job_order": active_job})

    raise Http404("Nenhuma ordem ativa encontrada para este QR Code.")

def contingency_go_endpoint(request: HttpRequest) -> HttpResponse:
    """
    Formulário de contingência para fallback humano por digitação de código curto.
    """
    if request.method == "POST":
        form = ContingencyForm(request.POST)
        if form.is_valid():
            short_code = form.cleaned_data["short_code"]
            # Procura a ordem pelo short_code
            job = NaishokuJobOrder.objects.filter(short_audit_code=short_code).first()
            if job:
                # Redireciona para o link assinado seguro daquela ordem
                token = signing.dumps(str(job.id))
                return redirect("go_endpoint", token=token)
            else:
                form.add_error("short_code", "Ordem de serviço correspondente não encontrada.")
    else:
        form = ContingencyForm()
    return render(request, "ops/contingency.html", {"form": form})

def driver_route_view(request: HttpRequest) -> HttpResponse:
    """
    Consolida as coordenadas dos trabalhadores indicados e gera uma intenção do Google Maps.
    """
    worker_ids_str = request.GET.get("workers", "")
    if not worker_ids_str:
        return HttpResponseBadRequest("Nenhum trabalhador selecionado.")

    try:
        worker_ids = [uuid.UUID(x.strip()) for x in worker_ids_str.split(",") if x.strip()]
    except (ValueError, TypeError):
        return HttpResponseBadRequest("Lista de IDs inválida.")

    workers = Worker.objects.filter(id__in=worker_ids)
    coords = []
    for w in workers:
        if w.geolocation:
            coords.append(w.geolocation.strip())

    if not coords:
        return HttpResponseBadRequest("Nenhum trabalhador possui coordenadas de geolocalização válidas.")

    # A última coordenada é o destino final, e as anteriores são os waypoints intermediários
    destination = coords[-1]
    waypoints = "|".join(coords[:-1])

    params = {
        "api": "1",
        "destination": destination,
    }
    if waypoints:
        params["waypoints"] = waypoints

    url = "https://www.google.com/maps/dir/?" + urllib.parse.urlencode(params)
    return HttpResponseRedirect(url)

@login_required
def dashboard_redirect_view(request: HttpRequest) -> HttpResponse:
    """Roteia o usuário pós-login para o painel correto com base no nível de acesso."""
    user: Any = request.user
    if user.is_superuser:
        return redirect("saas_admin_dashboard")
    elif getattr(user, "tenant_id", None):
        return redirect("factory_dashboard")
    return redirect("contingency_go_endpoint")

@login_required
def suspended_page_view(request: HttpRequest) -> HttpResponse:
    """Página de aviso amigável exibida para Tenants com assinaturas suspensas."""
    user: Any = request.user
    # Se o tenant regularizou a assinatura, libera o retorno para o dashboard
    if not user.is_superuser and user.tenant_id and user.tenant_id.is_subscription_active():
        return redirect("factory_dashboard")
    return render(request, "ops/suspended.html")

@login_required
@user_passes_test(lambda u: u.is_superuser)
def saas_admin_dashboard(request: HttpRequest) -> HttpResponse:
    """Dashboard de controle SaaS exclusivo do Superadmin (CRUD, adição de validade, cadastro de matrizes/peças)."""
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "create_tenant":
            corp_name = request.POST.get("corporate_name", "").strip()
            postal_code = request.POST.get("postal_code", "").strip()
            pref = request.POST.get("prefecture", "").strip()
            if corp_name:
                Tenant.objects.create(
                    corporate_name=corp_name,
                    postal_code=postal_code,
                    prefecture=pref,
                    subscription_expires_at=timezone.now() + timedelta(days=30)
                )
            return redirect("saas_admin_dashboard")
            
        elif action == "credit_subscription":
            tenant_uuid = request.POST.get("tenant_uuid", "")
            days_str = request.POST.get("days", "0")
            try:
                days = int(days_str)
                tenant = Tenant.objects.get(id=uuid.UUID(tenant_uuid))
                current_expiry = tenant.subscription_expires_at
                if not current_expiry or current_expiry < timezone.now():
                    current_expiry = timezone.now()
                tenant.subscription_expires_at = current_expiry + timedelta(days=days)
                tenant.save()
            except (ValueError, Tenant.DoesNotExist):
                pass
            return redirect("saas_admin_dashboard")

    tenants = Tenant.objects.all().order_by("corporate_name")
    
    context = {
        "tenants": tenants,
    }
    return render(request, "ops/admin_dashboard.html", context)

@login_required
def factory_dashboard(request: HttpRequest) -> HttpResponse:
    """Dashboard operacional da fábrica para gerenciar ordens de serviço, trabalhadores e coletas."""
    user: Any = request.user
    if not user.is_superuser and user.tenant_id:
        if not user.tenant_id.is_subscription_active():
            return redirect("suspended_page")

    orders = NaishokuJobOrder.objects.all().order_by("-assigned_at")
    active_orders_count = orders.exclude(status=NaishokuJobOrder.Status.FINALIZED).count()
    workers_count = Worker.objects.count()

    global_parts = GlobalPart.objects.all().order_by("sku")
    stock_summary = []
    for gp in global_parts:
        balance = StockMovementLedger.get_balance(gp, user.tenant_id)
        stock_summary.append({
            "part": gp,
            "balance": balance
        })
    
    context = {
        "job_orders": orders[:10],
        "active_orders_count": active_orders_count,
        "workers_count": workers_count,
        "stock_summary": stock_summary,
        "tenant": user.tenant_id,
    }
    return render(request, "ops/factory_dashboard.html", context)

@login_required
def worker_list_view(request: HttpRequest) -> HttpResponse:
    """Listagem e cadastro de trabalhadores (Workers) do tenant logado."""
    user: Any = request.user
    if not user.is_superuser and user.tenant_id:
        if not user.tenant_id.is_subscription_active():
            return redirect("suspended_page")

    if request.method == "POST":
        form = WorkerForm(request.POST)
        if form.is_valid():
            worker = form.save(commit=False)
            worker.tenant_id = user.tenant_id
            worker.save()
            return redirect("worker_list")
    else:
        form = WorkerForm()

    workers = Worker.objects.all().order_by("name")
    return render(request, "ops/worker_list.html", {"workers": workers, "form": form, "tenant": user.tenant_id})

@login_required
def shipment_list_view(request: HttpRequest) -> HttpResponse:
    """Listagem de lotes da matriz e recebimento de novos insumos."""
    user: Any = request.user
    if not user.is_superuser and user.tenant_id:
        if not user.tenant_id.is_subscription_active():
            return redirect("suspended_page")

    parent_factory_form = ParentFactoryForm()
    global_part_form = GlobalPartForm()
    shipment_form = MatrixShipmentForm()
    item_form = MatrixShipmentItemForm()
    active_tab = "lote"

    if request.method == "POST":
        action = request.POST.get("action", "receive_shipment")
        if action == "create_parent_factory":
            parent_factory_form = ParentFactoryForm(request.POST)
            if parent_factory_form.is_valid():
                parent_factory = parent_factory_form.save(commit=False)
                parent_factory.tenant_id = user.tenant_id
                parent_factory.save()
                return redirect("shipment_list")
            else:
                active_tab = "matriz"
        elif action == "create_global_part":
            global_part_form = GlobalPartForm(request.POST, request.FILES)
            if global_part_form.is_valid():
                global_part = global_part_form.save(commit=False)
                global_part.tenant_id = user.tenant_id
                global_part.save()
                return redirect("shipment_list")
            else:
                active_tab = "peca"
        elif action == "receive_shipment":
            shipment_form = MatrixShipmentForm(request.POST)
            item_form = MatrixShipmentItemForm(request.POST)
            if shipment_form.is_valid() and item_form.is_valid():
                shipment = shipment_form.save(commit=False)
                shipment.tenant_id = user.tenant_id
                shipment.save()

                item = item_form.save(commit=False)
                item.tenant_id = user.tenant_id
                item.matrix_shipment = shipment
                item.save()

                # Registra no diário de bordo do estoque (entrada de material)
                StockMovementLedger.objects.create(
                    global_part=item.global_part,
                    quantity=item.quantity_expected,
                    movement_type=StockMovementLedger.MovementType.MATRIZ_IN,
                    processed_by=user
                )

                return redirect("shipment_list")
            else:
                active_tab = "lote"

    shipments = MatrixShipment.objects.all().order_by("-received_at")
    parent_factories = ParentFactory.objects.all().order_by("name")
    global_parts = GlobalPart.objects.all().order_by("sku")

    return render(request, "ops/shipment_list.html", {
        "shipments": shipments,
        "shipment_form": shipment_form,
        "item_form": item_form,
        "parent_factory_form": parent_factory_form,
        "global_part_form": global_part_form,
        "parent_factories": parent_factories,
        "global_parts": global_parts,
        "tenant": user.tenant_id,
        "active_tab": active_tab
    })

@login_required
def stock_ledger_view(request: HttpRequest) -> HttpResponse:
    """Extrato e saldo do diário de bordo do estoque."""
    user: Any = request.user
    if not user.is_superuser and user.tenant_id:
        if not user.tenant_id.is_subscription_active():
            return redirect("suspended_page")

    global_parts = GlobalPart.objects.all().order_by("sku")
    parts_balance = []
    for gp in global_parts:
        balance = StockMovementLedger.get_balance(gp, user.tenant_id)
        parts_balance.append({
            "part": gp,
            "balance": balance
        })

    movements = StockMovementLedger.objects.all().order_by("-created_at")
    return render(request, "ops/stock_ledger.html", {
        "parts_balance": parts_balance,
        "movements": movements,
        "tenant": user.tenant_id
    })

@login_required
def job_order_create_view(request: HttpRequest) -> HttpResponse:
    """Criação de nova ordem de serviço (Alocação de Trabalho)."""
    user: Any = request.user
    if not user.is_superuser and user.tenant_id:
        if not user.tenant_id.is_subscription_active():
            return redirect("suspended_page")

    from django.db.models import Sum
    shipment_items = MatrixShipmentItem.objects.all().select_related('global_part', 'matrix_shipment')
    shipment_items_data = []
    for item in shipment_items:
        allocated = NaishokuJobOrder.objects.filter(matrix_shipment_item=item).aggregate(total=Sum('quantity_assigned'))['total'] or 0
        remaining = item.quantity_expected - allocated
        shipment_items_data.append({
            "item": item,
            "remaining_quantity": remaining
        })

    if request.method == "POST":
        form = NaishokuJobOrderForm(request.POST)
        if form.is_valid():
            job = form.save(commit=False)
            job.tenant_id = user.tenant_id
            job.assigned_at = timezone.now()

            generate_print = request.POST.get("action_generate_print") == "true"
            if not job.reusable_qr_code:
                if generate_print:
                    tenant_name = user.tenant_id.corporate_name if user.tenant_id else "NMS"
                    tenant_prefix = "".join([c for c in tenant_name if c.isalnum()]).upper()[:4]
                    if not tenant_prefix:
                        tenant_prefix = "NMS"
                    prefix_full = f"QR-{tenant_prefix}-"
                    
                    existing_codes = ReusableQRCode.objects.filter(code__startswith=prefix_full)
                    max_num = 0
                    for qr in existing_codes:
                        try:
                            num_str = qr.code[len(prefix_full):]
                            num = int(num_str)
                            if num > max_num:
                                max_num = num
                        except (ValueError, TypeError):
                            pass
                    next_num = max_num + 1
                    new_code = f"{prefix_full}{next_num:03d}"
                    
                    new_qr = ReusableQRCode.objects.create(
                        code=new_code,
                        is_active=True
                    )
                    job.reusable_qr_code = new_qr
                else:
                    form.add_error("reusable_qr_code", "QR Code é obrigatório ou selecione gerar e imprimir.")
                    return render(request, "ops/job_order_create.html", {
                        "form": form,
                        "tenant": user.tenant_id,
                        "shipment_items_data": shipment_items_data
                    })

            job.save()

            # Registra no diário de bordo (saída do estoque da fábrica para o trabalhador)
            StockMovementLedger.objects.create(
                global_part=job.matrix_shipment_item.global_part,
                quantity=-job.quantity_assigned,
                movement_type=StockMovementLedger.MovementType.WORKER_DISTRIBUTION,
                processed_by=user,
                worker=job.worker
            )

            if generate_print:
                return redirect("print_job_qr", pk=job.pk)

            return redirect("factory_dashboard")
    else:
        form = NaishokuJobOrderForm()

    return render(request, "ops/job_order_create.html", {
        "form": form,
        "tenant": user.tenant_id,
        "shipment_items_data": shipment_items_data
    })


@login_required
def print_job_qr_view(request: HttpRequest, pk: uuid.UUID) -> HttpResponse:
    """
    Exibe uma página pronta para impressão com o QR Code gerado.
    """
    user: Any = request.user
    if not user.is_superuser and user.tenant_id:
        if not user.tenant_id.is_subscription_active():
            return redirect("suspended_page")

    job = get_object_or_404(NaishokuJobOrder, pk=pk)
    if not job.reusable_qr_code:
        return HttpResponseBadRequest("Esta ordem de serviço não possui um QR Code associado.")

    # Gera o token do ReusableQRCode
    token = signing.dumps(str(job.reusable_qr_code.id))
    qr_link = request.build_absolute_uri(reverse("go_endpoint", kwargs={"token": token}))

    # URL da API externa para gerar a imagem do QR Code
    quoted_link = urllib.parse.quote(qr_link)
    qr_image_url = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={quoted_link}"

    context = {
        "job": job,
        "qr_code": job.reusable_qr_code,
        "qr_image_url": qr_image_url,
        "qr_link": qr_link,
        "tenant": user.tenant_id
    }
    return render(request, "ops/print_qr.html", context)

@login_required
def job_order_inspect_view(request: HttpRequest, pk: uuid.UUID) -> HttpResponse:
    """Inspeção física e finalização de uma ordem de serviço de costura/montagem."""
    user: Any = request.user
    if not user.is_superuser and user.tenant_id:
        if not user.tenant_id.is_subscription_active():
            return redirect("suspended_page")

    job = get_object_or_404(NaishokuJobOrder, pk=pk)

    if request.method == "POST":
        form = JobInspectionForm(job.quantity_assigned, request.POST)
        if form.is_valid():
            job.quantity_approved = form.cleaned_data["quantity_approved"]
            job.quantity_refused = form.cleaned_data["quantity_refused"]
            job.quantity_lost = form.cleaned_data["quantity_lost"]
            job.status = NaishokuJobOrder.Status.FINALIZED
            job.save()

            # Registra a devolução das peças aprovadas no estoque
            if job.quantity_approved > 0:
                StockMovementLedger.objects.create(
                    global_part=job.matrix_shipment_item.global_part,
                    quantity=job.quantity_approved,
                    movement_type=StockMovementLedger.MovementType.WORKER_RETURN_APPROVED,
                    processed_by=user,
                    worker=job.worker
                )

            # Registra a devolução das peças refugadas no estoque
            if job.quantity_refused > 0:
                StockMovementLedger.objects.create(
                    global_part=job.matrix_shipment_item.global_part,
                    quantity=job.quantity_refused,
                    movement_type=StockMovementLedger.MovementType.WORKER_RETURN_REFUSE,
                    processed_by=user,
                    worker=job.worker
                )

            # Registra a perda física no ledger para documentação (quantidade líquida 0 pois já foi descontado)
            if job.quantity_lost > 0:
                StockMovementLedger.objects.create(
                    global_part=job.matrix_shipment_item.global_part,
                    quantity=0,
                    movement_type=StockMovementLedger.MovementType.LOSS_DIVIDEND,
                    processed_by=user,
                    worker=job.worker
                )

            return redirect("factory_dashboard")
    else:
        form = JobInspectionForm(job.quantity_assigned, initial={
            "quantity_approved": job.quantity_assigned,
            "quantity_refused": 0,
            "quantity_lost": 0
        })

    return render(request, "ops/job_order_inspect.html", {"form": form, "job_order": job, "tenant": user.tenant_id})

from django.http import JsonResponse
import urllib.parse

@login_required
def resolve_qr_code_ajax(request: HttpRequest) -> HttpResponse:
    """Recebe uma URL ou token de QR Code e resolve para o ID correspondente do ReusableQRCode."""
    token = request.GET.get("token", "")
    url = request.GET.get("url", "")
    
    if url and not token:
        try:
            path_str = urllib.parse.urlparse(url).path
            parts = [p for p in path_str.split("/") if p]
            if len(parts) >= 1:
                token = parts[-1]
        except Exception:
            pass

    if not token:
        return JsonResponse({"success": False, "error": "Token não fornecido"}, status=400)

    try:
        resolved_id_str = signing.loads(token)
        resolved_uuid = uuid.UUID(resolved_id_str)
    except (signing.BadSignature, ValueError, TypeError):
        return JsonResponse({"success": False, "error": "Assinatura inválida no QR Code"}, status=400)

    # Busca o QR Code reutilizável
    qr = ReusableQRCode.objects.filter(id=resolved_uuid).first()
    if not qr:
        return JsonResponse({"success": False, "error": "QR Code não encontrado ou pertence a outro tenant"}, status=404)

    # Verifica se ele já está associado a uma ordem ativa
    active_order = NaishokuJobOrder.objects.filter(
        reusable_qr_code=qr
    ).exclude(status=NaishokuJobOrder.Status.FINALIZED).first()
    
    if active_order:
        return JsonResponse({
            "success": False, 
            "error": f"Este QR Code já está associado a uma ordem ativa (OS #{active_order.short_audit_code})"
        }, status=400)

    return JsonResponse({
        "success": True,
        "id": str(qr.id),
        "code": qr.code
    })
