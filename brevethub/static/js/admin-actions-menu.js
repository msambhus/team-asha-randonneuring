/**
 * Admin roster / volunteer export Actions dropdown.
 * Pages dispatch handlers via data-actions-handlers on the menu root.
 */
(function () {
  function closeAllMenus(except) {
    document.querySelectorAll('[data-actions-menu]').forEach(function (menu) {
      if (except && menu === except) return;
      var panel = menu.querySelector('.admin-actions-menu-panel');
      var btn = menu.querySelector('.admin-actions-menu-btn');
      if (panel) panel.hidden = true;
      if (btn) btn.setAttribute('aria-expanded', 'false');
    });
  }

  function flashMenuButton(menu, label) {
    var btn = menu.querySelector('.admin-actions-menu-btn');
    if (!btn) return;
    btn.classList.add('is-flash');
    var prev = btn.innerHTML;
    btn.innerHTML = label;
    setTimeout(function () {
      btn.classList.remove('is-flash');
      btn.innerHTML = prev;
    }, 1600);
  }

  window.adminActionsCopyText = function (text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    return new Promise(function (resolve, reject) {
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.left = '-9999px';
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand('copy') ? resolve() : reject();
      } catch (e) {
        reject(e);
      }
      document.body.removeChild(ta);
    });
  };

  window.adminActionsCopyRich = function (text, html) {
    if (navigator.clipboard && window.ClipboardItem) {
      return navigator.clipboard.write([
        new ClipboardItem({
          'text/plain': new Blob([text], { type: 'text/plain' }),
          'text/html': new Blob([html], { type: 'text/html' }),
        }),
      ]);
    }
    return window.adminActionsCopyText(text);
  };

  document.addEventListener('click', function () {
    closeAllMenus();
  });

  document.querySelectorAll('[data-actions-menu]').forEach(function (menu) {
    var btn = menu.querySelector('.admin-actions-menu-btn');
    var panel = menu.querySelector('.admin-actions-menu-panel');
    if (!btn || !panel) return;

    btn.addEventListener('click', function (e) {
      e.stopPropagation();
      if (btn.disabled) return;
      var willOpen = panel.hidden;
      closeAllMenus();
      panel.hidden = !willOpen;
      btn.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
    });

    panel.querySelectorAll('[data-action]').forEach(function (item) {
      item.addEventListener('click', function (e) {
        e.stopPropagation();
        var action = item.getAttribute('data-action');
        if (action === 'csv') {
          closeAllMenus();
          return;
        }
        e.preventDefault();
        var handlers = menu._actionHandlers || {};
        var fn = handlers[action];
        if (!fn) return;
        Promise.resolve(fn()).then(function () {
          flashMenuButton(menu, '✓ Copied');
        }).catch(function () {
          flashMenuButton(menu, '✗ Failed');
        });
        closeAllMenus();
      });
    });

    menu.registerActionHandlers = function (handlers) {
      menu._actionHandlers = handlers || {};
    };
  });
})();
