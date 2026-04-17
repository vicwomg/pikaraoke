/**
 * PK.dialog — Promise-based in-app replacement for window.prompt/confirm/alert.
 *
 * Mounts one dialog element lazily on first use. API:
 *
 *   await PK.dialog.confirm({ title, message, confirmText, cancelText, destructive })
 *   await PK.dialog.prompt({ title, message, defaultValue, placeholder, confirmText, cancelText, validator })
 *   await PK.dialog.alert({ title, message, confirmText })
 *
 * Returns: boolean for confirm; string | null for prompt (null on cancel);
 * undefined for alert.
 */
(function () {
  'use strict';

  window.PK = window.PK || {};
  if (window.PK.dialog) return;

  let dom = null;
  let closeActive = null;

  function mount() {
    if (dom) return dom;
    const backdrop = document.createElement('div');
    backdrop.className = 'pk-dialog-backdrop';
    backdrop.id = 'pk-dialog-backdrop';

    const wrap = document.createElement('div');
    wrap.className = 'pk-dialog';
    wrap.id = 'pk-dialog';
    wrap.setAttribute('role', 'dialog');
    wrap.setAttribute('aria-modal', 'true');
    wrap.innerHTML = `
      <div class="pk-dialog-inner">
        <h3 class="pk-dialog-title" data-pk-dlg-title></h3>
        <p class="pk-dialog-message" data-pk-dlg-message></p>
        <input type="text" class="pk-input pk-dialog-input" data-pk-dlg-input hidden>
        <div class="pk-dialog-actions">
          <button type="button" class="pk-btn" data-pk-dlg-cancel></button>
          <button type="button" class="pk-btn is-primary" data-pk-dlg-confirm></button>
        </div>
      </div>`;

    document.body.appendChild(backdrop);
    document.body.appendChild(wrap);

    dom = {
      backdrop,
      wrap,
      title: wrap.querySelector('[data-pk-dlg-title]'),
      message: wrap.querySelector('[data-pk-dlg-message]'),
      input: wrap.querySelector('[data-pk-dlg-input]'),
      cancel: wrap.querySelector('[data-pk-dlg-cancel]'),
      confirm: wrap.querySelector('[data-pk-dlg-confirm]'),
    };

    backdrop.addEventListener('click', () => closeActive && closeActive(null));
    document.addEventListener('keydown', (e) => {
      if (!dom.wrap.classList.contains('is-open')) return;
      if (e.key === 'Escape') { e.preventDefault(); closeActive && closeActive(null); }
      if (e.key === 'Enter' && document.activeElement !== dom.cancel) {
        e.preventDefault();
        closeActive && closeActive('confirm');
      }
    });

    return dom;
  }

  function open(opts) {
    const d = mount();

    // If a dialog is already open, close it (last-call-wins)
    if (closeActive) closeActive(null);

    d.title.textContent = opts.title || '';
    d.title.hidden = !opts.title;
    d.message.textContent = opts.message || '';
    d.message.hidden = !opts.message;

    d.input.hidden = !opts.input;
    if (opts.input) {
      d.input.value = opts.defaultValue || '';
      d.input.placeholder = opts.placeholder || '';
      d.input.type = opts.inputType || 'text';
    }

    d.confirm.textContent = opts.confirmText || 'OK';
    d.confirm.classList.toggle('is-primary', !opts.destructive);
    d.confirm.classList.toggle('is-danger', !!opts.destructive);

    if (opts.confirmOnly) {
      d.cancel.hidden = true;
    } else {
      d.cancel.hidden = false;
      d.cancel.textContent = opts.cancelText || 'Cancel';
    }

    return new Promise((resolve) => {
      let settled = false;

      function finish(value) {
        if (settled) return;
        settled = true;
        closeActive = null;
        d.wrap.classList.remove('is-open');
        d.backdrop.classList.remove('is-open');
        d.confirm.onclick = null;
        d.cancel.onclick = null;
        resolve(value);
      }

      closeActive = (result) => {
        if (result === 'confirm') {
          if (opts.input) {
            const v = d.input.value;
            if (opts.validator && !opts.validator(v)) return; // keep open
            finish(v);
          } else if (opts.confirmOnly) {
            finish(undefined);
          } else {
            finish(true);
          }
        } else {
          finish(opts.input ? null : opts.confirmOnly ? undefined : false);
        }
      };

      d.confirm.onclick = () => closeActive('confirm');
      d.cancel.onclick = () => closeActive(null);

      requestAnimationFrame(() => {
        d.backdrop.classList.add('is-open');
        d.wrap.classList.add('is-open');
        if (opts.input) {
          setTimeout(() => d.input.focus(), 50);
        } else {
          setTimeout(() => d.confirm.focus(), 50);
        }
      });
    });
  }

  window.PK.dialog = {
    confirm(opts = {}) {
      return open({
        title: opts.title,
        message: opts.message,
        confirmText: opts.confirmText,
        cancelText: opts.cancelText,
        destructive: opts.destructive,
      });
    },
    prompt(opts = {}) {
      return open({
        title: opts.title,
        message: opts.message,
        input: true,
        defaultValue: opts.defaultValue,
        placeholder: opts.placeholder,
        inputType: opts.inputType,
        confirmText: opts.confirmText,
        cancelText: opts.cancelText,
        validator: opts.validator,
      });
    },
    alert(opts = {}) {
      return open({
        title: opts.title,
        message: opts.message,
        confirmText: opts.confirmText || 'OK',
        confirmOnly: true,
      });
    },
  };
})();
