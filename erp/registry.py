from .models import Product, Warehouse, StockMovement, Partner, Order, OrderLine, ProductionRoute, RouteStep, CatalogImport, MaterialRecipe, MaterialRequirement, ProductionFlow, ProductionFlowStep, ProductionJob, ProductionJobStep
from .models import AuditEvent, QuantityReview, ConsumptionRecipe, ConsumptionRecipeLine, ProductionPosting, ProductionInputSnapshot
MODULES = {
    'urunler': (Product, 'Ürün kartları', ['code','name','category','production_area','unit','minimum_stock','active']),
    'depolar': (Warehouse, 'Depolar', ['name']),
    'stok': (StockMovement, 'Stok hareketleri', ['product','warehouse','kind','quantity','note','created_by','created_at']),
    'cariler': (Partner, 'Müşteri ve tedarikçiler', ['name','kind','phone','email']),
    'siparisler': (Order, 'Siparişler', ['number','kind','partner','date','status','currency','total']),
    'siparis-kalemleri': (OrderLine, 'Sipariş kalemleri', ['order','product','quantity','unit_price','total','status','cancelled_by','cancelled_at']),
    'rotalar': (ProductionRoute, 'Üretim ve temin rotaları', ['name','product','supply_method','flow','version','active']),
    'receteler': (MaterialRecipe, 'Ürün reçeteleri', ['source','sheet','parent_code','parent_name','product']),
    'recete-satirlari': (MaterialRequirement, 'Reçete satırları ve eksikler', ['sheet','row_number','parent_code','component_code','component_name','component_kind','quantity','unit','issues']),
    'excel-aktarimlari': (CatalogImport, 'Excel aktarımları', ['filename','created_at','summary']),
    'uretim-akislari': (ProductionFlow, 'Standart üretim akışları', ['name','area']),
    'uretim-isleri': (ProductionJob, 'Üretim iş emirleri', ['number','product','quantity','unit','flow','state']),
    'rota-adimlari': (RouteStep, 'Rota adımları', ['route','sequence','name','workstation','duration_minutes']),
    'tuketim-receteleri': (ConsumptionRecipe,'Stok tüketim reçeteleri',['product','name','version','unit','active','confirmed']),
    'tuketim-girdileri': (ConsumptionRecipeLine,'Tüketim girdileri',['recipe','product','quantity','unit','active']),
    'uretim-stok-fisleri': (ProductionPosting,'Üretim stok fişleri',['job','quantity','unit','input_warehouse','output_warehouse','created_by','created_at']),
    'islem-gecmisi': (AuditEvent,'İşlem geçmişi',['actor_label','created_at','action','model_label','object_id','object_label','reason']),
    'miktar-incelemeleri': (QuantityReview,'Miktar incelemeleri',['model_label','object_id','field_name','quantity','note','detected_at','resolved_at']),
}

READ_ONLY_MODELS = {CatalogImport,MaterialRecipe,MaterialRequirement,ProductionFlow,ProductionFlowStep,ProductionJobStep,AuditEvent,QuantityReview,ProductionPosting,ProductionInputSnapshot}
