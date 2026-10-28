from .models import Product, Warehouse, StockMovement, Partner, Order, OrderLine, ProductionRoute, RouteStep, CatalogImport, MaterialRecipe, MaterialRequirement, ProductionFlow, ProductionFlowStep, ProductionJob, ProductionJobStep
MODULES = {
    'urunler': (Product, 'Ürün kartları', ['code','name','kind','production_area','category','unit','minimum_stock','active']),
    'depolar': (Warehouse, 'Depolar', ['name']),
    'stok': (StockMovement, 'Stok hareketleri', ['product','warehouse','kind','quantity','note','created_by','created_at']),
    'cariler': (Partner, 'Müşteri ve tedarikçiler', ['name','kind','phone','email']),
    'siparisler': (Order, 'Siparişler', ['number','kind','partner','date','status','currency','total']),
    'siparis-kalemleri': (OrderLine, 'Sipariş kalemleri', ['order','product','quantity','unit_price','total']),
    'rotalar': (ProductionRoute, 'Üretim rotaları', ['name','product','version','active']),
    'receteler': (MaterialRecipe, 'Ürün reçeteleri', ['source','sheet','parent_code','parent_name','product']),
    'recete-satirlari': (MaterialRequirement, 'Reçete satırları ve eksikler', ['sheet','row_number','parent_code','component_code','component_name','component_kind','quantity','unit','issues']),
    'excel-aktarimlari': (CatalogImport, 'Excel aktarımları', ['filename','created_at','summary']),
    'uretim-akislari': (ProductionFlow, 'Standart üretim akışları', ['name','area']),
    'uretim-isleri': (ProductionJob, 'Üretim iş emirleri', ['number','product','quantity','unit','flow','state']),
    'rota-adimlari': (RouteStep, 'Rota adımları', ['route','sequence','name','workstation','duration_minutes']),
}

READ_ONLY_MODELS = {CatalogImport,MaterialRecipe,MaterialRequirement,ProductionFlow,ProductionFlowStep,ProductionJobStep}
