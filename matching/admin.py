"""Admin registration for the matching app."""

from django.contrib import admin

from .models import PriceCategory, Season


class PriceCategoryInline(admin.TabularInline):
    """Edit a season's price categories inline."""

    model = PriceCategory
    extra = 0


@admin.register(Season)
class SeasonAdmin(admin.ModelAdmin):
    """Admin for Season."""

    list_display = ["name", "is_active", "contact_window_hours", "created_at"]
    list_filter = ["is_active"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ["name"]}
    readonly_fields = ["created_at", "updated_at"]
    inlines = [PriceCategoryInline]


@admin.register(PriceCategory)
class PriceCategoryAdmin(admin.ModelAdmin):
    """Admin for PriceCategory."""

    list_display = ["season", "code", "order", "full_price", "discounted_price"]
    list_filter = ["season", "code"]
    ordering = ["season", "order"]
    readonly_fields = ["created_at", "updated_at"]
