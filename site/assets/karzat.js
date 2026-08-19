(function(){
  // Every table with data-page-size gets a pager: 25 rows at a time, prev / next / page numbers / 'mind'.
  // Filters mark excluded rows with data-x and call table.__pager.render(true); sorters re-append rows and
  // call render(false). Without JS the whole table is in the page.
  function paginate(table){
    var per0 = parseInt(table.getAttribute('data-page-size') || '25', 10), per = per0, page = 1;
    var tbody = table.tBodies[0]; if (!tbody) return;
    var counter = table.getAttribute('data-counter') ? document.getElementById(table.getAttribute('data-counter')) : null;
    var wrap = table.closest('.tablewrap') || table;
    var nav = document.createElement('nav'); nav.className = 'pgr'; nav.setAttribute('aria-label', 'Lapozás');
    wrap.parentNode.insertBefore(nav, wrap.nextSibling);
    function render(reset){
      if (reset) page = 1;
      var rows = Array.prototype.slice.call(tbody.rows).filter(function(r){ return !r.classList.contains('empty') && !(r.cells.length === 1 && r.cells[0].colSpan > 1); }), vis = rows.filter(function(r){ return !r.hasAttribute('data-x'); });
      var total = vis.length, pages = Math.max(1, Math.ceil(total / per));
      if (page > pages) page = pages;
      rows.forEach(function(r){ r.hidden = true; });
      var from = (page - 1) * per, to = Math.min(total, from + per);
      vis.slice(from, to).forEach(function(r){ r.hidden = false; });
      if (counter) counter.textContent = (total ? (from + 1) + '–' + to : '0') + ' / ' + total + (total !== rows.length ? ' (' + rows.length + ')' : '');
      if (pages <= 1 && per === per0 && total <= per0) { nav.innerHTML = ''; nav.hidden = true; return; }
      nav.hidden = false;
      var nums = [], seen = {};
      [1, 2, page - 2, page - 1, page, page + 1, page + 2, pages - 1, pages].forEach(function(n){ if (n >= 1 && n <= pages && !seen[n]) { seen[n] = 1; nums.push(n); } });
      nums.sort(function(a, b){ return a - b; });
      var html = '<button type="button" data-pg="prev"' + (page <= 1 ? ' disabled' : '') + ' aria-label="Előző oldal">‹</button>', last = 0;
      nums.forEach(function(n){ if (n - last > 1) html += '<span class="gap">…</span>'; html += '<button type="button" data-pg="' + n + '"' + (n === page ? ' aria-current="page" class="on"' : '') + '>' + n + '</button>'; last = n; });
      html += '<button type="button" data-pg="next"' + (page >= pages ? ' disabled' : '') + ' aria-label="Következő oldal">›</button>';
      html += '<button type="button" data-pg="all" class="all' + (per > per0 ? ' on' : '') + '">' + (per > per0 ? per0 + ' / oldal' : 'mind') + '</button>';
      nav.innerHTML = html;
    }
    var empty = null;
    function emptyRow(total){
      // an emptied table keeps its header and says so, instead of ending in a blank
      if (!total) { if (!empty) { empty = document.createElement('tr'); empty.className = 'empty'; var td = document.createElement('td'); td.colSpan = (table.tHead && table.tHead.rows[0] ? table.tHead.rows[0].cells.length : 1); td.textContent = 'Nincs találat.'; empty.appendChild(td); } if (empty.parentNode !== tbody) tbody.appendChild(empty); }
      else if (empty && empty.parentNode) empty.parentNode.removeChild(empty);
    }
    var render0 = render;
    render = function(reset){ render0(reset); var placeholder = Array.prototype.some.call(tbody.rows, function(r){ return r.cells.length === 1 && r.cells[0].colSpan > 1 && !r.classList.contains('empty'); }); emptyRow(placeholder ? 1 : tbody.querySelectorAll('tr:not([data-x]):not(.empty)').length); };
    nav.addEventListener('click', function(e){
      var b = e.target.closest('button[data-pg]'); if (!b) return;
      var v = b.getAttribute('data-pg'), had = document.activeElement === b;
      if (v === 'prev') page--; else if (v === 'next') page++;
      else if (v === 'all') { per = per > per0 ? per0 : 100000; page = 1; }
      else page = parseInt(v, 10);
      render(false);
      if (had) { var again = nav.querySelector('button[data-pg="' + v + '"]:not([disabled])') || nav.querySelector('button[data-pg="' + (v === 'prev' ? 'next' : v === 'next' ? 'prev' : 'all') + '"]') || nav.querySelector('button.on'); if (again) again.focus(); }
      var top = wrap.getBoundingClientRect().top; if (top < 60) window.scrollTo({top: window.pageYOffset + top - 64, behavior: 'auto'});
    });
    table.__pager = {render: render};
    render(false);
  }
  document.querySelectorAll('table[data-page-size]').forEach(paginate);
  window.__karzatRerender = function(table, reset){ if (table && table.__pager) table.__pager.render(reset); else if (table) { var rows = Array.prototype.slice.call(table.tBodies[0].rows); rows.forEach(function(r){ if (!r.classList.contains('grp')) r.hidden = r.hasAttribute('data-x'); }); } };
})();


