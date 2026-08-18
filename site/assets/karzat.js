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
  window.__karzatRerender = function(table, reset){ if (table && table.__pager) table.__pager.render(reset); else if (table) { var rows = Array.prototype.slice.call(table.tBodies[0].rows); rows.forEach(function(r){ r.hidden = r.hasAttribute('data-x'); }); } };
})();


(function(){
  // <input data-filter-table="id"> narrows a table by text (accent-insensitive); the button filters of the same
  // table consult table.__textMatch, so both narrow together.
  function fold(s){ return String(s || '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, ''); }
  document.querySelectorAll('input[data-filter-table]').forEach(function(inp){
    var table = document.getElementById(inp.getAttribute('data-filter-table')); if (!table || !table.tBodies[0]) return;
    var rows = Array.prototype.slice.call(table.tBodies[0].rows);
    rows.forEach(function(r){ r.__hay = fold(r.textContent); });
    table.__textq = '';
    table.__textMatch = function(r){ return !table.__textq || (r.__hay || fold(r.textContent)).indexOf(table.__textq) >= 0; };
    inp.addEventListener('input', function(){
      table.__textq = fold(inp.value).trim();
      if (table.__reapply) { table.__reapply(); return; }
      rows.forEach(function(r){ if (table.__textMatch(r)) r.removeAttribute('data-x'); else r.setAttribute('data-x', ''); });
      if (window.__karzatRerender) window.__karzatRerender(table, true);
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
    box.innerHTML = '<div class="row1"><span class="name"><a href="' + mpBase + esc(az) + '.html">' + esc(name) + '</a></span>' +
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
