(() => {
  const form=document.querySelector('[data-document-editor]');
  if (!form) return;
  const products=JSON.parse(document.getElementById('document-product-units').textContent);
  const lines=JSON.parse(document.getElementById('document-line-units').textContent);
  function update(row) {
    const kind=form.querySelector('#id_kind').value;
    const selector=row.querySelector(kind==='delivery'||kind==='receipt'?'[name$="-order_line"]':'[name$="-product"]');
    const units=kind==='delivery'||kind==='receipt'?lines:products;
    const input=row.querySelector('[name$="-quantity"]');
    const unit=units[selector.value];
    input.step=unit==='adet'?'1':'0.000000000001';
    input.min=kind==='count'?'0':unit==='adet'?'1':'0.000000000001';
    if(unit==='adet'&&input.value&&Number.isSafeInteger(Number(input.value)))input.value=String(Number(input.value));
  }
  form.addEventListener('change',event=>{
    if(event.target.id==='id_kind')form.querySelectorAll('.document-line').forEach(update);
    const row=event.target.closest('.document-line');if(row)update(row);
  });
  form.querySelector('[data-document-add]').addEventListener('click',()=>{
    const total=form.querySelector('#id_items-TOTAL_FORMS');
    if(Number(total.value)>=200)return;
    const template=form.querySelector('[data-document-empty]');
    const fragment=document.createElement('template');fragment.innerHTML=template.innerHTML.replaceAll('__prefix__',total.value);
    const row=fragment.content.firstElementChild;
    form.querySelector('[data-document-lines]').append(row);total.value=String(Number(total.value)+1);update(row);
  });
  form.querySelectorAll('.document-line').forEach(update);
})();
