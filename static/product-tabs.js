(() => {
  const workspace = document.querySelector('[data-product-workspace]');
  if (!workspace) return;
  const tablist = workspace.querySelector('[data-product-tablist]');
  const panels = workspace.querySelector('[data-product-panels]');
  const listTab = workspace.querySelector('[data-product-list-tab]');
  const listPanel = workspace.querySelector('[data-product-list-panel]');
  const entries = new Map([['list', {tab: listTab, panel: listPanel, scroll: {x: 0, y: 0}}]]);
  let active = 'list';
  listPanel.setAttribute('role', 'tabpanel');
  listPanel.setAttribute('aria-labelledby', listTab.id);
  tablist.hidden = false;

  function activate(id) {
    if (!entries.has(id)) return;
    if (id !== active) {
      entries.get(active).scroll = {x: window.scrollX, y: window.scrollY};
      active = id;
      for (const [key, entry] of entries) {
        const selected = key === id;
        entry.panel.hidden = !selected;
        entry.tab.setAttribute('aria-selected', String(selected));
        entry.tab.tabIndex = selected ? 0 : -1;
      }
      requestAnimationFrame(() => {
        if (active === id) {
          const {x, y} = entries.get(id).scroll;
          window.scrollTo({left: x, top: y, behavior: 'instant'});
        }
      });
    }
    const tab = entries.get(id).tab;
    tab.focus({preventScroll: true});
    const bounds = (tab.closest('.product-workspace-tab') || tab).getBoundingClientRect();
    const visible = tablist.getBoundingClientRect();
    if (bounds.left < visible.left) tablist.scrollLeft += bounds.left - visible.left;
    else if (bounds.right > visible.right) tablist.scrollLeft += bounds.right - visible.right;
  }

  function close(id) {
    const entry = entries.get(id);
    if (!entry || id === 'list') return;
    if (entry.controller) entry.controller.abort();
    if (active === id) activate('list');
    else entries.get(active).tab.focus({preventScroll: true});
    entry.tab.parentElement.remove();
    entry.panel.remove();
    entries.delete(id);
  }

  async function load(entry, address) {
    const url = new URL(address, window.location.href);
    if (url.origin !== window.location.origin || url.pathname !== entry.path) return;
    if (entry.controller) entry.controller.abort();
    const controller = new AbortController();
    entry.controller = controller;
    entry.retryUrl = url.href;
    entry.panel.setAttribute('aria-busy', 'true');
    entry.status.textContent = 'Ürün ayrıntıları yükleniyor…';
    try {
      const response = await fetch(url, {
        credentials: 'same-origin', signal: controller.signal,
        headers: {'X-Product-Tab': '1', Accept: 'text/html'},
      });
      if (!response.ok || response.redirected) throw new Error('Product request failed');
      const html = await response.text();
      const parsed = new DOMParser().parseFromString(html, 'text/html');
      const content = parsed.querySelector('[data-product-content]');
      if (!content || content.dataset.productId !== entry.id) throw new Error('Invalid product response');
      if (entries.get(entry.id) !== entry || entry.controller !== controller) return;
      const restoreFocus = entry.content.contains(document.activeElement);
      entry.content.replaceChildren(content);
      if (restoreFocus && active === entry.id) {
        entry.content.querySelector('[data-product-section][aria-current]').focus({preventScroll: true});
      }
      entry.loaded = true;
      entry.status.replaceChildren();
    } catch (error) {
      if (error.name !== 'AbortError' && entries.get(entry.id) === entry && entry.controller === controller) {
        entry.status.textContent = 'Ürün ayrıntıları yüklenemedi. Bağlantınızı ve erişim yetkinizi kontrol edip yeniden deneyin. ';
        const retry = document.createElement('button');
        retry.type = 'button';
        retry.className = 'button secondary';
        retry.textContent = 'Yeniden dene';
        retry.addEventListener('click', () => load(entry, entry.retryUrl));
        entry.status.append(retry);
      }
    } finally {
      if (entry.controller === controller) {
        entry.controller = null;
        entry.panel.removeAttribute('aria-busy');
      }
    }
  }

  function open(link) {
    const id = link.dataset.productId;
    if (!/^\d+$/.test(id)) return;
    const url = new URL(link.href);
    if (url.origin !== window.location.origin) return;
    let entry = entries.get(id);
    if (!entry) {
      const label = link.dataset.productCode;
      const container = document.createElement('div');
      container.className = 'product-workspace-tab';
      container.setAttribute('role', 'presentation');
      const tab = document.createElement('button');
      tab.type = 'button';
      tab.id = `product-tab-${id}`;
      tab.setAttribute('role', 'tab');
      tab.setAttribute('aria-selected', 'false');
      tab.setAttribute('aria-controls', `product-panel-${id}`);
      tab.setAttribute('aria-keyshortcuts', 'Delete');
      tab.tabIndex = -1;
      tab.textContent = label;
      tab.addEventListener('click', () => activate(id));
      const dismiss = document.createElement('button');
      dismiss.type = 'button';
      dismiss.className = 'product-tab-close';
      dismiss.setAttribute('aria-label', `${label} sekmesini kapat`);
      dismiss.title = 'Sekmeyi kapat (Delete)';
      dismiss.tabIndex = -1;
      dismiss.textContent = '×';
      dismiss.addEventListener('click', () => close(id));
      container.append(tab, dismiss);
      tablist.append(container);
      const panel = document.createElement('div');
      panel.id = `product-panel-${id}`;
      panel.dataset.productPanel = id;
      panel.setAttribute('role', 'tabpanel');
      panel.setAttribute('aria-labelledby', tab.id);
      panel.hidden = true;
      const status = document.createElement('p');
      status.className = 'product-tab-status';
      status.setAttribute('role', 'status');
      const content = document.createElement('div');
      panel.append(status, content);
      panels.append(panel);
      entry = {id, path: url.pathname, tab, panel, status, content, scroll: {x: 0, y: 0}};
      entries.set(id, entry);
    }
    activate(id);
    if (!entry.loaded && !entry.controller) load(entry, url);
  }

  listTab.addEventListener('click', () => activate('list'));
  workspace.addEventListener('click', event => {
    if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest('a');
    if (!link) return;
    if (link.matches('[data-product-open]')) {
      event.preventDefault();
      open(link);
    } else if (link.matches('[data-product-list]')) {
      event.preventDefault();
      activate('list');
    } else if (link.matches('[data-product-section]')) {
      event.preventDefault();
      const panel = link.closest('[data-product-panel]');
      load(entries.get(panel.dataset.productPanel), link.href);
    }
  });
  workspace.addEventListener('submit', event => {
    const form = event.target;
    if (!form.matches('[data-product-recipe]')) return;
    event.preventDefault();
    const panel = form.closest('[data-product-panel]');
    const url = new URL(form.action);
    url.search = new URLSearchParams(new FormData(form)).toString();
    load(entries.get(panel.dataset.productPanel), url);
  });
  tablist.addEventListener('keydown', event => {
    const tab = event.target.closest('[role="tab"]');
    if (!tab) return;
    const tabs = [...tablist.querySelectorAll('[role="tab"]')];
    const index = tabs.indexOf(tab);
    let next;
    if (event.key === 'ArrowRight') next = tabs[(index + 1) % tabs.length];
    if (event.key === 'ArrowLeft') next = tabs[(index - 1 + tabs.length) % tabs.length];
    if (event.key === 'Home') next = tabs[0];
    if (event.key === 'End') next = tabs[tabs.length - 1];
    if (next) {
      event.preventDefault();
      next.click();
    } else if (event.key === 'Delete' && active !== 'list') {
      event.preventDefault();
      close(active);
    }
  });
})();
