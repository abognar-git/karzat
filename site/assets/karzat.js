// First statement in the bundle, before anything can throw. Controls that only a script can operate were
// shipped as static markup on 5,146 pages — filter and sort buttons, complete with `aria-pressed`, so a
// screen reader announced a state that could never change and a reader without JavaScript was offered
// affordances that did nothing. The stylesheet hides them until this line runs, which is the only honest
// signal that they will work.
(function(){
  document.documentElement.classList.add('js');
  // …and the gated controls get their toggle state here rather than from the builder. The stylesheet hides
  // them until the line above runs, but markup outlives a stylesheet: reader mode and text browsers drop the
  // CSS and keep the HTML, and a <button> there is once again a control announced to a
  // screen reader that nobody can operate. The builder ships the class ("on") that says which one is current;
  // the ARIA state is this script's to make, because this script is what makes it true.
  // Only groups that actually toggle. `.filters` is the site's generic control row and also holds pure
  // action buttons — the Riport page's nine SQL examples fill a textarea and are not states — so a group
  // earns aria-pressed by containing a current choice (the builder's `on` class), and one that has none
  // gets nothing. Announcing "not pressed" on a button that runs an example is the same fault one level up.
  var groups = document.querySelectorAll('.filters');
  for (var g = 0; g < groups.length; g++) {
    if (!groups[g].querySelector('button.on')) continue;
    var f = groups[g].querySelectorAll('button');
    for (var i = 0; i < f.length; i++) f[i].setAttribute('aria-pressed', f[i].classList.contains('on') ? 'true' : 'false');
  }
})();


(function(){
  // The month strip is a row of real links; this makes hovering or focusing one worth doing before you
  // click it. The readout says what the House did that month and offers the two ways onward, so the charts
  // above stop being a picture you can only look at. Nothing here is required: without it every month is
  // still a link to that month's votes.
  var strip = document.querySelector('.mostrip'); if (!strip) return;
  var out = document.getElementById('moread'); if (!out) return;
  var hint = out.innerHTML, cur = null;
  function show(a){
    if (a === cur) return;
    if (cur) cur.classList.remove('on');
    cur = a; if (!a) { out.innerHTML = hint; return; }
    a.classList.add('on');   // a highlight, not aria-current: the mouse being over a link does not make it the current one
    // The link's own name, read back rather than reassembled. Writing the sentence twice meant writing the
    // rule for "a" against "az" twice as well — a rule about how a numeral is said, not how it is spelt —
    // and the second copy printed "a 1 döntésből". One sentence, built by hu_the() in the builder.
    var t = a.getAttribute('aria-label') || '', i = t.indexOf(' — ');
    var b = document.createElement('b');
    b.textContent = i < 0 ? t : t.slice(0, i);
    var link = document.createElement('a');
    link.href = a.getAttribute('href');
    link.textContent = 'a hónap szavazásai';
    out.textContent = '';
    out.appendChild(b);
    out.appendChild(document.createTextNode((i < 0 ? '' : t.slice(i)) + '  → '));
    out.appendChild(link);
  }
  strip.addEventListener('mouseover', function(e){ var a = e.target.closest('.mo'); if (a) show(a); });
  strip.addEventListener('focusin', function(e){ var a = e.target.closest('.mo'); if (a) show(a); });
  strip.addEventListener('mouseleave', function(){ show(null); });
  strip.addEventListener('focusout', function(e){
    if (!strip.contains(e.relatedTarget)) show(null);   // the readout stopped naming a month nobody was on
  });

  // The arrow keys, because the panel promised them. The heading said "lépkedj a nyilakkal" and only
  // mouseover, focusin and mouseleave were ever bound, so the arrows scrolled the page — a control that is
  // dead WITH a script, which is worse than the no-JS case this project writes its rules against. The tag and
  // the readout hint are set here rather than in the markup, so the promise is made by the code that keeps it.
  var tag = document.getElementById('motag');
  if (tag) tag.textContent = 'vidd rá az egeret, vagy lépkedj a nyilakkal';
  out.textContent = 'Válassz egy hónapot: itt jelenik meg, mit csinált a Ház akkor — és innen tovább lehet '
                  + 'menni a hónap szavazásaihoz.';
  hint = out.innerHTML;
  var mos = Array.prototype.slice.call(strip.querySelectorAll('.mo'));
  strip.setAttribute('role', 'group');
  strip.setAttribute('aria-label', 'A ciklus hónapjai — ' + mos.length + ' hónap');
  // A roving tab stop, applied by the script: without it every month keeps its own, which is the right
  // fallback. 39 stops become one, and the arrows walk the rest.
  mos.forEach(function(a, i){ a.tabIndex = i ? -1 : 0; });
  strip.addEventListener('keydown', function(e){
    var i = mos.indexOf(document.activeElement);
    if (i < 0) return;
    var j = e.key === 'ArrowRight' ? i + 1 : e.key === 'ArrowLeft' ? i - 1
          : e.key === 'Home' ? 0 : e.key === 'End' ? mos.length - 1 : -1;
    if (j < 0 && e.key !== 'Home') return;
    // Clamped, not wrapped. The chamber's seat walker wraps because a chamber is a ring; this is a timeline,
    // and ArrowRight on the last month of a cycle must not land on the first.
    j = Math.max(0, Math.min(mos.length - 1, j));
    e.preventDefault();
    mos[i].tabIndex = -1; mos[j].tabIndex = 0; mos[j].focus();
  });
})();

(function(){
  // The legend is a colour key in the markup and becomes a filter here — a span upgraded to a button rather
  // than a button shipped and hoped for. Three of the five charts draw ten overlapping lines, which is a
  // hairball nobody can read; one click leaves one faction visible on all of them at once, because the
  // question is almost always about one faction and never about the tangle.
  // Two guards, because `.legend .f[data-f]` is not unique to this page and the first version was happy to
  // take either of the others. The cycle index carries the same legend as a plain colour key beside its
  // chamber and has no series at all — upgrading it would have produced ten buttons that do nothing, which is
  // the exact fault this round has been chasing. The landing page's legend already filters the seats, and its
  // entries are real buttons carrying data-hf; a second click handler on top of that is worse than useless.
  var page = document.querySelector('.kz-main') || document.body;
  if (!page.querySelector('[data-s]')) return;
  var legend = document.querySelector('.legend'); if (!legend) return;
  var items = Array.prototype.slice.call(legend.querySelectorAll('.f[data-f]'))
    .filter(function(b){ return b.tagName === 'SPAN' && !b.hasAttribute('data-hf'); });
  if (!items.length) return;
  var only = 'all', known = {};
  items.forEach(function(b){ known[b.getAttribute('data-f')] = 1; });
  function apply(){
    // Only what the legend names. The qualified-majority line is a series like any other but belongs to no
    // faction, and the first version dimmed it too: choosing Fidesz made an unrelated chart nearly vanish.
    page.querySelectorAll('[data-s]').forEach(function(el){
      var s = el.getAttribute('data-s');
      el.classList.toggle('dim', only !== 'all' && known[s] === 1 && s !== only);
    });
    items.forEach(function(b){ b.setAttribute('aria-pressed', String(only === b.getAttribute('data-f'))); });
  }
  legend.classList.add('pick');
  var hint = document.createElement('span');
  hint.className = 'lg-hint';
  hint.textContent = '— kattints egyre, és csak az marad az ábrákon';
  legend.appendChild(hint);
  items.forEach(function(b){
    b.setAttribute('role', 'button');
    b.setAttribute('tabindex', '0');
    b.setAttribute('aria-pressed', 'false');
    b.title = b.getAttribute('data-f') + ' kiemelése — még egy kattintás visszahozza mindet';
    function toggle(){ only = (only === b.getAttribute('data-f')) ? 'all' : b.getAttribute('data-f'); apply(); }
    b.addEventListener('click', toggle);
    b.addEventListener('keydown', function(e){ if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); } });
  });
  apply();
})();


(function(){
  // The charts' points already carry their exact figures as <title> children — the browser shows them, but
  // slowly, and only when the cursor lands on a four-pixel dot. This reads the same titles and shows the
  // nearest point's figure instantly, wherever the cursor is over the chart. Pointer-only by design: the
  // same numbers are in the table below and in every point's title, so a keyboard or touch reader loses
  // nothing that this adds.
  if (!window.matchMedia || !matchMedia('(hover:hover)').matches) return;
  document.querySelectorAll('.tswrap').forEach(function(wrap){
    var svg = wrap.querySelector('svg.ts'); if (!svg) return;
    var tip = null, pts = null, raf = 0;
    function collect(){
      pts = [];
      svg.querySelectorAll('circle,rect').forEach(function(el){
        var t = el.querySelector('title'); if (!t) return;
        var r = el.getBoundingClientRect();
        pts.push({ el: el, x: r.left + r.width / 2, y: r.top + Math.min(r.height / 2, 8), text: t.textContent });
      });
    }
    function hide(){ if (tip) { tip.remove(); tip = null; } pts = null; }
    wrap.addEventListener('mouseenter', collect);
    wrap.addEventListener('mouseleave', hide);
    wrap.addEventListener('scroll', hide);            // positions cached in client space go stale on scroll
    wrap.addEventListener('mousemove', function(e){
      if (raf) return;
      raf = requestAnimationFrame(function(){
        raf = 0;
        if (!pts) collect();
        var best = null, bd = Infinity;
        for (var i = 0; i < pts.length; i++) {
          var p = pts[i];
          if (p.el.classList.contains('dim')) continue;      // the legend filter hides these; so does the tip
          var dx = p.x - e.clientX, dy = p.y - e.clientY, d = dx * dx + dy * dy / 4;
          if (d < bd) { bd = d; best = p; }
        }
        if (!best) { hide(); return; }
        if (!tip) {
          tip = document.createElement('div');
          tip.className = 'tstip';
          tip.setAttribute('aria-hidden', 'true');
          wrap.appendChild(tip);
        }
        tip.textContent = best.text;
        var wr = wrap.getBoundingClientRect();
        var left = best.x - wr.left + wrap.scrollLeft, top = best.y - wr.top - 26;
        tip.style.left = Math.max(0, Math.min(left - tip.offsetWidth / 2, wrap.scrollWidth - tip.offsetWidth)) + 'px';
        tip.style.top = Math.max(0, top) + 'px';
      });
    });
  });
})();


