(() => {
  const form = document.querySelector('[data-quantity-field]');
  if (!form) return;
  const unit = form.querySelector('[name="unit"]');
  const product = form.querySelector('[name="product"]');
  const units = JSON.parse(document.getElementById('quantity-product-units').textContent);
  const amount = form.querySelector(`[name="${form.dataset.quantityField}"]`);
  if ((!unit && !product) || !amount) return;
  const fractionStep = amount.step && amount.step !== '1' ? amount.step : '0.001';
  function update() {
    const count = (unit ? unit.value : units[product.value] || '').toLowerCase() === 'adet';
    amount.step = count ? '1' : fractionStep;
    amount.min = form.dataset.quantityField === 'minimum_stock' ? '0' : count ? '1' : fractionStep;
    if (count && amount.value && Number.isSafeInteger(Number(amount.value))) amount.value = String(Number(amount.value));
  }
  (unit || product).addEventListener('change', update);
  update();
})();
