"""Shared main-product details for product and stock card screens."""
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.urls import reverse

from .models import MaterialRecipe
from .production import material_plan
from .routing import is_grill_category, is_semifinished_component


def product_detail_context(request, product, *, stock_card=False):
    group_names = {r.sheet for r in product.materialrecipe_set.all() if r.sheet}
    if product.category:
        group_names.add(product.category)
    can_view_components = all(request.user.has_perm('erp.' + permission) for permission in (
        'view_materialrecipe', 'view_materialrequirement',
    ))
    can_view_stock = stock_card and all(request.user.has_perm('erp.' + permission) for permission in (
        'view_stockmovement', 'view_warehouse',
    ))
    tab = request.GET.get('tab', 'components' if can_view_components else 'overview')
    allowed_tabs = ('overview', 'components', 'stock') if stock_card else ('overview', 'components')
    if tab not in allowed_tabs:
        raise Http404
    if (tab == 'components' and not can_view_components) or (tab == 'stock' and not can_view_stock):
        raise PermissionDenied
    recipes = []
    selected_recipe = None
    sections = []
    if can_view_components:
        recipes = list(MaterialRecipe.objects.filter(product=product).select_related('source').order_by('-source_id', 'sheet', 'pk'))
        selected = request.GET.get('recipe', '')
        if selected:
            selected_recipe = next((r for r in recipes if str(r.pk) == selected), None)
            if selected_recipe is None:
                raise Http404
        elif recipes:
            selected_recipe = recipes[0]
        if selected_recipe and tab == 'components':
            rows, _ = material_plan(selected_recipe, Decimal('1'))
            sections = [
                {'title': 'Yarı mamuller', 'rows': [r for r in rows if is_semifinished_component(r['line'].component_kind, r['line'].sheet)]},
                {'title': 'Hammaddeler', 'rows': [r for r in rows if r['line'].component_kind == 'Hammadde']},
                {'title': 'Diğer bileşenler', 'rows': [r for r in rows if r['line'].component_kind != 'Hammadde' and not is_semifinished_component(r['line'].component_kind, r['line'].sheet)]},
            ]
    return {
        'product': product, 'tab': tab, 'can_view_components': can_view_components,
        'recipes': recipes, 'selected_recipe': selected_recipe, 'sections': sections,
        'slug': 'stok-kartlari' if stock_card else 'urunler',
        'card_list_title': 'Stok kartları' if stock_card else 'Ürün kartları',
        'card_edit_title': 'Stok kartını düzenle' if stock_card else 'Ürün kartını düzenle',
        'detail_url': reverse('stock_card_detail' if stock_card else 'product_detail', args=[product.pk]),
        'display_group': ', '.join(sorted(group_names)) or '—',
        'is_grill': any(is_grill_category(name) for name in group_names),
        'direct_routes': product.productionroute_set.filter(active=True).select_related('flow') if request.user.has_perm('erp.view_productionroute') else [],
        'embedded': request.headers.get('X-Product-Tab') == '1',
        'stock_card': stock_card, 'can_view_stock': can_view_stock,
    }