(function(){
  // <input data-filter-table="id"> narrows a table by text (accent-insensitive); the button filters of the same
  // table consult table.__textMatch, so both narrow together.
  function fold(s){ return String(s || '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, ''); }
  document.querySelectorAll('input[data-filter-table]').forEach(function(inp){
    var table = document.getElementById(inp.getAttribute('data-filter-table')); if (!table || !table.tBodies[0]) return;
    var all = Array.prototype.slice.call(table.tBodies[0].rows), rows = [], groups = [], cur = null, curTxt = '';
    all.forEach(function(r){ if (r.classList.contains('grp')) { cur = r; curTxt = fold(r.textContent); groups.push(r); r.__rows = []; return; } r.__hay = fold(r.textContent) + ' ' + curTxt; rows.push(r); if (cur) cur.__rows.push(r); });
    table.__textq = '';
    table.__textMatch = function(r){ return !table.__textq || (r.__hay || fold(r.textContent)).indexOf(table.__textq) >= 0; };
    table.__rowf = null;                                       // an attribute filter set by button[data-rowf] (below), if any
    function apply(){
      if (table.__reapply) { table.__reapply(); return; }
      rows.forEach(function(r){ var ok = table.__textMatch(r) && (!table.__rowf || r.getAttribute(table.__rowf[0]) === table.__rowf[1]); if (ok) r.removeAttribute('data-x'); else r.setAttribute('data-x', ''); });
      groups.forEach(function(g){ var any = g.__rows.some(function(r){ return !r.hasAttribute('data-x'); }); g.hidden = !any; });
      if (window.__karzatRerender) window.__karzatRerender(table, true);
    }
    table.__applyFilters = apply;
    inp.addEventListener('input', function(){ table.__textq = fold(inp.value).trim(); apply(); });
  });
  // <button data-rowf="attr:value" data-table="id"> (or data-rowf="all") beside a text box: keeps rows whose attribute equals the value
  document.querySelectorAll('button[data-rowf]').forEach(function(b){
    var table = document.getElementById(b.getAttribute('data-table')); if (!table) return;
    b.addEventListener('click', function(){
      var v = b.getAttribute('data-rowf');
      table.__rowf = (v === 'all') ? null : v.split(':');
      document.querySelectorAll('button[data-rowf][data-table="' + table.id + '"]').forEach(function(x){ var on = x === b; x.classList.toggle('on', on); x.setAttribute('aria-pressed', on ? 'true' : 'false'); });
      if (table.__applyFilters) table.__applyFilters();
    });
  });
})();


(function(){
  // felszolalas/kereses.html: terms are AND-ed; each term matches tokens by prefix (3+ letters, accents folded); the index
  // is sharded by the token's first two letters (idx/xx.json), the results table lists the speeches newest first and
  // fetches the visible pages' texts for a snippet.
  var q = document.getElementById('spq'), body = document.getElementById('spres'), n = document.getElementById('spn'), table = body && body.closest('table');
  if (!q || !body) return;
  function fold(s){ return String(s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase(); }
  function esc(s){ return String(s == null ? '' : s).replace(/[&<>"]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  var meta = null, metaFailed = false, metaWaiters = [], shards = {}, texts = {}, seq = 0;
  function get(url, cb){ var x = new XMLHttpRequest(); x.open('GET', url); x.onload = function(){ if (x.status >= 400) return cb(null); try { cb(JSON.parse(x.responseText)); } catch (e) { cb(null); } }; x.onerror = function(){ cb(null); }; x.send(); }
  function shard2(key, cb){ if (shards[key] !== undefined) return cb(shards[key]); get('idx/' + key + '.json', function(d){ shards[key] = d || {}; cb(shards[key]); }); }
  function shard(term, cb){ shard2(term.slice(0, 2), function(sh){ if (sh && sh.__split) shard2(term.slice(0, 3), cb); else cb(sh); }); }   // a big two-letter shard is split by the third letter
  function withMeta(cb){ if (meta) return cb(); if (metaFailed) return cb(); metaWaiters.push(cb); if (metaWaiters.length > 1) return; get('meta.json', function(d){ if (d) meta = d; else metaFailed = true; var w = metaWaiters; metaWaiters = []; w.forEach(function(f){ f(); }); }); }
  var urlTimer = null;
  function syncUrl(){ clearTimeout(urlTimer); urlTimer = setTimeout(function(){ if (!history.replaceState) return; var v = q.value.trim(); history.replaceState(null, '', v ? '?q=' + encodeURIComponent(v) : location.pathname); }, 300); }
  function search(){
    var my = ++seq;
    var terms = (fold(q.value).match(/[a-z0-9]{3,}/g) || []).filter(function(t, i, a){ return a.indexOf(t) === i; });
    if (!terms.length) { body.innerHTML = '<tr><td colspan="4" class="hero-meta">Kezdj el gépelni (legalább három betű).</td></tr>'; n.textContent = ''; if (window.__karzatRerender && table) window.__karzatRerender(table, true); return; }
    var need = terms.length + 1, sets = [];
    function step(){ if (--need > 0 || my !== seq) return; render(); }
    withMeta(step);
    terms.forEach(function(t, i){
      shard(t, function(sh){
        var ids = {};
        for (var k in sh) if (k !== '__split' && k.indexOf(t) === 0) { var arr = sh[k]; for (var j = 0; j < arr.length; j++) ids[arr[j]] = 1; }
        sets[i] = ids; step();
      });
    });
    function render(){
      if (!meta) { body.innerHTML = '<tr><td colspan="3" class="hero-meta">A kereső listája (meta.json) nem tölthető be.</td></tr>'; n.textContent = ''; return; }
      var hits = [];
      for (var i = 0; i < meta.length; i++) { var ok = true; for (var s = 0; s < sets.length; s++) { if (!sets[s][i]) { ok = false; break; } } if (ok) hits.push(i); }
      hits.reverse();                                                       // ids are chronological: newest first
      body.innerHTML = hits.map(function(i){ var m = meta[i]; var who = m[3] ? '<a href="../kepviselo/' + esc(m[3]) + '.html">' + esc(m[2]) + '</a>' : esc(m[2]);
        return '<tr data-i="' + i + '"><td class="ts mono"><a href="' + esc(m[0]) + '.html">' + esc(m[1]) + '</a></td><td>' + who + '<span class="sub">' + esc(m[4] || '') + (m[6] ? ' · ' + esc(m[6]) : '') + '</span></td><td>' + esc(m[5] || '') + '<span class="snip"></span></td></tr>'; }).join('')
        || '<tr><td colspan="3" class="hero-meta">Nincs találat.</td></tr>';
      n.textContent = hits.length + ' találat';
      if (window.__karzatRerender && table) window.__karzatRerender(table, true);
      snippets(terms);
    }
  }
  function snippets(terms){
    var rows = Array.prototype.slice.call(body.rows).filter(function(r){ return !r.hidden && r.hasAttribute('data-i'); });
    rows.forEach(function(r){
      var i = parseInt(r.getAttribute('data-i'), 10), m = meta[i], cell = r.querySelector('.snip'); if (!cell || cell.getAttribute('data-done')) return;
      cell.setAttribute('data-done', '1');
      var fill = function(d){ if (!d || !d.paragraphs) { cell.textContent = ''; return; }
        // matches are found on the folded text at word starts (the index rule) and painted on the original by the same
        // offsets — folding a precomposed letter keeps the length, so the offsets line up
        var txt = d.paragraphs.join(' '), f = fold(txt);
        if (f.length !== txt.length) f = txt.toLowerCase();
        var spans = [], first = -1;
        terms.forEach(function(t){ var re = new RegExp('(^|[^a-z0-9])' + t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g'), m; while ((m = re.exec(f))) { var st = m.index + m[1].length, en = st + t.length; while (en < f.length && /[a-z0-9]/.test(f[en])) en++; spans.push([st, en]); if (first < 0 || st < first) first = st; if (spans.length > 40) break; } });
        if (first < 0) { cell.textContent = txt.slice(0, 160) + (txt.length > 160 ? '…' : ''); return; }
        var a = Math.max(0, first - 90), b = Math.min(txt.length, first + 130), out = '', pos = a;
        spans.sort(function(x, y){ return x[0] - y[0]; }).forEach(function(sp){ if (sp[1] <= a || sp[0] >= b || sp[0] < pos) return; out += esc(txt.slice(pos, sp[0])) + '<mark>' + esc(txt.slice(sp[0], Math.min(sp[1], b))) + '</mark>'; pos = Math.min(sp[1], b); });
        out += esc(txt.slice(pos, b));
        cell.innerHTML = (a > 0 ? '…' : '') + out + (b < txt.length ? '…' : ''); };
      if (texts[m[0]]) fill(texts[m[0]]); else get(m[0] + '.json', function(d){ texts[m[0]] = d; fill(d); });
    });
  }
  q.addEventListener('input', function(){ syncUrl(); search(); });
  if (table) table.addEventListener('click', function(e){ if (e.target.closest && e.target.closest('button')) setTimeout(function(){ snippets((fold(q.value).match(/[a-z0-9]{3,}/g) || [])); }, 50); });
  document.addEventListener('click', function(e){ var b = e.target.closest && e.target.closest('nav.pgr button'); if (b && table && b.closest('nav.pgr') && b.closest('nav.pgr').previousElementSibling && b.closest('nav.pgr').previousElementSibling.contains(table)) setTimeout(function(){ snippets((fold(q.value).match(/[a-z0-9]{3,}/g) || [])); }, 50); });
  if (location.search) { var m = /[?&]q=([^&]+)/.exec(location.search); if (m) { q.value = decodeURIComponent(m[1].replace(/\+/g, ' ')); search(); } }
})();


(function(){
  // kepviselom/index.html: a settlement (Budapest: a district) → its OEVK(s) → the MP; the list is telepules.json,
  // loaded on the first keystroke; a city the annex splits by streets lists every candidate and says the address decides.
  var q = document.getElementById('town'), out = document.getElementById('townres'), mapEl = document.getElementById('oevk-map'); if (!q || !out || !mapEl) return;
  var map; try { map = JSON.parse(mapEl.textContent); } catch (e) { return; }
  function fold(s){ return String(s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().trim(); }
  function esc(s){ return String(s == null ? '' : s).replace(/[&<>"]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  var data = null, loading = false, keys = null;
  function load(cb){ if (data) return cb(); if (loading) return; loading = true; var x = new XMLHttpRequest(); x.open('GET', 'telepules.json'); x.onload = function(){ try { data = JSON.parse(x.responseText); } catch (e) { data = {}; } keys = Object.keys(data).map(function(k){ return [fold(k), k]; }); cb(); }; x.onerror = function(){ data = {}; keys = []; cb(); }; x.send(); }
  var urlTimer = null;
  function syncUrl(){ clearTimeout(urlTimer); urlTimer = setTimeout(function(){ if (!history.replaceState) return; var v = q.value.trim(); history.replaceState(null, '', v ? '?t=' + encodeURIComponent(v) : location.pathname); }, 300); }
  function render(){
    var t = fold(q.value);
    if (t.length < 2) { out.innerHTML = ''; return; }
    var exact = keys.filter(function(k){ return k[0] === t; }), pre = keys.filter(function(k){ return k[0].indexOf(t) === 0 && k[0] !== t; }).slice(0, 12);
    var hits = exact.concat(pre);
    if (!hits.length) { out.innerHTML = '<div class="hero-meta">Nincs ilyen település a választókerületi mellékletben.</div>'; return; }
    out.innerHTML = hits.map(function(k){
      var name = k[1], list = data[name] || [];
      var parts = list.map(function(o){ var key = o[0] + '-' + o[1], mp = map[key], who = mp ? (mp[0] ? '<a href="../kepviselo/' + esc(mp[0]) + '.html">' + esc(mp[1]) + '</a>' : esc(mp[1])) + (mp[2] ? ' (' + esc(mp[2]) + ')' : '') : '—';
        return esc(o[0]) + ' ' + o[1] + '. OEVK → ' + who + (o[2] ? ' <span class="sub">csak a település egy része — a cím dönt</span>' : ''); });
      return '<div class="townhit"><b>' + esc(name) + '</b>' + (list.length > 1 ? ' <span class="sub">több választókerület, utcák szerint — a cím dönt</span>' : '') + '<ul>' + parts.map(function(p){ return '<li>' + p + '</li>'; }).join('') + '</ul></div>'; }).join('');
  }
  q.addEventListener('input', function(){ syncUrl(); load(render); });
  if (location.search) { var m = /[?&]t=([^&]+)/.exec(location.search); if (m) { q.value = decodeURIComponent(m[1].replace(/\+/g, ' ')); load(render); } }
})();


(function(){
  // The landing page's chamber: every seat is the member who sits in it. Hover or focus names them and shows the
  // cycle record the MP page prints; a click pins the card (Esc or a click on the floor releases it, a click on the
  // name opens the page); the legend filters to one faction. Keyboard: the chamber is one tab stop, arrows walk the
  // seats in seating order, Enter pins. Everything is in the page — no request is made.
  var hall = document.querySelector('.chamber-today'), box = document.getElementById('hall'), src = document.getElementById('hall-data');
  if (!hall || !box || !src) return;
  var data; try { data = JSON.parse(src.textContent); } catch (e) { return; }
  var svg = hall.querySelector('svg'); if (!svg) return;
  var hint = box.innerHTML, pinned = null, colours = {};
  document.querySelectorAll('.chamber-today .legend .f[data-f] i').forEach(function(i){ colours[i.parentNode.getAttribute('data-f')] = i.style.background; });
  var PHOTO_BASE = 'https://www.parlament.hu/felicitas/api/query/resource/kepviseloexportok/kepviselo-exported-queries-provider/kepviselo-kepek/';
  var mpBase = src.getAttribute('data-mp-base') || '';
  function esc(s){ return String(s == null ? '' : s).replace(/[&<>"]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  var seats = Array.prototype.slice.call(svg.querySelectorAll('.seat[data-az]'));
  seats.forEach(function(g, i){ g.setAttribute('tabindex', i === 0 ? '0' : '-1'); });
  function focusSeat(i){ if (!seats.length) return; i = (i + seats.length) % seats.length; seats.forEach(function(g, k){ g.setAttribute('tabindex', k === i ? '0' : '-1'); }); seats[i].focus(); }
  function render(az){
    var d = data[az]; if (!d) return;
    var name = d[0], fac = d[1], mandate = d[2], seat = d[3], cast = d[4], inroll = d[5], w = d[6], a = d[7], sp = d[8], com = d[9];
    var sz = (fac === 'szószóló');                       // a nationality spokesperson: sits and speaks, never votes
    var c = colours[fac] || '#8a8a8a';
    var part = inroll ? Math.round(100 * cast / inroll) : null, agree = (w + a) ? Math.round(100 * w / (w + a)) : null;
    var who = sz ? esc(name) : '<a href="' + mpBase + esc(az) + '.html">' + esc(name) + '</a>';
    box.innerHTML = '<img class="portrait insp" src="' + PHOTO_BASE + esc(az) + '" alt="" width="195" height="260" loading="lazy" decoding="async" referrerpolicy="no-referrer" title="fénykép: parlament.hu" onerror="this.remove()">' +
      '<div class="row1"><span class="name">' + who + '</span>' +
      '<span class="meta"><i class="d" style="--c:' + c + '"></i> ' + (sz ? 'nemzetiségi szószóló' : esc(fac)) + ' · ' + esc(mandate) + (seat ? ' · ' + esc(seat) : '') + '</span></div>' +
      '<div class="row2"><span class="rec"><span class="lbl">a ciklusban</span>' +
      (sz ? 'nem szavaz — a szószóló felszólalhat és bizottságban dolgozik' + (com ? ' · bizottság <b>' + com + '</b>' : '')
          : (inroll ? 'leadott <b>' + cast + '</b> / ' + inroll + ' (' + part + '%) · frakciójával <b>' + w + '</b> · ellene <b>' + a + '</b>' + (agree !== null ? ' · egyetért <b>' + agree + '%</b>' : '') : 'még nincs név szerinti szavazása')) +
      (sp ? ' · felszólalás <b>' + sp + '</b>' : '') + '</span>' +
      (pinned ? '<span class="pin">rögzítve<button type="button" data-unpin>Esc</button></span>' : '') + '</div>';
  }
  function mark(az, on){ svg.querySelectorAll('.seat[data-az="' + az + '"]').forEach(function(g){ g.classList.toggle('hl', on); }); }
  function show(az){ if (pinned && pinned !== az) return; render(az); }
  function reset(){ if (pinned) return; box.innerHTML = hint; }
  svg.addEventListener('mouseover', function(e){ var g = e.target.closest('.seat[data-az]'); if (!g) return; mark(g.getAttribute('data-az'), true); show(g.getAttribute('data-az')); });
  svg.addEventListener('mouseout', function(e){ var g = e.target.closest('.seat[data-az]'); if (!g) return; if (pinned !== g.getAttribute('data-az')) mark(g.getAttribute('data-az'), false); reset(); });
  svg.addEventListener('focusin', function(e){ var g = e.target.closest('.seat[data-az]'); if (!g) return; mark(g.getAttribute('data-az'), true); show(g.getAttribute('data-az')); });
  svg.addEventListener('focusout', function(e){ var g = e.target.closest('.seat[data-az]'); if (!g) return; if (pinned !== g.getAttribute('data-az')) mark(g.getAttribute('data-az'), false); reset(); });
  function pin(az){ if (pinned) mark(pinned, false); pinned = az; hall.classList.add('pinned'); mark(az, true); render(az); }
  function unpin(){ if (pinned) mark(pinned, false); pinned = null; hall.classList.remove('pinned'); box.innerHTML = hint; }
  svg.addEventListener('click', function(e){ var g = e.target.closest('.seat[data-az]'); if (!g) { unpin(); return; } var az = g.getAttribute('data-az'); if (pinned === az) unpin(); else pin(az); });
  svg.addEventListener('keydown', function(e){
    var g = e.target.closest('.seat[data-az]'); if (!g) return; var i = seats.indexOf(g);
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') { e.preventDefault(); focusSeat(i + 1); }
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') { e.preventDefault(); focusSeat(i - 1); }
    else if (e.key === 'Home') { e.preventDefault(); focusSeat(0); }
    else if (e.key === 'End') { e.preventDefault(); focusSeat(seats.length - 1); }
    else if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); var az = g.getAttribute('data-az'); if (pinned === az) unpin(); else pin(az); }
  });
  box.addEventListener('click', function(e){ if (e.target.closest('[data-unpin]')) unpin(); });
  document.addEventListener('keydown', function(e){ if (e.key === 'Escape') unpin(); });
  // the legend filters: one faction at a time, the rest dimmed (a second click clears it)
  var fac = 'all';
  document.querySelectorAll('.chamber-today .legend button[data-hf]').forEach(function(b){
    b.addEventListener('click', function(){
      fac = (fac === b.getAttribute('data-hf')) ? 'all' : b.getAttribute('data-hf');
      document.querySelectorAll('.chamber-today .legend button[data-hf]').forEach(function(x){ var on = fac !== 'all' && x === b; x.classList.toggle('on', on); x.setAttribute('aria-pressed', on ? 'true' : 'false'); });
      svg.querySelectorAll('.seat[data-f]').forEach(function(g){ g.classList.toggle('dim', fac !== 'all' && g.getAttribute('data-f') !== fac); });
    });
  });
})();


(function(){
  var tbody = document.getElementById('rows'), n = document.getElementById('n'); if (!tbody) return;
  var rows = Array.prototype.slice.call(tbody.rows), rule = 'all', result = 'all', year = 'all', q = '';
  var hay = rows.map(function(r){ var more = r.querySelector('.more'); return ((r.textContent || '') + ' ' + (more ? more.getAttribute('title') : '')).toLowerCase(); });
  function press(sel, on){ document.querySelectorAll(sel).forEach(function(x){ var isOn = x === on; x.classList.toggle('on', isOn); x.setAttribute('aria-pressed', isOn ? 'true' : 'false'); }); }
  var table = tbody.closest('table');
  function render(){
    var k = 0;
    rows.forEach(function(r, i){
      var ok = (rule === 'all' || r.getAttribute('data-rule') === rule) && (result === 'all' || r.getAttribute('data-result') === result) && (year === 'all' || r.getAttribute('data-y') === year) && (!q || hay[i].indexOf(q) >= 0);
      if (ok) { r.removeAttribute('data-x'); k++; } else r.setAttribute('data-x', '');
    });
    if (window.__karzatRerender) window.__karzatRerender(table, true); else n.textContent = k + ' / ' + rows.length;
  }
  document.querySelectorAll('button[data-rule]').forEach(function(b){ b.addEventListener('click', function(){ rule = b.getAttribute('data-rule'); press('button[data-rule]', b); render(); }); });
  document.querySelectorAll('button[data-result]').forEach(function(b){ b.addEventListener('click', function(){ result = b.getAttribute('data-result'); press('button[data-result]', b); render(); }); });
  document.querySelectorAll('button[data-year]').forEach(function(b){ b.addEventListener('click', function(){ year = b.getAttribute('data-year'); press('button[data-year]', b); render(); }); });
  document.getElementById('q').addEventListener('input', function(e){ q = e.target.value.trim().toLowerCase(); render(); });
  render();
})();


(function(){
  var chart = document.querySelector('.chart'), box = document.getElementById('insp'), src = document.getElementById('insp-data');
  if (!chart || !box || !src) return;
  var data; try { data = JSON.parse(src.textContent); } catch (e) { return; }
  var svg = chart.querySelector('svg'); if (!svg) return;
  var hint = box.innerHTML, pinned = null, colours = {};
  document.querySelectorAll('.legend .f[data-f] i').forEach(function(i){ colours[i.parentNode.getAttribute('data-f')] = i.style.background; });
  svg.querySelectorAll('.seat[data-az]').forEach(function(g){ if (g.hasAttribute('data-f') && !colours[g.getAttribute('data-f')]) { var c = g.querySelector('[fill]:not([fill="none"])'); if (c) colours[g.getAttribute('data-f')] = c.getAttribute('fill'); } });
  // keyboard: the chamber is one tab stop; ← → ↑ ↓ / Home / End walk the seats, Enter pins
  var seats = Array.prototype.slice.call(svg.querySelectorAll('.seat[data-az]'));
  seats.forEach(function(g, i){ g.setAttribute('tabindex', i === 0 ? '0' : '-1'); });
  function focusSeat(i){ if (!seats.length) return; i = (i + seats.length) % seats.length; seats.forEach(function(g, k){ g.setAttribute('tabindex', k === i ? '0' : '-1'); }); seats[i].focus(); }
  svg.addEventListener('keydown', function(e){
    var g = e.target.closest('.seat[data-az]'); if (!g) return; var i = seats.indexOf(g); if (i < 0) return;
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') { e.preventDefault(); focusSeat(i + 1); }
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') { e.preventDefault(); focusSeat(i - 1); }
    else if (e.key === 'Home') { e.preventDefault(); focusSeat(0); }
    else if (e.key === 'End') { e.preventDefault(); focusSeat(seats.length - 1); }
  });
  var POS = {igen:'igen', nem:'nem', tartozkodott:'tartózkodott', jelen_nem_szavazott:'jelen, nem szavazott', nem_szavazott:'nem szavazott', bejelentett_hianyzo:'előre bejelentett hiányzó', igazoltan_tavol:'igazoltan távol'};
  var PHOTO_BASE = 'https://www.parlament.hu/felicitas/api/query/resource/kepviseloexportok/kepviselo-exported-queries-provider/kepviselo-kepek/';
  var root = (document.querySelector('a.brand') || {}).getAttribute ? document.querySelector('a.brand').getAttribute('href').replace(/index\.html$/, '') : '';
  var mpBase = (svg.closest('body').querySelector('.pager') ? '../kepviselo/' : 'kepviselo/');
  function esc(s){ return String(s == null ? '' : s).replace(/[&<>"]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  function render(az){
    var d = data[az]; if (!d) return;
    var name = d[0], fac = d[1], mandate = d[2], seat = d[3], pos = d[4], cast = d[5], inroll = d[6], w = d[7], a = d[8], streak = d[9];
    var c = colours[fac] || '#8a8a8a';
    var part = inroll ? Math.round(100 * cast / inroll) : 0, agree = (w + a) ? Math.round(100 * w / (w + a)) : null;
    var sq = '';
    for (var i = 0; i < streak.length; i++) { var ch = streak[i]; sq += '<i class="' + (ch === '.' ? 'x' : ch) + (i === streak.length - 1 ? ' now' : '') + '" style="--c:' + c + '"></i>'; }
    box.innerHTML = '<img class="portrait insp" src="' + PHOTO_BASE + esc(az) + '" alt="" width="195" height="260" loading="lazy" decoding="async" referrerpolicy="no-referrer" title="fénykép: parlament.hu" onerror="this.remove()">' +
      '<div class="row1"><span class="name"><a href="' + mpBase + esc(az) + '.html">' + esc(name) + '</a></span>' +
      '<span class="meta"><i class="d" style="--c:' + c + '"></i> ' + esc(fac) + ' · ' + esc(mandate) + (seat ? ' · ' + esc(seat) : '') + '</span>' +
      '<span class="badge' + (pos === 'igen' ? ' ok' : pos === 'nem' ? ' no' : ' mid') + '">' + esc(POS[pos] || pos) + '</span></div>' +
      '<div class="row2"><span class="rec"><span class="lbl">a ciklusban</span>leadott <b>' + cast + '</b> / ' + inroll + ' (' + part + '%) · frakciójával <b>' + w + '</b> · ellene <b>' + a + '</b>' + (agree !== null ? ' · egyezés ' + agree + '%' : '') + '</span>' +
      '<span class="streak" title="az utolsó ' + streak.length + ' név szerinti szavazás eddig a szavazásig: frakciójával (szín) · ellene (fehér) · nem adott le (üres)"><span class="lbl">utolsó ' + streak.length + '</span>' + sq + '</span></div>' +
      (pinned ? '<span class="pin">rögzítve<button type="button" data-unpin>Esc</button></span>' : '');
  }
  function mark(az, on){
    svg.querySelectorAll('.seat[data-az="' + az + '"]').forEach(function(g){ g.classList.toggle('hl', on); });
    document.querySelectorAll('tr[data-az="' + az + '"]').forEach(function(r){ r.classList.toggle('hl', on); });
  }
  function show(az){ if (pinned && pinned !== az) return; render(az); }
  function reset(){ if (pinned) return; box.innerHTML = hint; }
  svg.addEventListener('mouseover', function(e){ var g = e.target.closest('.seat[data-az]'); if (!g) return; mark(g.getAttribute('data-az'), true); show(g.getAttribute('data-az')); });
  svg.addEventListener('mouseout', function(e){ var g = e.target.closest('.seat[data-az]'); if (!g) return; if (pinned !== g.getAttribute('data-az')) mark(g.getAttribute('data-az'), false); reset(); });
  svg.addEventListener('focusin', function(e){ var g = e.target.closest('.seat[data-az]'); if (!g) return; mark(g.getAttribute('data-az'), true); show(g.getAttribute('data-az')); });
  svg.addEventListener('focusout', function(e){ var g = e.target.closest('.seat[data-az]'); if (!g) return; if (pinned !== g.getAttribute('data-az')) mark(g.getAttribute('data-az'), false); reset(); });
  function pin(az){ if (pinned) mark(pinned, false); pinned = az; chart.classList.add('pinned'); mark(az, true); render(az); }
  function unpin(){ if (pinned) mark(pinned, false); pinned = null; chart.classList.remove('pinned'); box.innerHTML = hint; }
  svg.addEventListener('click', function(e){ var g = e.target.closest('.seat[data-az]'); if (!g) { unpin(); return; } var az = g.getAttribute('data-az'); if (pinned === az) unpin(); else pin(az); });
  svg.addEventListener('keydown', function(e){ var g = e.target.closest('.seat[data-az]'); if (!g) return; if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); var az = g.getAttribute('data-az'); if (pinned === az) unpin(); else pin(az); } });
  box.addEventListener('click', function(e){ if (e.target.closest('[data-unpin]')) unpin(); });
  document.addEventListener('keydown', function(e){ if (e.key === 'Escape') unpin(); });
  // the roll-call table talks back: hovering a row lights its seat
  document.querySelectorAll('tr[data-az]').forEach(function(r){
    r.addEventListener('mouseenter', function(){ mark(r.getAttribute('data-az'), true); show(r.getAttribute('data-az')); });
    r.addEventListener('mouseleave', function(){ if (pinned !== r.getAttribute('data-az')) mark(r.getAttribute('data-az'), false); reset(); });
  });
  // filters dim the seats they exclude
  window.__karzatDimSeats = function(fac, pos){
    svg.querySelectorAll('.seat').forEach(function(g){ var ok = (fac === 'all' || g.getAttribute('data-f') === fac) && (pos === 'all' || g.getAttribute('data-pos') === pos); g.classList.toggle('dim', !ok); });
  };
})();


(function(){
  var table = document.getElementById('roll'); if (!table) return;
  var tbody = table.tBodies[0], rows = Array.prototype.slice.call(tbody.rows), dir = {};
  var fac = 'all', pos = 'all';
  function press(sel, on){ document.querySelectorAll(sel).forEach(function(x){ var isOn = x === on; x.classList.toggle('on', isOn); x.setAttribute('aria-pressed', isOn ? 'true' : 'false'); }); }
  function apply(){
    var k = 0;
    rows.forEach(function(r){ var ok = (fac === 'all' || r.getAttribute('data-f') === fac) && (pos === 'all' || r.getAttribute('data-p') === pos) && (!table.__textMatch || table.__textMatch(r)); if (ok) { r.removeAttribute('data-x'); k++; } else r.setAttribute('data-x', ''); });
    if (window.__karzatRerender) window.__karzatRerender(table, true); else document.getElementById('rn').textContent = k + ' / ' + rows.length;
    if (window.__karzatDimSeats) window.__karzatDimSeats(fac, pos);
  }
  table.__reapply = apply;
  document.querySelectorAll('button[data-fac]').forEach(function(b){ b.addEventListener('click', function(){ fac = b.getAttribute('data-fac'); press('button[data-fac]', b); apply(); }); });
  document.querySelectorAll('button[data-posf]').forEach(function(b){ b.addEventListener('click', function(){ pos = b.getAttribute('data-posf'); press('button[data-posf]', b); apply(); }); });
  var heads = table.querySelectorAll('th.sortable');
  heads.forEach(function(th, i){
    th.setAttribute('aria-sort', 'none');
    var btn = document.createElement('button'); btn.type = 'button'; btn.className = 'sortbtn';
    while (th.firstChild) btn.appendChild(th.firstChild);
    th.appendChild(btn);
    function sort(){
      var d = dir[i] = -(dir[i] || -1);
      var key = th.getAttribute('data-key');
      function val(r){ return key === 'text' ? (r.cells[0].textContent || '').trim() : (r.getAttribute('data-' + key) || ''); }
      rows.sort(function(a, b){ var x = val(a), y = val(b); var nx = parseFloat(x), ny = parseFloat(y); if (!isNaN(nx) && !isNaN(ny)) return (nx - ny) * d; return x.localeCompare(y, 'hu') * d; });
      rows.forEach(function(r){ tbody.appendChild(r); });
      heads.forEach(function(h){ h.setAttribute('aria-sort', h === th ? (d > 0 ? 'ascending' : 'descending') : 'none'); });
      if (window.__karzatRerender) window.__karzatRerender(table, false);
    }
    btn.addEventListener('click', sort);
  });
  apply();
})();


(function(){
  var t = document.getElementById('mine'); if (!t) return;
  var rows = Array.prototype.slice.call(t.tBodies[0].rows), n = document.getElementById('rn'), al = 'all', yr = 'all';
  function apply(){ var k = 0; rows.forEach(function(r){ var ok = (al === 'all' || r.getAttribute('data-al') === al) && (yr === 'all' || r.getAttribute('data-y') === yr) && (!t.__textMatch || t.__textMatch(r)); if (ok) { r.removeAttribute('data-x'); k++; } else r.setAttribute('data-x', ''); }); if (window.__karzatRerender) window.__karzatRerender(t, true); else n.textContent = k + ' / ' + rows.length; }
  t.__reapply = apply;
  function press(sel, on){ document.querySelectorAll(sel).forEach(function(x){ var isOn = x === on; x.classList.toggle('on', isOn); x.setAttribute('aria-pressed', isOn ? 'true' : 'false'); }); }
  document.querySelectorAll('button[data-alf]').forEach(function(b){ b.addEventListener('click', function(){ al = b.getAttribute('data-alf'); press('button[data-alf]', b); apply(); }); });
  document.querySelectorAll('button[data-yf]').forEach(function(b){ b.addEventListener('click', function(){ yr = b.getAttribute('data-yf'); press('button[data-yf]', b); apply(); }); });
  apply();
})();


(function(){
  // The citation names the page by its site-root path when no deployed origin was configured at build time;
  // in a browser the origin is known, so print the address the reader is actually looking at.
  var sc = document.querySelector('script[src$="assets/karzat.js"]'), root = sc ? sc.getAttribute('src').replace(/assets\/karzat\.js$/, '') : '';
  document.querySelectorAll('.cite[data-path]').forEach(function(sec){
    var p = sec.getAttribute('data-path'); if (!p || /^[a-z]+:/i.test(p)) return;
    var abs; try { abs = new URL(root + p, location.href).href; } catch (e) { return; }
    sec.querySelectorAll('pre').forEach(function(pre){ pre.textContent = pre.textContent.split(p).join(abs); });
  });
  document.querySelectorAll('.cite [data-copy]').forEach(function(b){
    b.addEventListener('click', function(){
      var pre = b.parentNode.querySelector('pre'); if (!pre) return;
      var done = function(){ b.classList.add('done'); b.textContent = 'másolva'; setTimeout(function(){ b.classList.remove('done'); b.textContent = 'másolás'; }, 1500); };
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(pre.textContent).then(done, function(){});
      else { var r = document.createRange(); r.selectNodeContents(pre); var sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(r); try { document.execCommand('copy'); done(); } catch (e) {} }
    });
  });
})();


(function(){
  var q = document.getElementById('sq'), out = document.getElementById('sres'), n = document.getElementById('sn'); if (!q || !out) return;
  var items = null, loading = false, kind = 'all', cyc = 'all';
  function fold(s){ return String(s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, ''); }
  function esc(s){ return String(s == null ? '' : s).replace(/[&<>"]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  var failed = false;
  function load(cb){ if (items) return cb(); if (loading) return; loading = true; var x = new XMLHttpRequest(); x.open('GET', 'index.json'); x.onload = function(){ try { items = JSON.parse(x.responseText); } catch (e) { items = []; failed = true; } items.forEach(function(it){ it.f = fold(it.t) + ' ' + fold(it.s); it.ft = fold(it.t); }); cb(); }; x.onerror = function(){ items = []; failed = true; cb(); }; x.send(); }
  var urlTimer = null;
  function syncUrl(){ clearTimeout(urlTimer); urlTimer = setTimeout(function(){ if (!history.replaceState) return; var v = q.value.trim(); history.replaceState(null, '', v ? '?q=' + encodeURIComponent(v) : location.pathname); }, 300); }
  var KIND = {iromany: 'iromány', kepviselo: 'képviselő', szemely: 'pályakép'};
  function render(){
    var terms = fold(q.value).split(/\s+/).filter(Boolean);
    if (!terms.length) { out.innerHTML = '<tr><td colspan="3" class="hero-meta">Kezdj el gépelni.</td></tr>'; n.textContent = ''; return; }
    if (failed) { out.innerHTML = '<tr><td colspan="3" class="hero-meta">A keresőindex (index.json) nem tölthető be — a listák külön oldalakon: irományok, képviselők, személyek.</td></tr>'; n.textContent = ''; return; }
    var hits = [];
    for (var i = 0; i < items.length; i++) {
      var it = items[i];
      if (kind !== 'all' && it.k !== kind) continue;
      if (cyc !== 'all' && String(it.c) !== cyc && it.k !== 'szemely') continue;
      var ok = true, score = 0;
      for (var j = 0; j < terms.length; j++) { var t = terms[j]; if (it.f.indexOf(t) < 0) { ok = false; break; } if (it.ft === t) score += 3; else if (it.ft.indexOf(t) === 0) score += 2; else if (it.ft.indexOf(t) >= 0) score += 1; }
      if (ok) hits.push([score, it]);
    }
    hits.sort(function(a, b){ return b[0] - a[0] || (b[1].d || '').localeCompare(a[1].d || ''); });
    var shown = hits.slice(0, 200);
    out.innerHTML = shown.map(function(h){ var it = h[1]; return '<tr><td><a href="../' + esc(it.u) + '">' + esc(it.t) + '</a><span class="sub">' + esc(it.s) + '</span></td><td class="mono">' + esc(KIND[it.k] || it.k) + (it.n ? ' · ' + it.n + ' szavazás' : '') + '</td><td class="mono">' + (it.c ? it.c + '.' : '—') + '</td></tr>'; }).join('') || '<tr><td colspan="3" class="hero-meta">Nincs találat.</td></tr>';
    n.textContent = hits.length + ' találat' + (hits.length > 200 ? ' (az első 200)' : '');
  }
  q.addEventListener('input', function(){ syncUrl(); load(render); });
  document.querySelectorAll('button[data-sk]').forEach(function(b){ b.addEventListener('click', function(){ kind = b.getAttribute('data-sk'); document.querySelectorAll('button[data-sk]').forEach(function(x){ var on = x === b; x.classList.toggle('on', on); x.setAttribute('aria-pressed', on ? 'true' : 'false'); }); load(render); }); });
  document.querySelectorAll('button[data-sc]').forEach(function(b){ b.addEventListener('click', function(){ cyc = b.getAttribute('data-sc'); document.querySelectorAll('button[data-sc]').forEach(function(x){ var on = x === b; x.classList.toggle('on', on); x.setAttribute('aria-pressed', on ? 'true' : 'false'); }); load(render); }); });
  if (location.search) { var m = /[?&]q=([^&]+)/.exec(location.search); if (m) { q.value = decodeURIComponent(m[1].replace(/\+/g, ' ')); load(render); } }
})();


(function(){
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  if (document.hidden) return;                       // a background tab gets the finished page, not a stalled boot
  var CH = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
  function rnd(){ return CH[Math.floor(Math.random()*CH.length)]; }
  function ease(t){ return 1 - Math.pow(1 - t, 3); }
  // panels: fade and settle in
  var panels = Array.prototype.slice.call(document.querySelectorAll('.panel, .counts .c, .fresh, .kz-terminal'));
  panels.forEach(function(p, i){ p.style.opacity = '0'; p.style.transform = 'scale(.985)'; p.style.transition = 'opacity .35s ease-out, transform .45s cubic-bezier(.2,.7,.2,1)'; setTimeout(function(){ p.style.opacity = ''; p.style.transform = ''; }, 60 + i * 35); });
  // seats: pop in, inner rows first
  var seats = Array.prototype.slice.call(document.querySelectorAll('.chart svg .seat'));
  if (seats.length && seats.length < 600) {
    seats.forEach(function(g){ g.style.transition = 'none'; g.style.opacity = '0'; g.style.transform = 'scale(.3)'; g.style.transformBox = 'fill-box'; g.style.transformOrigin = 'center'; });
    seats.forEach(function(g, i){ setTimeout(function(){ g.style.transition = 'opacity .28s ease-out, transform .38s cubic-bezier(.2,.8,.2,1.2)'; g.style.opacity = ''; g.style.transform = ''; setTimeout(function(){ g.style.transition = ''; g.style.transformBox = ''; g.style.transformOrigin = ''; }, 450); }, 250 + i * 4); });
  }
  // labels (mono, uppercase only): scramble in — digits and punctuation stay put
  document.querySelectorAll('[data-kz-text]').forEach(function(el, i){
    var txt = el.textContent, n = txt.length; if (!n || n > 120) return;
    var t0 = null, dur = 320 + Math.min(n, 40) * 8, delay = 200 + i * 40;
    function step(ts){ if (!t0) t0 = ts; var k = ease(Math.min(1, (ts - t0) / dur)) * n, out = ''; for (var j = 0; j < n; j++){ var c = txt[j]; out += (j < k || /[\s\d.,:;–—\-\/()·|%]/.test(c)) ? c : rnd(); } el.textContent = out; if (k < n) requestAnimationFrame(step); else el.textContent = txt; }
    setTimeout(function(){ requestAnimationFrame(step); }, delay);
  });
  // numbers: count up to the printed value, formatted exactly as printed
  document.querySelectorAll('[data-kz-number]').forEach(function(el, i){
    var text = el.textContent, m = /^([^\d]*)(\d[\d\s.,]*)(.*)$/.exec(text); if (!m) return;
    var target = parseFloat(m[2].replace(/[\s,]/g, '')); if (isNaN(target)) return;
    var prefix = m[1], suffix = m[3], t0 = null, dur = 900, delay = 300 + i * 30, isInt = Math.round(target) === target;
    function fmt(v){ return prefix + (isInt ? String(Math.round(v)) : v.toFixed(1)) + suffix; }
    function step(ts){ if (!t0) { t0 = ts; } var p = ease(Math.min(1, (ts - t0) / dur)); el.textContent = p < 1 ? fmt(target * p) : text; if (p < 1) requestAnimationFrame(step); }
    setTimeout(function(){ requestAnimationFrame(function(ts){ el.textContent = fmt(0); step(ts); }); }, delay);
  });
  // terminal: type the log lines, cursor at the end of the last one
  var log = document.querySelector('.kz-terminal .log');
  if (log) {
    var spans = Array.prototype.slice.call(log.querySelectorAll('span')), lines = spans.map(function(s){ return s.textContent; });
    spans.forEach(function(s){ s.textContent = ''; });
    var cur = document.createElement('i'); cur.className = 'cursor';
    var li = 0, ci = 0;
    function tick(){ if (li >= lines.length){ if (spans.length) spans[spans.length - 1].appendChild(cur); return; } var line = lines[li]; ci++; spans[li].textContent = line.slice(0, ci); if (ci >= line.length){ li++; ci = 0; setTimeout(tick, 120); } else setTimeout(tick, 9); }
    setTimeout(tick, 500);
  }
})();