(function(){
  // The kind names carry their plain-word definition in data-def; hovering one shows it in the same box the
  // charts use for their readout. Pointer-only: the identical text sits in the "Mit jelentenek a jogcímek?"
  // glossary right above the table, which is the keyboard, touch and no-script path.
  if (window.matchMedia && matchMedia('(hover:hover)').matches) {
    var tip = null;
    document.addEventListener('mouseover', function(e){
      var t = e.target.closest && e.target.closest('.term');
      if (!t) { if (tip) { tip.remove(); tip = null; } return; }
      if (!tip) {
        tip = document.createElement('div');
        tip.className = 'tstip';
        tip.style.maxWidth = '340px';
        tip.style.whiteSpace = 'normal';
        tip.setAttribute('aria-hidden', 'true');
      }
      tip.textContent = t.getAttribute('data-def') || '';
      var host = t.closest('.tablewrap') || t.parentElement;
      if (getComputedStyle(host).position === 'static') host.style.position = 'relative';
      host.appendChild(tip);
      var hr = host.getBoundingClientRect(), tr = t.getBoundingClientRect();
      tip.style.left = Math.max(0, tr.left - hr.left + host.scrollLeft) + 'px';
      var above = tr.top - hr.top - tip.offsetHeight - 6;
      tip.style.top = (above >= 0 ? above : tr.bottom - hr.top + 6) + 'px';
    });
  }
})();

(function(){
  // The jogcím panel ships one finished table per faction and this only chooses which one is visible: no
  // arithmetic runs here, so the browser cannot disagree with the builder. The buttons live in a .filters
  // block, which the stylesheet keeps hidden until html.js — without a script there is one table and no
  // control pointing at the others.
  var box = document.getElementById('jogcim'); if (!box) return;
  var btns = box.querySelectorAll('button[data-fft]'); if (!btns.length) return;
  btns.forEach(function(b){
    b.addEventListener('click', function(){
      var want = b.getAttribute('data-fft');
      box.querySelectorAll('.ffv').forEach(function(v){ v.hidden = v.getAttribute('data-ffv') !== want; });
      btns.forEach(function(x){ var on = x === b; x.classList.toggle('on', on); x.setAttribute('aria-pressed', on ? 'true' : 'false'); });
    });
  });
})();


