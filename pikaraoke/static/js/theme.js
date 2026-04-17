/**
 * Theme switcher. Applies data-theme="late-show" | "summer" on <html>.
 * Persists choice in localStorage under "pk-theme".
 *
 * Exposes window.PK.theme.{get, set, toggle, apply}.
 * Runs apply() eagerly in <head> to avoid a flash of the wrong theme.
 */
(function () {
  'use strict';

  window.PK = window.PK || {};
  if (window.PK.theme) return;

  const KEY = 'pk-theme';
  const THEMES = ['mazury', 'late-show'];
  const DEFAULT = 'mazury';

  function get() {
    try {
      const v = localStorage.getItem(KEY);
      return THEMES.includes(v) ? v : DEFAULT;
    } catch (_) {
      return DEFAULT;
    }
  }

  function set(theme) {
    if (!THEMES.includes(theme)) return;
    try { localStorage.setItem(KEY, theme); } catch (_) {}
    apply(theme);
  }

  function toggle() {
    const current = get();
    const next = current === 'mazury' ? 'late-show' : 'mazury';
    set(next);
    return next;
  }

  function apply(theme) {
    theme = theme || get();
    document.documentElement.setAttribute('data-theme', theme);
    const isLight = theme === 'mazury';
    document.documentElement.style.colorScheme = isLight ? 'light' : 'dark';
    let meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', isLight ? '#f5ecd4' : '#0c0a07');
    document.dispatchEvent(new CustomEvent('pk:theme-change', { detail: { theme } }));
  }

  window.PK.theme = { get, set, toggle, apply, THEMES };

  // Apply as early as possible. Re-apply on DOM ready in case <html> wasn't
  // reachable during script eval (it is, but defensive).
  apply();
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => apply());
  }
})();
