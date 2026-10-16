/* WebMCP: expose Realitify's key actions to in-browser AI agents. */
(function () {
  var done = false;
  function text(obj) { return { content: [{ type: 'text', text: JSON.stringify(obj) }] }; }

  function register(mc) {
    function reg(tool) { try { var p = mc.registerTool(tool); if (p && p.catch) p.catch(function () {}); } catch (e) {} }

    reg({
      name: 'search_listings',
      description: 'Search available apartments and houses for rent or sale in the Czech Republic across many portals (Sreality, Bezrealitky, iDNES, Bazoš…).',
      inputSchema: {
        type: 'object',
        properties: {
          q: { type: 'string', description: 'Locality, e.g. "Praha", "Brno", "Smíchov"' },
          offer: { type: 'string', enum: ['pronajem', 'prodej'], description: 'Rent or sale' },
          disposition: { type: 'string', description: 'Layout, e.g. "2+kk"' },
          price_to: { type: 'string', description: 'Maximum price in CZK' },
          limit: { type: 'integer', minimum: 1, maximum: 20 }
        }
      },
      execute: function (args) {
        var p = new URLSearchParams();
        ['q', 'offer', 'disposition', 'price_to'].forEach(function (k) { if (args && args[k]) p.set(k, args[k]); });
        p.set('limit', String((args && args.limit) || 10));
        return fetch('/api/public/catalog?' + p.toString()).then(function (r) { return r.json(); }).then(text);
      }
    });

    reg({
      name: 'open_search',
      description: 'Open the Realitify listing search page for a locality in the browser.',
      inputSchema: { type: 'object', properties: { q: { type: 'string', description: 'Locality or keywords' } } },
      execute: function (args) {
        var q = (args && args.q) || '';
        location.href = '/hledat' + (q ? '?q=' + encodeURIComponent(q) : '');
        return Promise.resolve(text({ ok: true, url: '/hledat' }));
      }
    });

    reg({
      name: 'open_rent_index',
      description: 'Open the Czech rent index (median rents per city, per m²).',
      inputSchema: { type: 'object', properties: {} },
      execute: function () { location.href = '/index'; return Promise.resolve(text({ ok: true, url: '/index' })); }
    });

    reg({
      name: 'start_free_alerts',
      description: 'Open sign-up to get alerts about new listings matching the user\'s filters (free tier available).',
      inputSchema: { type: 'object', properties: {} },
      execute: function () { location.href = '/registrace'; return Promise.resolve(text({ ok: true, url: '/registrace' })); }
    });
  }

  function init() {
    if (done) return true;
    var mc = navigator.modelContext || document.modelContext;
    if (!mc || typeof mc.registerTool !== 'function') return false;
    done = true;
    register(mc);
    return true;
  }

  if (init()) return;
  document.addEventListener('DOMContentLoaded', init);
  window.addEventListener('load', init);
  var tries = 0;
  var timer = setInterval(function () { if (init() || ++tries > 40) clearInterval(timer); }, 250);
})();
