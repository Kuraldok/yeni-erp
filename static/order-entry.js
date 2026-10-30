(() => {
  const editor = document.querySelector('[data-order-editor]');
  if (!editor) return;
  const source = editor.querySelector(editor.dataset.mode === 'order' ? '#id_kind' : '#id_order');
  const group = editor.querySelector('#order-product-group');
  const status = editor.querySelector('[data-selection-status]');
  const save = editor.querySelector('[data-save-order]');
  const add = editor.querySelector('[data-add-line]');
  const body = editor.querySelector('[data-order-lines]');
  const total = editor.querySelector('#id_lines-TOTAL_FORMS');
  const maximum = editor.querySelector('#id_lines-MAX_NUM_FORMS');
  let selection = JSON.parse(document.querySelector('#order-selection-data').textContent);
  let version = 0;
  let controller;
  const fields = () => [...editor.querySelectorAll('select[name="product"], select[name$="-product"]')];
  const validKind = () => ['sale', 'purchase'].includes(selection.kind);

  function updateUnits(field) {
    const product = selection.products.find(item => String(item.id) === field.value);
    const unit = (field.closest('.order-line-row') || field.closest('.field')).querySelector('[data-product-unit]');
    if (unit) unit.textContent = product ? product.unit : '';
  }

  function updateChoices(field) {
    const selected = field.value;
    const options = selection.products.filter(product => {
      if (String(product.id) === selected) return true;
      if (!group.value) return true;
      return group.value === '__none__' ? !product.groups.length : product.groups.includes(group.value);
    });
    field.replaceChildren(new Option('Seçiniz', ''));
    for (const product of options) {
      field.add(new Option(`${product.label} · ${product.unit}`, String(product.id)));
    }
    field.value = selection.products.some(product => String(product.id) === selected) ? selected : '';
    field.disabled = !validKind();
    updateUnits(field);
  }

  function updateGroups() {
    const selected = group.value;
    group.replaceChildren(new Option('Tüm ürün grupları', ''));
    for (const name of selection.groups) group.add(new Option(name, name));
    group.add(new Option('Grubu belirtilmeyenler', '__none__'));
    group.value = selected === '__none__' || selection.groups.includes(selected) ? selected : '';
  }

  function showSelection() {
    fields().forEach(updateChoices);
    if (!validKind()) status.textContent = 'Önce sipariş türünü veya taslak siparişi seçin.';
    else if (!selection.products.length) status.textContent = 'Bu türde seçilebilecek ürün kaydı yok.';
    else status.textContent = selection.kind === 'sale' ? 'Satış: yalnızca ana ürünler seçilebilir.' : 'Satın alma: yalnızca hammaddeler seçilebilir.';
  }

  async function reloadProducts() {
    const requestVersion = ++version;
    if (controller) controller.abort();
    if (!source.value) {
      selection = {kind: '', products: [], groups: []};
      updateGroups();
      showSelection();
      save.disabled = false;
      if (add) add.disabled = false;
      return;
    }
    controller = new AbortController();
    fields().forEach(field => { field.disabled = true; });
    save.disabled = true;
    if (add) add.disabled = true;
    status.textContent = 'Ürünler yükleniyor…';
    try {
      const url = new URL(editor.dataset.optionsUrl, window.location.origin);
      url.searchParams.set(editor.dataset.mode === 'order' ? 'kind' : 'order', source.value);
      const response = await fetch(url, {credentials: 'same-origin', signal: controller.signal, headers: {Accept: 'application/json'}});
      if (!response.ok) throw new Error('Selection request failed');
      const data = await response.json();
      if (requestVersion !== version) return;
      selection = data;
      updateGroups();
      showSelection();
    } catch (error) {
      if (error.name !== 'AbortError' && requestVersion === version) {
        selection = {kind: '', products: [], groups: []};
        updateGroups();
        showSelection();
        status.textContent = 'Ürünler yüklenemedi. Seçimi güncelle düğmesiyle yeniden deneyin.';
      }
    } finally {
      if (requestVersion === version) {
        save.disabled = false;
        if (add) add.disabled = false;
      }
    }
  }

  source.addEventListener('change', reloadProducts);
  group.addEventListener('change', () => { if (!save.disabled) fields().forEach(updateChoices); });
  editor.addEventListener('change', event => {
    if (fields().includes(event.target)) updateUnits(event.target);
  });
  if (add) add.addEventListener('click', () => {
    const index = Number(total.value);
    if (maximum && index >= Number(maximum.value)) {
      status.textContent = 'Sipariş için izin verilen satır sayısına ulaşıldı.';
      return;
    }
    const template = document.createElement('template');
    template.innerHTML = editor.querySelector('[data-empty-line]').innerHTML.replaceAll('__prefix__', String(index));
    body.append(template.content);
    total.value = index + 1;
    updateChoices(body.lastElementChild.querySelector('select'));
  });
  editor.addEventListener('click', event => {
    const button = event.target.closest('[data-remove-line]');
    if (!button) return;
    const row = button.closest('.order-line-row');
    if (row.querySelector('input[name$="-id"]').value) return;
    row.remove();
    [...body.children].forEach((line, index) => {
      for (const element of line.querySelectorAll('[name], [id], [for]')) {
        for (const attribute of ['name', 'id', 'for']) {
          if (element.hasAttribute(attribute)) element.setAttribute(attribute, element.getAttribute(attribute).replace(/lines-\d+-/g, `lines-${index}-`));
        }
      }
    });
    total.value = body.children.length;
  });
  showSelection();
})();
