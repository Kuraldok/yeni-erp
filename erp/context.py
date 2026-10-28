from .registry import MODULES

def navigation(request):
    return {'navigation': [{'slug': slug, 'title': title} for slug, (model, title, _) in MODULES.items() if request.user.has_perm(f'erp.view_{model._meta.model_name}')]}
