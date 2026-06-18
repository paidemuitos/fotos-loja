from django.urls import path
from django.contrib.auth import views as auth_views
from ops import views

urlpatterns = [
    path("login/", auth_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("go/", views.contingency_go_endpoint, name="contingency_go_endpoint"),
    path("go/<str:token>/", views.go_endpoint, name="go_endpoint"),
    path("route/", views.driver_route_view, name="driver_route_view"),
    
    # Rotas de controle de acesso de dashboards
    path("dashboard/redirect/", views.dashboard_redirect_view, name="dashboard_redirect"),
    path("dashboard/admin/", views.saas_admin_dashboard, name="saas_admin_dashboard"),
    path("dashboard/factory/", views.factory_dashboard, name="factory_dashboard"),
    path("dashboard/workers/", views.worker_list_view, name="worker_list"),
    path("dashboard/shipments/", views.shipment_list_view, name="shipment_list"),
    path("dashboard/stock/", views.stock_ledger_view, name="stock_ledger"),
    path("dashboard/jobs/new/", views.job_order_create_view, name="job_order_create"),
    path("dashboard/resolve-qr/", views.resolve_qr_code_ajax, name="resolve_qr_code_ajax"),
    path("dashboard/jobs/<uuid:pk>/print-qr/", views.print_job_qr_view, name="print_job_qr"),
    path("dashboard/jobs/<uuid:pk>/inspect/", views.job_order_inspect_view, name="job_order_inspect"),
    path("suspended/", views.suspended_page_view, name="suspended_page"),
]
