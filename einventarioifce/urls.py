from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import RedirectView

urlpatterns = [
    # Página inicial do projeto → Vistoria (blocos)
    path("", RedirectView.as_view(pattern_name="vistoria_public:blocos", permanent=False), name="home"),

    path("admin/", admin.site.urls),
    path("vistoria/", include("vistoria.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
