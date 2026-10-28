from .models import Product, Warehouse, StockMovement, Partner, Order, OrderLine, ProductionRoute, RouteStep
MODULES = {
    'urunler': (Product, 'Ürün kartları', ['code','name','unit','minimum_stock','active']),
    'depolar': (Warehouse, 'Depolar', ['name']),
    'stok': (StockMovement, 'Stok hareketleri', ['product','warehouse','kind','quantity','note','created_by','created_at']),
    'cariler': (Partner, 'Müşteri ve tedarikçiler', ['name','kind','phone','email']),
    'siparisler': (Order, 'Siparişler', ['number','kind','partner','date','status','currency','total']),
    'siparis-kalemleri': (OrderLine, 'Sipariş kalemleri', ['order','product','quantity','unit_price','total']),
    'rotalar': (ProductionRoute, 'Üretim rotaları', ['name','product','version','active']),
    'rota-adimlari': (RouteStep, 'Rota adımları', ['route','sequence','name','workstation','duration_minutes']),
}
