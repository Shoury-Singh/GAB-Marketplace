from django.urls import path
from . import views

urlpatterns = [
    path('register/', views.register_view, name='register'),
    path('login/', views.login_view, name='login'),
    path('password-reset/', views.password_reset_view, name='password_reset'),
    path('logout/', views.logout_view, name='logout'),
    path('profile/', views.profile_view, name='profile'),
    path('dashboard/industry/', views.industry_dashboard, name='industry_dashboard'),
    path('dashboard/wholesaler/', views.wholesaler_dashboard, name='wholesaler_dashboard'),
    path('dashboard/shopkeeper/', views.shopkeeper_dashboard, name='shopkeeper_dashboard'),
    path('dashboard/buyer/', views.buyer_dashboard, name='buyer_dashboard'),
]