(function(){
  // Every table with data-page-size gets a pager: 25 rows at a time, prev / next / page numbers / 'mind'.
  // Filters mark excluded rows with data-x and call table.__pager.render(true); sorters re-append rows and
  // call render(false). Without JS the whole table is in the page.
  function paginate(table){
    var per0 = parseInt(table.getAttribute('data-page-size') || '25', 10), per = per0, page = 1;
    var tbody = table.tBodies[0]; if (!tbody) return;
    var counter = table.getAttribute('data-counter') ? document.getElementById(table.getAttribute('data-counter')) : null;
    var wrap = table.closest('.tablewrap') || table;
    var nav = document.createElement('nav'); nav.className = 'pgr';
    // Two paginated tables on one page produced two <nav>s called 'Lapozás', which in a landmark list is
    // two entries a reader cannot tell apart. The table's own caption or the heading above it names them.
    var named = (table.caption && table.caption.textContent.trim())
             || (function(sec){ var h = sec && sec.querySelector('h2'); return h ? h.textContent.trim() : ''; })(table.closest('section'));
    nav.setAttribute('aria-label', named ? 'Lapozás: ' + named.slice(0, 60) : 'Lapozás');
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
  // The footer's fact rotates on a click. The pool is fetched once from the site root and cached in the tab, so a
  // reader who keeps clicking never pays for it twice; with JavaScript off the page's own fact stands as built.
  var line = document.querySelector('.factline'); if (!line) return;
  var btn = line.querySelector('[data-fact-next]'), holder = line.querySelector('[data-fact]');
  if (!btn || !holder) return;
  var sc = document.querySelector('script[src$="assets/karzat.js"]');
  var root = sc ? sc.getAttribute('src').replace(/assets\/karzat\.js$/, '') : '';
  var pool = null, i = -1;
  function esc(s){ return String(s == null ? '' : s).replace(/[&<>"]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  function show(){
    if (!pool || !pool.length) return;
    i = (i + 1) % pool.length;
    var f = pool[i];
    holder.innerHTML = (f.href ? '<a href="' + esc(root + f.href) + '">' + esc(f.hu) + '</a>' : esc(f.hu))
      + ' <i class="sub">' + esc(f.scope || '') + '</i>';
  }
  btn.addEventListener('click', function(){
    if (pool) return show();
    var x = new XMLHttpRequest(); x.open('GET', root + 'tenyek.json');
    x.onload = function(){ try { pool = (JSON.parse(x.responseText) || {}).facts || []; } catch (e) { pool = []; }
      if (!pool.length) { btn.remove(); return; }
      var here = holder.textContent.trim();
      for (var k = 0; k < pool.length; k++) if (here.indexOf(pool[k].hu.slice(0, 24)) === 0) i = k;   // continue from the one on the page
      show(); };
    x.onerror = function(){ btn.remove(); };
    x.send();
  });
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
  // is sharded by the token's first two letters (idx2/xx.json; the digit is a format version, see the builder), the results table lists the speeches newest first and
  // fetches the visible pages' texts for a snippet.
  var q = document.getElementById('spq'), body = document.getElementById('spres'), n = document.getElementById('spn'), table = body && body.closest('table');
  var byDate = document.getElementById('spdate');
  var SHOW_MAX = 200;        // rows built into the table; the pager walks these twenty at a time
  if (!q || !body) return;
  body.innerHTML = '<tr><td colspan="3" class="hero-meta">Kezdj el gépelni (legalább három betű).</td></tr>';
  function fold(s){ return String(s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase(); }
  function esc(s){ return String(s == null ? '' : s).replace(/[&<>"]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  var meta = null, metaFailed = false, metaWaiters = [], shards = {}, texts = {}, seq = 0;
  function get(url, cb){ var x = new XMLHttpRequest(); x.open('GET', url); x.onload = function(){ if (x.status >= 400) return cb(null); try { cb(JSON.parse(x.responseText)); } catch (e) { cb(null); } }; x.onerror = function(){ cb(null); }; x.send(); }
  function shard2(key, cb){ if (shards[key] !== undefined) return cb(shards[key]); get('idx2/' + key + '.json', function(d){ shards[key] = d || {}; cb(shards[key]); }); }
  function shard(term, cb){ shard2(term.slice(0, 2), function(sh){ if (sh && sh.__split) shard2(term.slice(0, 3), cb); else cb(sh); }); }   // a big two-letter shard is split by the third letter
  function withMeta(cb){ if (meta) return cb(); if (metaFailed) return cb(); metaWaiters.push(cb); if (metaWaiters.length > 1) return; get('meta2.json', function(d){ if (d) meta = d; else metaFailed = true; var w = metaWaiters; metaWaiters = []; w.forEach(function(f){ f(); }); }); }
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
        // One typed word can match many indexed tokens by prefix, and BM25 wants them treated as one term:
        // its frequency in a document is the sum over the tokens that matched, and its document frequency is
        // the number of documents any of them reached.
        var tf = {};
        for (var k in sh) if (k !== '__split' && k.indexOf(t) === 0) {
          var arr = sh[k];
          for (var j = 0; j + 1 < arr.length; j += 2) tf[arr[j]] = (tf[arr[j]] || 0) + arr[j + 1];
        }
        sets[i] = tf; step();
      });
    });
    function render(){
      if (!meta) { body.innerHTML = '<tr><td colspan="3" class="hero-meta">A kereső listája (meta2.json) nem tölthető be.</td></tr>'; n.textContent = ''; return; }
      // Ranked, not filtered. Requiring every word and then ordering by date is what this page did, and on
      // 492 questions the record answers for itself it put the right speech in the first ten 7.7% of the
      // time — a long question matched almost nothing, and what it matched came back by accident of date.
      // BM25 over the same index finds 40.6% — the figure the page quotes comes from
      // data/derived/search_bench.json, and this comment is the last place a copy of it lives by hand. A word that appears in half the speeches counts for little; a
      // word that appears in twelve counts for a great deal; a short speech saying it twice beats a long one
      // saying it twice. Nothing here is new to information retrieval, and none of it was on this page.
      var N = meta.length, k1 = 1.5, bb = 0.75, avg = 0;
      for (var i = 0; i < N; i++) avg += (meta[i][7] || 0);
      avg = avg / Math.max(1, N) || 1;
      var score = {}, any = false;
      for (var s = 0; s < sets.length; s++) {
        var df = 0, tfs = sets[s];
        for (var kk in tfs) df++;
        if (!df) continue;
        var idf = Math.log(1 + (N - df + 0.5) / (df + 0.5));
        for (var d in tfs) {
          var f = tfs[d], dl = meta[d][7] || avg;
          score[d] = (score[d] || 0) + idf * f * (k1 + 1) / (f + k1 * (1 - bb + bb * dl / avg));
          any = true;
        }
      }
      var hits = [];
      if (any) { for (var d2 in score) hits.push(+d2);
                 hits.sort(function(a, b){ return score[b] - score[a] || b - a; }); }
      if (byDate && byDate.checked) hits.sort(function(a, b){ return b - a; });   // ids are chronological
      // Dropping the requirement that every word appear turns a sentence-length question into most of the
      // corpus: "a magyar gazdaság helyzete és a jövő évi költségvetés" scores 19,602 of cycle 40's 24,365
      // speeches, and building a row for each of them is five megabytes of HTML into innerHTML — the tab
      // stops responding. Ranked results have a long tail by construction and nobody reads past the first
      // page of it; the cut is stated rather than silent, because a hidden cut is how a search comes to look
      // like it found nothing beyond what it showed.
      var scored = hits.length;
      if (hits.length > SHOW_MAX) hits = hits.slice(0, SHOW_MAX);
      body.innerHTML = hits.map(function(i){ var m = meta[i]; var who = m[3] ? '<a href="../kepviselo/' + esc(m[3]) + '.html">' + esc(m[2]) + '</a>' : esc(m[2]);
        return '<tr data-i="' + i + '"><td class="ts mono"><a href="' + esc(m[0]) + '.html">' + esc(m[1]) + '</a></td><td>' + who + '<span class="sub">' + esc(m[4] || '') + (m[6] ? ' · ' + esc(m[6]) : '') + '</span></td><td>' + esc(m[5] || '') + '<span class="snip"></span></td></tr>'; }).join('')
        || '<tr><td colspan="3" class="hero-meta">Nincs találat.</td></tr>';
      n.textContent = (scored > hits.length
        ? 'a legjobb ' + hits.length + ' a ' + scored.toLocaleString('hu-HU') + ' találatból'
        : hits.length + ' találat')
        + (any && !(byDate && byDate.checked) ? ' · relevancia szerint' : '');
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
  // The order switch had no listener at all: ticking it changed nothing until the reader happened to
  // type another character, at which point the results silently rearranged themselves. A control that
  // does nothing when used is worse than one that is not there.
  if (byDate) byDate.addEventListener('change', search);
  q.addEventListener('input', function(){ syncUrl(); search(); });
  if (table) table.addEventListener('click', function(e){ if (e.target.closest && e.target.closest('button')) setTimeout(function(){ snippets((fold(q.value).match(/[a-z0-9]{3,}/g) || [])); }, 50); });
  document.addEventListener('click', function(e){ var b = e.target.closest && e.target.closest('nav.pgr button'); if (b && table && b.closest('nav.pgr') && b.closest('nav.pgr').previousElementSibling && b.closest('nav.pgr').previousElementSibling.contains(table)) setTimeout(function(){ snippets((fold(q.value).match(/[a-z0-9]{3,}/g) || [])); }, 50); });
  if (location.search) { var m = /[?&]q=([^&]+)/.exec(location.search); if (m) { q.value = decodeURIComponent(m[1].replace(/\+/g, ' ')); search(); } }
})();


(function(){
  // kepviselom/index.html: a settlement or a postcode (Budapest: a district) → its OEVK(s) → the MP; the lists are
  // telepules.json and iranyitoszam.json, loaded on the first keystroke; a city the annex splits by streets lists
  // every candidate and says the address decides. A postcode names a settlement, never a district of the annex's own.
  var q = document.getElementById('town'), out = document.getElementById('townres'), mapEl = document.getElementById('oevk-map'); if (!q || !out || !mapEl) return;
  var map; try { map = JSON.parse(mapEl.textContent); } catch (e) { return; }
  function fold(s){ return String(s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().trim(); }
  function esc(s){ return String(s == null ? '' : s).replace(/[&<>"]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  var data = null, loading = false, keys = null, zips = null;
  function get(url, cb){ var x = new XMLHttpRequest(); x.open('GET', url); x.onload = function(){ var v = null; try { v = JSON.parse(x.responseText); } catch (e) { v = null; } cb(v); }; x.onerror = function(){ cb(null); }; x.send(); }
  function load(cb){
    if (data) return cb();
    if (loading) return;
    loading = true;
    get('telepules.json', function(v){
      data = v || {}; keys = Object.keys(data).map(function(k){ return [fold(k), k]; });
      get('iranyitoszam.json', function(z){ zips = z || {}; cb(); });
    });
  }
  var urlTimer = null;
  function syncUrl(){ clearTimeout(urlTimer); urlTimer = setTimeout(function(){ if (!history.replaceState) return; var v = q.value.trim(); history.replaceState(null, '', v ? '?t=' + encodeURIComponent(v) : location.pathname); }, 300); }
  function render(){
    var raw = String(q.value || '').trim(), t = fold(raw);
    if (t.length < 2) { out.innerHTML = ''; return; }
    var zip = /^\d{4}$/.test(raw) ? raw : null, names = null, note = '';
    if (zip) {
      names = (zips && zips[zip]) || null;
      if (!names) { out.innerHTML = '<div class="hero-meta">Ehhez az irányítószámhoz nincs település a listánkban. Próbáld a település nevével.</div>'; return; }
      note = '<div class="hero-meta" style="margin-bottom:6px">' + esc(zip) + ' · ' + (names.length > 1 ? esc(names.length + ' település ezzel az irányítószámmal (közös posta)') : esc(names[0])) + '</div>';
    } else if (/^\d+$/.test(raw)) {
      out.innerHTML = '<div class="hero-meta">Az irányítószám négy számjegy — például 1114 vagy 9021.</div>'; return;
    }
    var hits;
    if (names) {
      hits = names.map(function(n){ return [fold(n), n]; });
    } else {
      var exact = keys.filter(function(k){ return k[0] === t; }), pre = keys.filter(function(k){ return k[0].indexOf(t) === 0 && k[0] !== t; }).slice(0, 12);
      hits = exact.concat(pre);
    }
    if (!hits.length) { out.innerHTML = '<div class="hero-meta">Nincs ilyen település a választókerületi mellékletben.</div>'; return; }
    out.innerHTML = note + hits.map(function(k){
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
  var PHOTO_BASE = '/assets/portre/', PHOTO_EXT = '.webp';   // our own grey copy, ~3 kB; the picture is
  // already the size and the tone it is drawn at, so nothing is downloaded or filtered that is not shown
  var mpBase = src.getAttribute('data-mp-base') || '';
  function esc(s){ return String(s == null ? '' : s).replace(/[&<>"]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  var seats = Array.prototype.slice.call(svg.querySelectorAll('.seat[data-az]'));
  seats.forEach(function(g, i){ g.setAttribute('tabindex', i === 0 ? '0' : '-1'); });
  function focusSeat(i){ if (!seats.length) return; i = (i + seats.length) % seats.length; seats.forEach(function(g, k){ g.setAttribute('tabindex', k === i ? '0' : '-1'); }); seats[i].focus(); }
  function render(az){
    var d = data[az]; if (!d) return;
    var name = d[0], fac = d[1], mandate = d[2], seat = d[3], cast = d[4], inroll = d[5], w = d[6], a = d[7], sp = d[8], com = d[9];
    var photo = d[10] || (PHOTO_BASE + esc(az) + PHOTO_EXT), credit = d[11] || 'fénykép: parlament.hu';   // the House's non-MP members are not on parlament.hu's portrait endpoint
    var office = d[12] || '';                            // Speaker, deputy Speaker, clerk — drawn on the platform too
    var sz = (fac === 'szószóló');                       // a nationality spokesperson: sits and speaks, never votes
    var km = (fac === 'kormánytag');                     // a member of the government without a mandate: the same
    var c = colours[fac] || '#8a8a8a';
    var part = inroll ? Math.round(100 * cast / inroll) : null, agree = (w + a) ? Math.round(100 * w / (w + a)) : null;
    var who = (sz || km) ? esc(name) : '<a href="' + mpBase + esc(az) + '.html">' + esc(name) + '</a>';
    box.innerHTML = '<img class="portrait insp" src="' + esc(photo) + '" alt="" width="195" height="260" loading="lazy" decoding="async" referrerpolicy="no-referrer" title="' + esc(credit) + '" onerror="this.remove()">' +
      '<div class="row1"><span class="name">' + who + '</span>' +
      '<span class="meta"><i class="d" style="--c:' + c + '"></i> ' + (sz ? 'nemzetiségi szószóló' : km ? 'kormánytag, nem képviselő' : esc(fac)) + (office ? ' · <b>' + esc(office) + '</b>' : '') + ' · ' + esc(mandate) + (seat ? ' · ' + esc(seat) : '') + '</span></div>' +
      '<div class="row2"><span class="rec"><span class="lbl">a ciklusban</span>' +
      (sz ? 'nem szavaz — a szószóló felszólalhat és bizottságban dolgozik' + (com ? ' · bizottság <b>' + com + '</b>' : '')
        : km ? 'nem szavaz — a kormány tagja mandátum nélkül; a miniszteri padban ül és felszólal'
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
  // the legend ships as inert spans (information without a script); the running script upgrades each into
  // the toggle button it binds below — no reader ever meets a focusable control that does nothing
  document.querySelectorAll('.chamber-today .legend span.f[data-hf]').forEach(function(sp){
    var b = document.createElement('button'); b.type = 'button'; b.className = sp.className;
    b.setAttribute('data-hf', sp.getAttribute('data-hf')); b.setAttribute('data-f', sp.getAttribute('data-f') || '');
    b.setAttribute('aria-pressed', 'false'); b.title = 'csak ' + sp.getAttribute('data-hf') + ' — mégegyszer: mind';
    b.innerHTML = sp.innerHTML; sp.parentNode.replaceChild(b, sp);
  });
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
  // A month can arrive in the address: the Számok charts point here, one bar to one month. Without a script
  // the link still lands on the list at #dir and shows all of it — less, but never a broken promise. The
  // banner says which month is showing and offers the way back out, because a filtered list that does not
  // say it is filtered is a list that lies about how much the House did.
  var month = (new URLSearchParams(location.search).get('m') || 'all');
  if (!/^\d{4}-\d{2}$/.test(month)) month = 'all';
  var hay = rows.map(function(r){ var more = r.querySelector('.more'); return ((r.textContent || '') + ' ' + (more ? more.getAttribute('title') : '')).toLowerCase(); });
  function press(sel, on){ document.querySelectorAll(sel).forEach(function(x){ var isOn = x === on; x.classList.toggle('on', isOn); x.setAttribute('aria-pressed', isOn ? 'true' : 'false'); }); }
  var table = tbody.closest('table');
  function render(){
    var k = 0;
    rows.forEach(function(r, i){
      var ok = (rule === 'all' || r.getAttribute('data-rule') === rule) && (result === 'all' || r.getAttribute('data-result') === result) && (year === 'all' || r.getAttribute('data-y') === year) && (month === 'all' || r.getAttribute('data-m') === month) && (!q || hay[i].indexOf(q) >= 0);
      if (ok) { r.removeAttribute('data-x'); k++; } else r.setAttribute('data-x', '');
    });
    if (window.__karzatRerender) window.__karzatRerender(table, true); else n.textContent = k + ' / ' + rows.length;
  }
  document.querySelectorAll('button[data-rule]').forEach(function(b){ b.addEventListener('click', function(){ rule = b.getAttribute('data-rule'); press('button[data-rule]', b); render(); }); });
  document.querySelectorAll('button[data-result]').forEach(function(b){ b.addEventListener('click', function(){ result = b.getAttribute('data-result'); press('button[data-result]', b); render(); }); });
  document.querySelectorAll('button[data-year]').forEach(function(b){ b.addEventListener('click', function(){ year = b.getAttribute('data-year'); press('button[data-year]', b); render(); }); });
  if (month !== 'all') {
    var host = document.getElementById('dir') || tbody.closest('section') || tbody.parentNode;
    var note = document.createElement('div');
    note.className = 'hero-meta prose';
    note.style.margin = '8px 0';
    var honap = ['január', 'február', 'március', 'április', 'május', 'június', 'július', 'augusztus', 'szeptember', 'október', 'november', 'december'];
    var mi = +month.slice(5, 7);
    var mnev = (mi >= 1 && mi <= 12) ? month.slice(0, 4) + '. ' + honap[mi - 1] : month;
    note.innerHTML = 'Ez a lista most csak egy hónapot mutat: <b>' + mnev + '</b> szavazásait. ' +
      '<a href="' + location.pathname + '#dir">a teljes lista: mind a ' + rows.length + ' szavazás</a>';
    host.insertBefore(note, host.firstChild);
  }
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
  var PHOTO_BASE = '/assets/portre/', PHOTO_EXT = '.webp';   // our own grey copy, ~3 kB; the picture is
  // already the size and the tone it is drawn at, so nothing is downloaded or filtered that is not shown
  var root = (document.querySelector('a.brand') || {}).getAttribute ? document.querySelector('a.brand').getAttribute('href').replace(/index\.html$/, '') : '';
  var mpBase = (svg.closest('body').querySelector('.pager') ? '../kepviselo/' : 'kepviselo/');
  function esc(s){ return String(s == null ? '' : s).replace(/[&<>"]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  function render(az){
    var d = data[az]; if (!d) return;
    var name = d[0], fac = d[1], mandate = d[2], seat = d[3], pos = d[4], cast = d[5], inroll = d[6], w = d[7], a = d[8], streak = d[9], office = d[10] || '';
    var c = colours[fac] || '#8a8a8a';
    var part = inroll ? Math.round(100 * cast / inroll) : 0, agree = (w + a) ? Math.round(100 * w / (w + a)) : null;
    var sq = '';
    for (var i = 0; i < streak.length; i++) { var ch = streak[i]; sq += '<i class="' + (ch === '.' ? 'x' : ch) + (i === streak.length - 1 ? ' now' : '') + '" style="--c:' + c + '"></i>'; }
    box.innerHTML = '<img class="portrait insp" src="' + PHOTO_BASE + esc(az) + PHOTO_EXT + '" alt="" width="192" height="256" loading="lazy" decoding="async" referrerpolicy="no-referrer" title="fénykép: parlament.hu" onerror="this.remove()">' +
      '<div class="row1"><span class="name"><a href="' + mpBase + esc(az) + '.html">' + esc(name) + '</a></span>' +
      '<span class="meta"><i class="d" style="--c:' + c + '"></i> ' + esc(fac) + (office ? ' · <b>' + esc(office) + '</b>' : '') + ' · ' + esc(mandate) + (seat ? ' · ' + esc(seat) : '') + '</span>' +
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
  // Each seat is a real link to that person's career page — that is what a keyboard and a reader without
  // JavaScript get. Where the script is running the card is the better answer, so the click is intercepted;
  // a modified click (new tab, new window) is left alone, because the reader asked for the page.
  svg.addEventListener('click', function(e){
    var g = e.target.closest('.seat[data-az]');
    if (!g) { unpin(); return; }
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
    e.preventDefault();
    var az = g.getAttribute('data-az'); if (pinned === az) unpin(); else pin(az);
  });
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
  // The same month counted two ways. Both charts are in the page already; this hides one and adds the switch.
  var panel = document.getElementById('szavazasszam'); if (!panel) return;
  var bar = panel.querySelector('.viewsw'); if (!bar) return;
  var views = Array.prototype.slice.call(panel.querySelectorAll('.view'));
  if (views.length < 2) return;
  // Out of the heading. A screen reader builds a heading's name from everything inside it, so leaving the
  // switch there made the h2 announce "Szavazások havonta ülésnaponként" and put the two buttons in the
  // reading order twice. The placeholder sits in the markup inside the h2 and is empty until now, so moving
  // it here costs nothing and keeps the tab order — heading, switch, chart.
  panel.querySelector('h2').insertAdjacentElement('afterend', bar);
  var btns = [];
  function pick(i){
    views.forEach(function(v, j){ v.hidden = j !== i; });
    btns.forEach(function(b, j){ b.setAttribute('aria-pressed', String(j === i)); });
  }
  views.forEach(function(v, i){
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'vbtn';
    b.textContent = v.getAttribute('data-label');
    b.addEventListener('click', function(){ pick(i); });
    bar.appendChild(b);
    btns.push(b);
  });
  panel.classList.add('switched');
  pick(0);
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
  // The slot ships with the sentence a reader without JavaScript needs. We are running, so replace it with
  // the one that is true here — the instruction "start typing" is only honest once typing does something.
  out.innerHTML = '<tr><td colspan="3" class="hero-meta">Kezdj el gépelni.</td></tr>';
  var items = null, loading = false, kind = 'all', cyc = 'all';
  function fold(s){ return String(s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, ''); }
  function esc(s){ return String(s == null ? '' : s).replace(/[&<>"]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  var failed = false;
  function load(cb){ if (items) return cb(); if (loading) return; loading = true; var x = new XMLHttpRequest(); x.open('GET', 'index.json'); x.onload = function(){ try { items = JSON.parse(x.responseText); } catch (e) { items = []; failed = true; } items.forEach(function(it){ it.f = fold(it.t) + ' ' + fold(it.s); it.ft = fold(it.t); }); cb(); }; x.onerror = function(){ items = []; failed = true; cb(); }; x.send(); }
  var urlTimer = null;
  function syncUrl(){ clearTimeout(urlTimer); urlTimer = setTimeout(function(){ if (!history.replaceState) return; var v = q.value.trim(); history.replaceState(null, '', v ? '?q=' + encodeURIComponent(v) : location.pathname); }, 300); }
  var KIND = {iromany: 'iromány', kepviselo: 'képviselő', szemely: 'pályakép', szoszolo: 'szószóló', kormany: 'kormánytag'};
  // Two things a substring search over legal titles gets wrong in Hungarian, both measured against this
  // index before they were fixed rather than guessed at.
  //
  // The abbreviations a journalist types are not what a statute is called. "btk" returned nothing while 49
  // motions concern the büntető törvénykönyv; "ptk" nothing against 28; "gdpr" nothing against 44. Only the
  // shorthands that actually pay are here: the noisy ones (tb, kata, nav, eho) match inside unrelated words
  // as substrings and were left out on the evidence, not on taste.
  //
  // And the vowel that disappears when a Hungarian -alom/-elem word is inflected: "védelem" is not a
  // substring of "védelmi", so the dictionary form a reader types missed 597 of the 773 items about
  // védelem, "gyermekvédelem" 28 of 34, "hatalom" 99 of 110. Dropping the final "em"/"om" gives the stem
  // both forms share. Checked for noise on a sample of the 597: every one was about védelem.
  var SHORT = {btk: 'bunteto torvenykonyv', ptk: 'polgari torvenykonyv', szja: 'szemelyi jovedelemado',
               tao: 'tarsasagi ado', gdpr: 'adatvedelmi', mnb: 'magyar nemzeti bank',
               hhsz: 'hazszabaly', afa: 'altalanos forgalmi ado'};
  function variants(t){
    var v = [t];
    if (SHORT[t]) v.push(SHORT[t]);
    // stem + m, not the bare stem: every inflected form of an -alom/-elem word continues with m
    // (védelmi, védelméről, hatalmi), while the bare stem "hatal" also matches "hatálybalépés" — accent
    // folding makes hatály into hatal — and that phrase is everywhere in legislation. Measured: the bare
    // stem gave "hatalom" 110 hits of which 55 were about hatály; stem+m gives 45 and costs "védelem"
    // nothing (773 either way). Those 45 are NOT all about hatalom — 23 are felhatalmazás, meghatalmazás
    // and hatalmas, which share the root but not the subject, and an earlier version of this comment
    // claimed otherwise on a glance at the word forms without reading what they meant. They are kept
    // rather than excluded because Hungarian writes its compounds closed: anchoring the stem to a word
    // start would also throw away környezetvédelmi and rendvédelmi, which are exactly what a reader
    // searching "védelem" wants. The ranking above is what separates them — the typed word first.
    if (t.length >= 6 && (t.slice(-4) === 'elem' || t.slice(-4) === 'alom')) v.push(t.slice(0, -2) + 'm');
    return v;
  }

  function render(){
    var terms = fold(q.value).split(/\s+/).filter(Boolean).map(function(t){
      return {raw: t, v: variants(t)};
    });
    if (!terms.length) { out.innerHTML = '<tr><td colspan="3" class="hero-meta">Kezdj el gépelni.</td></tr>'; n.textContent = ''; return; }
    if (failed) { out.innerHTML = '<tr><td colspan="3" class="hero-meta">A keresőindex (index.json) nem tölthető be — a listák külön oldalakon: irományok, képviselők, személyek.</td></tr>'; n.textContent = ''; return; }
    var hits = [];
    for (var i = 0; i < items.length; i++) {
      var it = items[i];
      if (kind !== 'all' && it.k !== kind) continue;
      if (cyc !== 'all' && String(it.c) !== cyc && it.k !== 'szemely') continue;
      var ok = true, score = 0;
      for (var j = 0; j < terms.length; j++) {
        // every term must still match (AND across terms); a term matches on any of its variants (OR within)
        var vs = terms[j].v, hit = false;
        for (var k = 0; k < vs.length; k++) if (it.f.indexOf(vs[k]) >= 0) { hit = true; break; }
        if (!hit) { ok = false; break; }
        // Ranking, and the reason it had to change with the expansion. `ft` is the item's SHORT field —
        // a person's name, but for the 12,713 motions only the number ("T/342"), never the title. So every
        // topical query scored zero on every motion, the sort fell back to date, and once "védelem" grew
        // past the 200-row cap the exact matches were pushed off the page by newer stem-only ones: 176 hits
        // all shown became 773 of which the newest 200 held 39 typed matches. More recall, fewer answers.
        // The typed word now scores wherever it appears, so an item that literally says what was asked
        // outranks one reached through an abbreviation or a stem, and the cap cuts the weaker tail.
        var t = terms[j].raw;
        if (it.f.indexOf(t) >= 0) score += 4;
        if (it.ft === t) score += 3; else if (it.ft.indexOf(t) === 0) score += 2; else if (it.ft.indexOf(t) >= 0) score += 1;
      }
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
  // @sql-block — tests/test_site.py finds the loader by this marker. It used to key on the line below,
  // which meant a guard fix silently unhooked ten tests at once; a marker is a promise, a line is not.
  var box = document.getElementById('q'), runBtn = document.getElementById('run');
  if (!box || !runBtn) return;                       // a cycle index has #q too — its filter — and no console
  var out = document.getElementById('out');
  var state = document.getElementById('sqlstate'), took = document.getElementById('took');
  var db = null, conn = null;
  document.querySelectorAll('button.ex').forEach(function(b){
    b.addEventListener('click', function(){ box.value = b.getAttribute('data-q'); box.focus(); });
  });
  function say(t){ if (state) state.textContent = t; }
  // Everything is loaded from this origin: the runtime, the worker and the Parquet files. The page calls
  // no third party, which is the reason the runtime is vendored rather than pulled from a CDN.
  // Two lifecycle defects lived here, and both told the reader something untrue.
  //
  // `db` is not assigned until after `await import(...)`, so a stop clicked during the module download found
  // `if (db)` false, terminated nothing, and let boot() carry on: the page said "megszakítva — a következő
  // futtatás újraindítja az adatbázist" and then rendered the answer to the query it had just disowned. And
  // because the handler re-enabled the run button, a second click passed the `if (conn)` guard and started a
  // whole second boot — another Worker, another 35 MB instantiate — orphaning the first.
  //
  // A generation counter fixes both without racing: stop bumps it, every await checks it, and one in-flight
  // promise is shared so a second click waits for the first boot rather than starting another. Terminating
  // mid-instantiate leaves the library's own promise pending for ever, so the check has to be ours, not its.
  var gen = 0, booting = null;
  function stale(mine){ return mine !== gen; }
  async function boot(){
    if (conn) return conn;
    if (booting) return booting;
    booting = (async function(){ try { return await bootOnce(gen); } finally { booting = null; } })();
    return booting;
  }
  async function bootOnce(mine){
    say('adatbázis betöltése…');
    // Absolute, all of them. A relative path here is resolved against whichever context does the fetching, and
    // the wasm is fetched by the worker rather than the page: '../assets/…' became '/assets/assets/…' and 404ed.
    var base = new URL('../assets/duckdb/', location.href).href;
    var duckdb = await import(base + 'duckdb.mjs');
    if (stale(mine)) throw new Error('__stopped');
    var worker = new Worker(base + 'duckdb-eh.worker.js');
    var d = new duckdb.AsyncDuckDB(new duckdb.ConsoleLogger(duckdb.LogLevel.WARNING), worker);
    if (stale(mine)) { worker.terminate(); throw new Error('__stopped'); }
    db = d;
    await db.instantiate(base + 'duckdb-eh.wasm');
    if (stale(mine)) { try { db.terminate(); } catch (e) {} db = null; throw new Error('__stopped'); }
    var c = await db.connect();
    if (stale(mine)) { try { db.terminate(); } catch (e) {} db = null; throw new Error('__stopped'); }
    // DuckDB fetches its parquet extension at run time, from extensions.duckdb.org, which the vendoring did not
    // cover and the static scan could not see: the CSP is what found it. Unblocked it would have meant every
    // reader of this page making a request to a third party — the exact thing vendoring the runtime prevents.
    // Ours is a mirror of the same layout, so the repository setting is all it takes.
    // `conn` is published only once the whole thing works. Assigning it here, before the extension and the
    // views, meant that a failed INSTALL or a missing Parquet file cached a half-built connection for the
    // life of the page: boot() handed it back on every later call, no views existed, and every query answered
    // "does not exist" until the reader reloaded — which nothing told them to do.
    try {
      await c.query("SET custom_extension_repository='" + base + "ext'");
      await c.query("INSTALL parquet; LOAD parquet;");
      say('táblák regisztrálása…');
      await views(c);
    } catch (e) {
      try { db.terminate(); } catch (e2) {}
      db = null;
      throw e;
    }
    if (stale(mine)) throw new Error('__stopped');
    conn = c;
    say('kész — a lekérdezés a te gépeden fut');
    return conn;
  }
  var TABLES = (document.getElementById('q').dataset.tables || '').split(',').filter(Boolean);
  async function views(conn) {
    for (var i = 0; i < TABLES.length; i++) {
      var name = TABLES[i];
      var url = new URL('../adatok/' + name + '.parquet', location.href).href;
      await db.registerFileURL(name + '.parquet', url, 4 /* HTTP */, false);
      await conn.query("CREATE OR REPLACE VIEW " + name + " AS SELECT * FROM read_parquet('" + name + ".parquet')");
    }
  }
  // A reader may DROP a view — it is their own database, in their own tab. Rebuilding the views costs a tenth
  // of a second, which is too much to pay on every query and nothing at all to pay on the one that failed.
  function droppedOne(msg) {
    var m = String(msg);
    if (!/Catalog Error/.test(m)) return false;
    // DuckDB appends 'Did you mean "szavazatok"?' to a plain typo, and the first version searched the whole
    // message — so `FROM szavazatokk` announced "a táblák visszaállítása…" and rebuilt all eight views to fix
    // a spelling mistake. Only the name the engine says is missing counts.
    var miss = m.match(/with name ([^\s]+) does not exist/);
    if (!miss) return false;
    for (var i = 0; i < TABLES.length; i++) if (TABLES[i] === miss[1]) return true;
    return false;
  }
  // Everything that reaches innerHTML goes through this, including the column names. They did not, and
  // SELECT 1 AS "<img src=x onerror=…>" ran the handler: the query is the reader's own, so the reach is theirs
  // alone on an origin with no cookie and no session — but a known injection is not something to ship.
  function esc(v){
    return String(v).replace(/[&<>"']/g, function(m){
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m];
    });
  }
  // Arrow hands a DECIMAL back as its unscaled integer, so 1.5 arrives as 15 and 0.0001 as 1. Printed raw that
  // is not a formatting blemish, it is a wrong number on the reader's screen with nothing to mark it — the worst
  // kind of defect this page could have, on a page whose whole claim is that the figures are the record's own.
  // DuckDB types a bare `1.5` as DECIMAL(2,1), so a reader meets this without going looking for it.
  // The bundle is minified, so the type's constructor is called `ti` and matching on its name finds nothing —
  // the first version of this did exactly that and silently kept printing 15 for 1.5. Arrow's numeric type id
  // survives minification; 7 is Decimal. Float64 also carries a `precision`, so `scale` alone would not do.
  var ARROW_DECIMAL = 7;
  function scales(tbl){
    var m = {};
    tbl.schema.fields.forEach(function(f){
      var t = f.type;
      if (t && t.typeId === ARROW_DECIMAL && typeof t.scale === 'number' && t.scale > 0) m[f.name] = t.scale;
    });
    return m;
  }
  function descale(v, sc){
    if (v === null || v === undefined || !sc) return v;
    var n = typeof v === 'bigint' ? Number(v) : (typeof v === 'number' ? v : Number(String(v)));
    return isFinite(n) ? n / Math.pow(10, sc) : v;
  }
  function render(tbl){
    var head = out.querySelector('thead'), body = out.querySelector('tbody');
    var cols = tbl.schema.fields.map(function(f){ return f.name; });
    var sc = scales(tbl);
    head.innerHTML = '<tr>' + cols.map(function(c){ return '<th>' + esc(c) + '</th>'; }).join('') + '</tr>';
    // The count printed to the reader used to be the loop counter, which stopped at 501: not the rows the query
    // returned, not the rows on screen, a third number that was neither. `numRows` is what the result holds.
    var rows = [], keep = [], n = 0, total = tbl.numRows;
    for (var row of tbl) {
      if (n++ >= SHOWN) break;
      var rec = {};
      cols.forEach(function(c){ rec[c] = sc[c] ? descale(row[c], sc[c]) : row[c]; });
      keep.push(rec);
      rows.push('<tr>' + cols.map(function(c){
        var v = rec[c];
        if (v === null || v === undefined) v = '';
        return '<td' + (typeof v === 'number' || typeof v === 'bigint' ? ' class="num mono"' : '') + '>'
             + esc(v) + '</td>';
      }).join('') + '</tr>');
    }
    body.innerHTML = rows.join('') || '<tr><td>nincs sor</td></tr>';
    last = { cols: cols, rows: keep, total: total };
    draw();
    return { total: total, shown: keep.length };
  }
  var stopBtn = document.getElementById('stop');
  if (stopBtn) stopBtn.addEventListener('click', function(){
    // SELECT count(*) FROM szavazatok a, szavazatok b is a trillion-row cross join and the worker will sit on it
    // for as long as it takes. Terminating is the only reliable brake; the next run boots a fresh one.
    gen++;                                   // whatever is in flight is now somebody else's; see boot()
    if (db) { try { db.terminate(); } catch (e) {} }
    db = null; conn = null; booting = null;
    runBtn.disabled = false; stopBtn.hidden = true;
    say('megszakítva — a következő futtatás újraindítja az adatbázist');
  });

  // ── the chart ─────────────────────────────────────────────────────────────────────────────────────
  // Drawn by hand in SVG rather than by a library, for the same reason the chamber and the day strip are: a
  // charting library brings its own palette, type and grid, and would look like a guest on the page. Here the
  // colours are the site's own variables and a faction gets the colour it has everywhere else.
  // The colours are the site's own, handed to the page from config/factions.yml. They were a second palette
  // typed out here, and every one of the twelve shared parties disagreed with the rest of the site: Fidesz was
  // #f97316 in a chart and #f36f21 in the chamber beside it. Three parties that exist in the data — FKGP, MIÉP,
  // Nemzetiségi képviselő — were missing entirely and drew grey, which reads as "no faction" and is a claim.
  var FAC = JSON.parse(document.getElementById('q').dataset.factions || '{}');
  var last = null, kind = 'oszlop';
  var SHOWN = 500;                       // rows put in the table; the chart takes fewer still
  function num(v){ return typeof v === 'bigint' ? Number(v) : (typeof v === 'number' ? v : null); }
  function shape(rows, cols){
    if (!rows.length || cols.length < 2) return null;
    // a column's kind is what its values are across the result, not what the first row happens to hold
    function kind(c){
      var seen = 0;
      for (var r = 0; r < rows.length && seen < 20; r++) {
        var v = rows[r][c];
        if (v === null || v === undefined) continue;
        seen++;
        if (num(v) === null) return 'text';
      }
      return seen ? 'num' : 'empty';
    }
    var labelCol = null, valueCol = null;
    for (var i = 0; i < cols.length; i++) {
      var k = kind(cols[i]);
      if (k === 'num' && valueCol === null && i > 0) valueCol = cols[i];
      else if (labelCol === null && k !== 'num') labelCol = cols[i];
    }
    if (labelCol === null) labelCol = cols[0];
    if (valueCol === null) for (var j = cols.length - 1; j >= 0; j--) if (kind(cols[j]) === 'num') { valueCol = cols[j]; break; }
    if (!valueCol) return null;
    // after valueCol is settled, never before: computed first, the fallback's own column could end up in the
    // label as well as on the axis. And by kind, not by row zero, for the same reason as everything else here.
    var also = cols.filter(function(c){ return c !== labelCol && c !== valueCol && kind(c) !== 'num'; })
                   .slice(0, 1);
    // Is the x axis ordered? A line drawn between faction names asserts a path from Fidesz to MSZP, and there is
    // none — the order is whatever ORDER BY happened to produce. A date or a number is ordered; a party is not,
    // and the page says so rather than drawing the claim anyway.
    var keys = rows.map(function(r){ var v = r[labelCol];
      return num(v) !== null ? num(v) : (/^\d{4}(-\d{2})?(-\d{2})?$/.test(String(v)) ? String(v) : null); });
    // ORDER BY datum DESC is an axis with a direction, and the first version called it "names, not order"
    var up = keys[0] !== null, down = keys[0] !== null;
    for (var k = 1; k < keys.length; k++) {
      if (keys[k] === null) { up = down = false; break; }
      if (keys[k] < keys[k - 1]) up = false;
      if (keys[k] > keys[k - 1]) down = false;
    }
    var ordered = up || down;
    // the colour of a row: its own name if that is a faction, otherwise a faction column if the query has one
    var facCol = null;
    for (var f = 0; f < cols.length; f++) if (/^(frakcio|frakció|part|faction)$/i.test(cols[f])) facCol = cols[f];
    return { labelCol: labelCol, valueCol: valueCol, ordered: ordered, facCol: facCol, also: also };
  }
  function svgEl(t, a){ var e = document.createElementNS('http://www.w3.org/2000/svg', t);
    for (var k in a) e.setAttribute(k, a[k]); return e; }
  function label(r, sh){ return sh.also.reduce(function(t, c){
    return r[c] == null ? t : t + ' → ' + String(r[c]); },
    r[sh.labelCol] == null ? '—' : String(r[sh.labelCol])); }
  function fmt(v, st){
    var d = st && st < 1 ? Math.min(12, Math.ceil(-Math.log(st) / Math.LN10) + 1)
                         : (Math.abs(v) >= 1 || v === 0 ? 3 : 8);
    return Number(v).toLocaleString('hu-HU', {maximumFractionDigits: d});
  }
  function clip(v, n){ var t = String(v == null ? '' : v); return t.length > n ? t.slice(0, n - 1) + '…' : t; }
  function colourOf(r, sh){ return FAC[String(r[sh.labelCol])] ||
    (sh.facCol ? FAC[String(r[sh.facCol])] : null) || 'var(--dim3)'; }
  // ticks a reader can hold in their head: 0 / 2000 / 4000, not 0 / 1528 / 3057
  function step(range){ var raw = range / 4, mag = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10));
    var n = raw / mag; return (n > 5 ? 10 : n > 2 ? 5 : n > 1 ? 2 : 1) * mag; }
  function draw(){
    var fig = document.getElementById('fig'), wrap = document.getElementById('chartwrap');
    fig.innerHTML = '';
    if (!last) { wrap.hidden = true; return; }
    wrap.hidden = false;
    if (kind === 'nincs') return;
    var sh = shape(last.rows, last.cols);
    if (!sh) { fig.innerHTML = '<div class="hero-meta prose">Ehhez az eredményhez nem rajzolható ábra: ' +
      'egy megnevezés- és egy számoszlop kell hozzá.</div>'; return; }
    if ((kind === 'vonal') && !sh.ordered) {
      fig.innerHTML = '<div class="hero-meta prose"><b>Vonal itt nem rajzolható.</b> A vízszintes tengelyen ' +
        'megnevezések állnak, nem sorrend — két szomszédos pont közé húzott vonal olyan átmenetet állítana, ' +
        'ami nincs. Dátum vagy szám szerint rendezve a vonal működik; itt az oszlop vagy a pont a helyes ábra.</div>';
      return;
    }
    var rows = last.rows.slice(0, 40);
    var vals = rows.map(function(r){ return num(r[sh.valueCol]) || 0; });
    var max = Math.max.apply(null, vals.concat([0])), min = Math.min.apply(null, vals.concat([0]));
    var st = step((max - min) || 1);
    var lo = Math.floor(min / st) * st, hi = Math.ceil(max / st) * st;
    var W = 900, L = 190, Rr = 70, T = 14, rowH = 22, thin = 1;
    var H = kind === 'oszlop' ? T + rows.length * rowH + 24 : 430;
    var svg = svgEl('svg', {viewBox: '0 0 ' + W + ' ' + H, role: 'img',
      'aria-label': sh.valueCol + ' ' + sh.labelCol + ' szerint, ' + rows.length + ' sor'});
    var span = (hi - lo) || 1;
    if (kind === 'oszlop') {
      if (lo < 0) svg.appendChild(svgEl('line', {x1: (L + (0 - lo) / span * (W - L - Rr)).toFixed(1),
        y1: T, x2: (L + (0 - lo) / span * (W - L - Rr)).toFixed(1), y2: T + rows.length * rowH, class: 'ax'}));
      rows.forEach(function(r, i){
        var y = T + i * rowH, raw = num(r[sh.valueCol]), v = raw === null ? 0 : raw;
        var full = W - L - Rr, zero = L + (0 - lo) / span * full;
        var x1 = L + (Math.min(0, v) - lo) / span * full, w = Math.max(1, Math.abs(v) / span * full);
        // no bar at all where the record holds no number: a zero-length bar would read as a zero
        if (raw !== null) svg.appendChild(svgEl('rect', {x: x1.toFixed(1), y: y + 4, width: w,
          height: rowH - 9, class: 'bar', style: '--c:' + colourOf(r, sh)}));
        var t1 = svgEl('text', {x: L - 8, y: y + rowH / 2 + 2, 'text-anchor': 'end'});
        t1.textContent = clip(label(r, sh), 29); svg.appendChild(t1);
        var t2 = svgEl('text', {x: (x1 + w + 6).toFixed(1), y: y + rowH / 2 + 2, class: 'v'});
        t2.textContent = raw === null ? '—' : fmt(v, st); svg.appendChild(t2);
      });
    } else {
      // room under the axis for a full set of slanted labels: every point is named, not just the ends
      var x0 = 92, y0 = H - 132, plotW = W - x0 - Rr;
      // a 10-unit label needs ~12 units of clearance perpendicular to the axis; at -55° that is a pitch of 15
      var every = thin = Math.ceil(12 / (Math.max(1, plotW / Math.max(1, rows.length - 1)) * 0.82));
      var ticks = Math.max(1, Math.round(span / st));
      for (var g = 0; g <= ticks; g++) {
        var gy = y0 - g / ticks * (y0 - T), gv = lo + g * st;
        svg.appendChild(svgEl('line', {x1: x0, y1: gy.toFixed(1), x2: x0 + plotW, y2: gy.toFixed(1), class: 'grid'}));
        var gt = svgEl('text', {x: x0 - 8, y: (gy + 3).toFixed(1), 'text-anchor': 'end', class: g === ticks ? 'v' : ''});
        gt.textContent = fmt(gv, st); svg.appendChild(gt);
      }
      svg.appendChild(svgEl('line', {x1: x0, y1: y0, x2: x0 + plotW, y2: y0, class: 'ax'}));
      svg.appendChild(svgEl('line', {x1: x0, y1: T, x2: x0, y2: y0, class: 'ax'}));
      var pts = rows.map(function(r, i){
        return [x0 + (rows.length < 2 ? plotW / 2 : i / (rows.length - 1) * plotW),
                y0 - ((num(r[sh.valueCol]) || 0) - lo) / span * (y0 - T)];
      });
      if (kind === 'vonal') svg.appendChild(svgEl('path', {d: 'M' + pts.map(function(q){ return q[0].toFixed(1) + ' ' + q[1].toFixed(1); }).join('L'), class: 'ln'}));
      rows.forEach(function(r, i){
        if (num(r[sh.valueCol]) !== null)
          svg.appendChild(svgEl('circle', {cx: pts[i][0].toFixed(1), cy: pts[i][1].toFixed(1), r: 3.5,
                                           class: 'pt', style: '--c:' + colourOf(r, sh)}));
        if (i % every) return;
        var t = svgEl('text', {x: pts[i][0].toFixed(1), y: (y0 + 12).toFixed(1), 'text-anchor': 'end',
                               transform: 'rotate(-55 ' + pts[i][0].toFixed(1) + ' ' + (y0 + 12).toFixed(1) + ')'});
        t.textContent = clip(label(r, sh), 24);
        svg.appendChild(t);
      });
    }
    fig.appendChild(svg);
    var cap = document.createElement('figcaption');
    cap.className = 'hero-meta';
    cap.textContent = sh.valueCol + ' — ' + sh.labelCol + ' szerint' +
      ((last.total || last.rows.length) > rows.length
        ? ' · az első ' + rows.length + ' sor a ' + (last.total || last.rows.length).toLocaleString('hu-HU') + '-ból' : '') +
      (sh.also.length ? ' és ' + sh.also[0] : '') +
      (thin > 1 ? ' · minden ' + thin + '. címke fér ki' : '') +
      (sh.ordered ? '' : ' · a vízszintes tengely megnevezés, nem sorrend');
    fig.appendChild(cap);
  }
  document.querySelectorAll('button[data-chart]').forEach(function(b){
    b.addEventListener('click', function(){
      kind = b.getAttribute('data-chart');
      document.querySelectorAll('button[data-chart]').forEach(function(x){
        var on = x === b; x.classList.toggle('on', on); x.setAttribute('aria-pressed', on ? 'true' : 'false'); });
      draw();
    });
  });
  function svgText(){
    var el = document.querySelector('#fig svg'); if (!el) return null;
    // The same light treatment every other chart on the site exports with: a reader who saves one picture
    // from the console and one from a cycle page should get two pictures that look like they came from the
    // same place. The class is added for the read and removed immediately — see JS_FIGSAVE.
    var root = document.documentElement, added = !root.classList.contains('kz-export');
    if (added) { root.classList.add('kz-export'); void root.offsetWidth; }
    var c = el.cloneNode(true);
    c.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
    c.setAttribute('style', 'background:#ffffff');
    // Read the computed style from the ORIGINAL node and write it onto the clone. Computing it on the clone
    // returns nothing, because the clone is detached from the document and CSS variables resolve against the
    // tree — the first version did that and produced a file with var(--c) in it, invisible outside this page.
    var src = el.querySelectorAll('*'), dst = c.querySelectorAll('*');
    for (var i = 0; i < src.length; i++) {
      var cs = getComputedStyle(src[i]), n = dst[i];
      if (n.classList.contains('bar') || n.classList.contains('pt')) n.setAttribute('fill', cs.fill);
      if (n.classList.contains('ln')) { n.setAttribute('stroke', cs.stroke); n.setAttribute('fill', 'none'); }
      if (n.classList.contains('ax') || n.classList.contains('grid')) n.setAttribute('stroke', cs.stroke);
      if (n.tagName === 'text') {
        n.setAttribute('fill', cs.fill);
        n.setAttribute('font-family', 'ui-monospace, SFMono-Regular, Menlo, monospace');
        n.setAttribute('font-size', '10');
      }
      n.removeAttribute('class'); n.removeAttribute('style');
    }
    if (added) root.classList.remove('kz-export');
    return '<?xml version="1.0" encoding="UTF-8"?>' + new XMLSerializer().serializeToString(c);
  }
  function save(blob, name){
    var u = URL.createObjectURL(blob), a = document.createElement('a');
    a.href = u; a.download = name; a.click(); setTimeout(function(){ URL.revokeObjectURL(u); }, 2000);
  }
  var dsvg = document.getElementById('dlsvg'), dpng = document.getElementById('dlpng'), dpr = document.getElementById('dlprint');
  if (dsvg) dsvg.addEventListener('click', function(){
    var t = svgText(); if (t) save(new Blob([t], {type: 'image/svg+xml'}), 'karzat-riport.svg'); });
  if (dpng) dpng.addEventListener('click', function(){
    var t = svgText(); if (!t) return;
    var img = new Image();
    img.onload = function(){
      var cv = document.createElement('canvas');
      cv.width = 1800; cv.height = Math.round(1800 * img.height / img.width) || 900;
      var g = cv.getContext('2d');
      g.fillStyle = '#ffffff'; g.fillRect(0, 0, cv.width, cv.height);
      g.drawImage(img, 0, 0, cv.width, cv.height);
      cv.toBlob(function(b){ save(b, 'karzat-riport.png'); }, 'image/png');
    };
    img.src = 'data:image/svg+xml;base64,' + btoa(unescape(encodeURIComponent(t)));
  });
  if (dpr) dpr.addEventListener('click', function(){ window.print(); });
  runBtn.addEventListener('click', async function(){
    runBtn.disabled = true; took.textContent = '';
    if (stopBtn) stopBtn.hidden = false;
    var mine = gen;
    try {
      var c = await boot();
      if (stale(mine)) return;
      var t0 = performance.now();
      var res;
      try {
        res = await c.query(box.value);
      } catch (e1) {
        if (!droppedOne(e1.message || e1)) throw e1;
        say('a táblák visszaállítása…');
        await views(c);
        t0 = performance.now();
        res = await c.query(box.value);
        say('kész — a táblák visszaálltak');
      }
      if (stale(mine)) return;
      var r = render(res);
      took.textContent = r.total.toLocaleString('hu-HU') + ' sor'
        + (r.total > r.shown ? ' · az első ' + r.shown + ' látszik' : '')
        + ' · ' + Math.round(performance.now() - t0) + ' ms';
      say('kész — a lekérdezés a te gépeden fut');
    } catch (e) {
      if (String(e && e.message) === '__stopped' || stale(mine)) return;
      out.querySelector('thead').innerHTML = '';
      out.querySelector('tbody').innerHTML = '<tr><td class="mono">' + esc(e.message || e) + '</td></tr>';
      // the previous query's chart used to stay on screen under the error, captioned with its own columns,
      // reading as though it answered the query that had just failed
      last = null; draw();
      say('hiba — a lekérdezés nem futott le');
    } finally { if (!stale(mine)) { runBtn.disabled = false; if (stopBtn) stopBtn.hidden = true; } }
  });
})();


(function(){
  // On a narrow screen the ten cycles are a strip that scrolls inside the top bar, and on an older cycle's page
  // the one you are reading sits off to the right where you cannot see it. This brings it into view. It is an
  // instant jump, not a scroll animation, so it is right for reduced-motion readers too and needs no guard; and
  // it is an enhancement only — without JavaScript every cycle is still a link, just possibly out of sight.
  // "here" is a <b aria-current> on a cycle page; the landing has no <b> and marks the current cycle .near
  var strip = document.querySelector('.kz-topbar .cyc');
  var here = strip && (strip.querySelector('[aria-current]') || strip.querySelector('a.near'));
  if (!strip || !here || strip.scrollWidth <= strip.clientWidth) return;
  strip.scrollLeft = Math.max(0, here.offsetLeft - (strip.clientWidth - here.offsetWidth) / 2);
})();


(function(){
  // Any chart on this site can leave it as a picture. Two things make that useful rather than merely possible:
  // the picture is rendered on a light ground (a studio cannot use the dark theme), and the source line is
  // burnt into the file itself — an attribution that lives in the page does not travel with the image, and an
  // image without one gets redrawn by the programme's own graphics desk, which is how a citation disappears.
  //
  // The controls are injected here rather than shipped in the markup, so a reader without a script is never
  // offered a button that cannot work, and a save button never pretends to be a toggle.
  if (!document.querySelector || !window.URL || !URL.createObjectURL) return;

  var PROPS = ['fill','fill-opacity','fill-rule','stroke','stroke-width','stroke-opacity','stroke-dasharray',
               'stroke-linecap','stroke-linejoin','opacity','font-family','font-size','font-weight',
               'font-style','letter-spacing','text-anchor','dominant-baseline','paint-order','vector-effect'];

  function figures(){
    return Array.prototype.slice.call(document.querySelectorAll('svg[viewBox]')).filter(function(s){
      if (s.closest('.legend') || s.closest('.kz-topbar') || s.closest('.figsave')) return false;
      var r = s.getBoundingClientRect();
      return r.width >= 240 && r.height >= 80;
    });
  }

  function titleOf(svg){
    var box = svg.closest('section, figure, .panel') || svg.parentNode;
    var h = box.querySelector('h2 span[data-kz-text], figcaption .label, h3');
    // Only a colon ends the label: Hungarian ordinals carry a full stop ("43. ciklus") and splitting on it
    // cut the caption at the number.
    var t = h ? h.textContent : (svg.getAttribute('aria-label') || '').split(':')[0];
    t = (t || 'ábra').replace(/\s+/g, ' ').trim();
    if (t.length > 78) {                              // cut on a word, never mid-word, and say it was cut
      var cut = t.slice(0, 78);
      t = cut.slice(0, Math.max(cut.lastIndexOf(' '), 40)).replace(/[,;:—-]$/, '') + '…';
    }
    return t;
  }

  function slug(t){
    return t.toLowerCase().replace(/[áàâ]/g,'a').replace(/[éè]/g,'e').replace(/[íì]/g,'i')
            .replace(/[óòöő]/g,'o').replace(/[úùüű]/g,'u').replace(/[^a-z0-9]+/g,'-')
            .replace(/^-|-$/g,'').slice(0, 48) || 'abra';
  }

  // The clone is detached, so computed style must be read from the ORIGINAL node: CSS variables resolve
  // against the tree, and reading the clone yields var(--c) — a file that is invisible anywhere but here.
  function inlined(svg){
    var c = svg.cloneNode(true);
    c.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
    c.removeAttribute('class'); c.removeAttribute('style'); c.removeAttribute('tabindex');
    var src = svg.querySelectorAll('*'), dst = c.querySelectorAll('*'), drop = [];
    for (var i = 0; i < src.length; i++) {
      var cs = getComputedStyle(src[i]), n = dst[i];
      if (cs.display === 'none' || cs.visibility === 'hidden') { drop.push(n); continue; }
      // Transient opacity is state, not content. JS_BOOT fades every seat in on a staircase and the seat
      // inspector dims the unpinned ones; both write it inline, and copying the computed value burnt a
      // half-finished animation or one reader's click into a file meant to travel. The stylesheet's own
      // opacity (a dimmed series, a lighter mark) has no inline style and is copied as before.
      var skipOpacity = src[i].style && src[i].style.opacity !== '';
      for (var j = 0; j < PROPS.length; j++) {
        if (PROPS[j] === 'opacity' && skipOpacity) continue;
        var v = cs.getPropertyValue(PROPS[j]);
        // `none` is kept: for fill and stroke it is a value, not an absence. Dropping it let a line chart
        // inherit the default black fill and render as a solid blob, and the dissent ring the same.
        if (v && v !== 'normal' && v !== 'auto') n.setAttribute(PROPS[j], v);
      }
      n.removeAttribute('class'); n.removeAttribute('style');
      n.removeAttribute('tabindex'); n.removeAttribute('role');
    }
    for (var k = 0; k < drop.length; k++) if (drop[k].parentNode) drop[k].parentNode.removeChild(drop[k]);
    return c;
  }

  function stamp(){
    var b = document.querySelector('.kz-topbar .sync b');
    return b ? b.textContent.trim() : '';
  }

  // The caption band is added to the viewBox rather than around it, so the picture stays one SVG and one
  // aspect ratio whatever the host does with it.
  function framed(c, title, box){
    var vb = (c.getAttribute('viewBox') || '').split(/[\s,]+/).map(Number);
    if (vb.length !== 4 || !vb[2] || !vb[3]) return c;
    var x = vb[0], y = vb[1], w = vb[2], h = vb[3];
    // Crop to what is actually drawn. A chart's declared viewBox carries whatever margin suited the page it
    // sits on — for the chamber that is a fifth of the area — and in a picture headed for a studio those
    // pixels are better spent on the room. The measurement comes from the LIVE node, because a detached
    // clone has no layout and getBBox() on it returns zeros.
    if (box && box.width > 0 && box.height > 0) {
      var m = Math.max(box.width, box.height) * 0.02;
      x = box.x - m; y = box.y - m; w = box.width + m * 2; h = box.height + m * 2;
    }
    var pad = Math.max(w * 0.012, h * 0.012), band = Math.max(h * 0.085, w * 0.030), fs = band * 0.34;
    var NS = 'http://www.w3.org/2000/svg';
    var bg = document.createElementNS(NS, 'rect');
    bg.setAttribute('x', x - pad); bg.setAttribute('y', y - pad);
    bg.setAttribute('width', w + pad * 2); bg.setAttribute('height', h + band + pad * 2);
    bg.setAttribute('fill', '#ffffff');
    c.insertBefore(bg, c.firstChild);
    function line(t, dy, size, fill){
      var e = document.createElementNS(NS, 'text');
      e.setAttribute('x', x); e.setAttribute('y', y + h + dy);
      e.setAttribute('font-family', 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace');
      e.setAttribute('font-size', size); e.setAttribute('fill', fill);
      e.textContent = t; c.appendChild(e);
    }
    line(title, band * 0.42, fs * 1.05, '#111111');
    line('karzat · ogykarzat.hu · forrás: az Országgyűlés adatai' + (stamp() ? ' · szinkron ' + stamp() : ''),
         band * 0.82, fs * 0.8, '#52525b');
    c.setAttribute('viewBox', [x - pad, y - pad, w + pad * 2, h + band + pad * 2].join(' '));
    c.removeAttribute('width'); c.removeAttribute('height');
    return c;
  }

  function build(svg, frozenTitle){
    var root = document.documentElement, title = frozenTitle || titleOf(svg);
    // A pinned seat dims every other seat in the room. That is a reading aid for one reader at one moment,
    // not something a published picture should carry, so the pin is lifted for the read and put back.
    var pinned = svg.closest('.pinned');
    if (pinned) pinned.classList.remove('pinned');
    var box = null;
    try { box = svg.getBBox(); } catch (e) { box = null; }
    root.classList.add('kz-export');
    void root.offsetWidth;                       // let the light palette resolve before anything is read
    var c;
    try { c = inlined(svg); } finally {
      root.classList.remove('kz-export');
      if (pinned) pinned.classList.add('pinned');
    }
    c = framed(c, title, box);
    // The aspect must come from the FRAMED clone: framed() padded all four sides and added a caption band,
    // so the original viewBox describes a picture that no longer exists and every PNG came out stretched.
    var fb = (c.getAttribute('viewBox') || '').split(/[\s,]+/).map(Number);
    return {xml: '<?xml version="1.0" encoding="UTF-8"?>\n' + new XMLSerializer().serializeToString(c),
            name: 'karzat-' + slug(title),
            w: fb.length === 4 ? fb[2] : 0, h: fb.length === 4 ? fb[3] : 0};
  }

  function save(blob, name){
    var u = URL.createObjectURL(blob), a = document.createElement('a');
    a.href = u; a.download = name; document.body.appendChild(a); a.click();
    setTimeout(function(){ a.remove(); URL.revokeObjectURL(u); }, 2000);
  }

  function png(svg, btn, frozenTitle){
    var out = build(svg, frozenTitle), img = new Image();
    img.onload = function(){
      var W = 1920, H = Math.round(W * (img.height || 1) / (img.width || 1)) || 1080;
      var cv = document.createElement('canvas'); cv.width = W; cv.height = H;
      var g = cv.getContext('2d');
      g.fillStyle = '#ffffff'; g.fillRect(0, 0, W, H);
      g.drawImage(img, 0, 0, W, H);
      cv.toBlob(function(b){ if (b) save(b, out.name + '.png'); btn.textContent = btn.getAttribute('data-lbl'); }, 'image/png');
    };
    img.onerror = function(){ btn.textContent = 'nem sikerült'; };
    img.width = 1920; img.height = out.w ? Math.round(1920 * out.h / out.w) : 1080;
    img.src = 'data:image/svg+xml;base64,' + btoa(unescape(encodeURIComponent(out.xml)));
  }

  // One row per CONTAINER, not per chart, and the click picks whichever chart is visible inside it. The
  // Számok page renders a finished chart per faction and shows one at a time; a row bound to the chart that
  // happened to be visible at load would save the wrong picture the moment a reader filters.
  function visibleIn(host){
    return Array.prototype.slice.call(host.querySelectorAll('svg[viewBox]')).filter(function(s){
      var r = s.getBoundingClientRect();
      return r.width >= 240 && r.height >= 80;
    })[0];
  }
  // Hosts are collected from EVERY chart svg, not only the ones laid out right now: the Számok page ships a
  // finished chart per faction and hides all but one, so a size gate here left the hidden hosts without a
  // control that a reader would need the moment they filtered. The size check stays where it belongs — at
  // click time, choosing which chart inside the host is the visible one.
  var hosts = [];
  Array.prototype.slice.call(document.querySelectorAll('svg[viewBox]')).forEach(function(svg){
    if (svg.closest('.legend') || svg.closest('.kz-topbar')) return;
    var h = svg.closest('figure, .chart, .chartbox, .tswrap, .covwrap, .turnwrap, .stripwrap, .chamber-today');
    if (h && hosts.indexOf(h) < 0) hosts.push(h);
  });
  hosts.forEach(function(host){
    if (host.querySelector(':scope > .figsave')) return;
    // Captured now, before JS_BOOT starts scrambling every [data-kz-text] label: this block runs earlier in
    // the bundle, so the heading still reads what the builder wrote. A click during the animation would
    // otherwise burn "SZ4V9Z…" into the file.
    var frozen = titleOf(visibleIn(host) || host.querySelector('svg[viewBox]') || host);
    var row = document.createElement('div');
    row.className = 'figsave';
    row.innerHTML = '<button type="button" data-lbl="kép mentése">kép mentése</button>' +
                    '<button type="button" data-lbl="SVG">SVG</button>' +
                    '<span class="fs-note">világos háttérrel, a forrással a képen</span>';
    var bs = row.querySelectorAll('button');
    bs[0].addEventListener('click', function(){
      var svg = visibleIn(host); if (!svg) return;
      this.textContent = 'mentés…'; png(svg, this, frozen);
    });
    bs[1].addEventListener('click', function(){
      var svg = visibleIn(host); if (!svg) return;
      var out = build(svg, frozen);
      save(new Blob([out.xml], {type: 'image/svg+xml;charset=utf-8'}), out.name + '.svg');
    });
    host.appendChild(row);
  });
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
