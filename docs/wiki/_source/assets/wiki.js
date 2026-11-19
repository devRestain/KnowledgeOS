/* KnowledgeOS wiki behaviour — hand-written static asset, copied as-is by the builder.
   No modules, no fetch, no remote resources; works from file://.
   Reads window.KOS_WIKI_INDEX (assets/search-index.js) and degrades quietly without it. */
(function () {
  'use strict';

  var doc = document;
  var MAX_RESULTS = 10;

  function norm(s) {
    s = String(s == null ? '' : s);
    if (s.normalize) s = s.normalize('NFC');
    return s.toLowerCase();
  }
  function escapeRe(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }
  function el(tag, cls, text) {
    var n = doc.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function isTyping(t) {
    if (!t || !t.tagName) return false;
    var n = t.tagName;
    return n === 'INPUT' || n === 'TEXTAREA' || n === 'SELECT' || t.isContentEditable;
  }

  /* ---------- search ---------- */
  function initSearch() {
    var input = doc.getElementById('wiki-search');
    var list = doc.getElementById('search-results');
    if (!input || !list) return;

    var raw = Array.isArray(window.KOS_WIKI_INDEX) ? window.KOS_WIKI_INDEX : [];
    var docs = null;                       // lazily prepared
    var items = [];                        // current result descriptors
    var active = -1;

    var status = el('div', 'sr-only');
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    list.parentNode.appendChild(status);

    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-expanded', 'false');
    input.setAttribute('aria-controls', 'search-results');
    list.setAttribute('role', 'listbox');
    list.setAttribute('aria-label', '검색 결과');

    function prepare() {
      if (docs) return docs;
      docs = raw.map(function (d) {
        return {
          slug: String(d.slug || ''),
          title: String(d.title || d.slug || ''),
          group: String(d.group || ''),
          t: norm(d.title),
          s: norm(d.slug),
          h: (d.headings || []).map(function (h) { return { id: h.id, text: String(h.text || ''), n: norm(h.text) }; }),
          x: String(d.text || '').normalize ? String(d.text || '').normalize('NFC') : String(d.text || '')
        };
      });
      docs.forEach(function (d) { d.xl = d.x.toLowerCase(); });
      return docs;
    }

    function search(query) {
      var terms = norm(query).split(/\s+/).filter(Boolean);
      if (!terms.length) return [];
      var out = [];
      prepare().forEach(function (d, order) {
        var score = 0, titleHits = 0, i;
        var best = null, bestHits = 0;
        for (i = 0; i < terms.length; i++) {
          var term = terms[i], hit = false, ti = d.t.indexOf(term);
          if (ti >= 0) { score += ti === 0 ? 120 : 100; titleHits++; hit = true; }
          if (d.s === term) { score += 60; hit = true; }
          if (d.xl.indexOf(term) >= 0) { score += 10; hit = true; }
          for (var k = 0; k < d.h.length; k++) {
            if (d.h[k].n.indexOf(term) >= 0) { score += k === 0 ? 20 : 40; hit = true; break; }
          }
          if (!hit) return;                // every term must match somewhere
        }
        d.h.forEach(function (h) {
          var c = 0;
          terms.forEach(function (term) { if (h.n.indexOf(term) >= 0) c++; });
          if (c > bestHits) { best = h; bestHits = c; }
        });
        var useHeading = best && bestHits > titleHits && best.id;
        out.push({
          doc: d,
          order: order,
          score: score + (useHeading ? 0 : 5),
          href: d.slug + '.html' + (useHeading ? '#' + best.id : ''),
          where: useHeading ? best.text : '',
          terms: terms
        });
      });
      out.sort(function (a, b) { return b.score - a.score || a.order - b.order; });
      return out.slice(0, MAX_RESULTS);
    }

    function snippet(d, terms) {
      var text = d.x.replace(/\s+/g, ' ');
      var low = text.toLowerCase();
      var at = -1, i;
      for (i = 0; i < terms.length && at < 0; i++) at = low.indexOf(terms[i]);
      var start = at < 0 ? 0 : Math.max(0, at - 28);
      var s = text.slice(start, start + 120);
      return (start > 0 ? '…' : '') + s + (start + 120 < text.length ? '…' : '');
    }

    function appendMarked(node, text, terms) {
      var re = new RegExp('(' + terms.map(escapeRe).join('|') + ')', 'gi');
      var pos = 0, m;
      while ((m = re.exec(text)) !== null) {
        if (!m[0]) { re.lastIndex++; continue; }
        if (m.index > pos) node.appendChild(doc.createTextNode(text.slice(pos, m.index)));
        node.appendChild(el('mark', null, m[0]));
        pos = m.index + m[0].length;
      }
      if (pos < text.length) node.appendChild(doc.createTextNode(text.slice(pos)));
    }

    function setActive(i) {
      var nodes = list.querySelectorAll('.sr-item');
      if (active >= 0 && nodes[active]) {
        nodes[active].classList.remove('is-active');
        nodes[active].setAttribute('aria-selected', 'false');
      }
      active = i;
      if (i >= 0 && nodes[i]) {
        nodes[i].classList.add('is-active');
        nodes[i].setAttribute('aria-selected', 'true');
        input.setAttribute('aria-activedescendant', nodes[i].id);
        if (nodes[i].scrollIntoView) nodes[i].scrollIntoView({ block: 'nearest' });
      } else {
        input.removeAttribute('aria-activedescendant');
      }
    }

    function open() { list.hidden = false; input.setAttribute('aria-expanded', 'true'); }
    function close() {
      list.hidden = true;
      input.setAttribute('aria-expanded', 'false');
      input.removeAttribute('aria-activedescendant');
      active = -1;
    }

    function render() {
      var q = input.value.trim();
      if (!q) { items = []; list.textContent = ''; close(); status.textContent = ''; return; }
      list.textContent = '';
      if (!raw.length) {
        items = [];
        list.appendChild(el('li', 'sr-empty', '검색 색인을 불러오지 못했습니다.'));
        open();
        status.textContent = '검색 색인을 불러오지 못했습니다.';
        return;
      }
      items = search(q);
      if (!items.length) {
        list.appendChild(el('li', 'sr-empty', '“' + q + '”에 해당하는 문서가 없습니다. 다른 단어로 찾아보세요.'));
        status.textContent = '검색 결과 없음';
        active = -1;
        open();
        return;
      }
      items.forEach(function (r, i) {
        var li = el('li', 'sr-item');
        li.id = 'sr-' + i;
        li.setAttribute('role', 'option');
        li.setAttribute('aria-selected', 'false');
        var a = el('a');
        a.href = r.href;
        a.tabIndex = -1;
        var head = el('div', 'sr-head');
        var title = el('span', 'sr-title');
        appendMarked(title, r.doc.title, r.terms);
        head.appendChild(title);
        if (r.where) head.appendChild(el('span', 'sr-where', '› ' + r.where));
        if (r.doc.group) head.appendChild(el('span', 'sr-group', r.doc.group));
        a.appendChild(head);
        var sn = el('div', 'sr-snippet');
        appendMarked(sn, snippet(r.doc, r.terms), r.terms);
        a.appendChild(sn);
        li.appendChild(a);
        li.addEventListener('mousemove', function () { if (active !== i) setActive(i); });
        list.appendChild(li);
      });
      status.textContent = items.length + '개 결과';
      open();
      setActive(0);
    }

    input.addEventListener('input', render);
    input.addEventListener('focus', function () { if (input.value.trim() && list.hidden) render(); });
    input.addEventListener('keydown', function (e) {
      if (e.isComposing || e.keyCode === 229) return;   // Korean IME composition
      var n = items.length;
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        if (list.hidden) render(); else if (n) setActive((active + 1) % n);
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        if (n) setActive((active - 1 + n) % n);
      } else if (e.key === 'Enter') {
        if (!list.hidden && n) {
          e.preventDefault();
          window.location.href = items[Math.max(active, 0)].href;
        }
      } else if (e.key === 'Escape') {
        if (!list.hidden) { e.preventDefault(); close(); }
        else if (input.value) { input.value = ''; }
        else { input.blur(); }
      }
    });

    doc.addEventListener('click', function (e) {
      if (!list.hidden && !(input.parentNode.contains(e.target))) close();
    });
    doc.addEventListener('keydown', function (e) {
      if (e.key === '/' && !e.ctrlKey && !e.metaKey && !e.altKey && !isTyping(e.target)) {
        e.preventDefault();
        input.focus();
        input.select();
      }
    });
  }

  /* ---------- off-canvas navigation (<900px) ---------- */
  function initNav() {
    var btn = doc.querySelector('.nav-toggle');
    var side = doc.querySelector('.sidebar');
    if (!btn || !side) return;
    var body = doc.body;
    var scrim = el('div', 'nav-scrim');
    scrim.setAttribute('aria-hidden', 'true');
    body.appendChild(scrim);
    var narrow = window.matchMedia ? window.matchMedia('(max-width: 899.98px)') : null;

    function set(open, restoreFocus) {
      body.classList.toggle('nav-open', open);
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
      if (open) {
        var cur = side.querySelector('[aria-current="page"]') || side.querySelector('a');
        if (cur) { cur.focus({ preventScroll: true }); if (cur.scrollIntoView) cur.scrollIntoView({ block: 'center' }); }
      } else if (restoreFocus) {
        btn.focus();
      }
    }
    btn.addEventListener('click', function () { set(!body.classList.contains('nav-open'), false); });
    scrim.addEventListener('click', function () { set(false, true); });
    side.addEventListener('click', function (e) {
      var a = e.target.closest ? e.target.closest('a') : null;
      if (a) set(false, false);
    });
    doc.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && body.classList.contains('nav-open')) { e.preventDefault(); set(false, true); }
    });
    if (narrow && narrow.addEventListener) {
      narrow.addEventListener('change', function (e) { if (!e.matches) set(false, false); });
    }
  }

  /* ---------- keyboard access for scrollable regions ---------- */
  function initScrollRegions() {
    var nodes = doc.querySelectorAll('.table-wrap, figure.diagram, .page pre');
    Array.prototype.forEach.call(nodes, function (n) {
      if (n.scrollWidth > n.clientWidth + 1) {
        n.tabIndex = 0;
        n.setAttribute('role', 'region');
        n.setAttribute('aria-label', n.matches('pre') ? '코드 (가로 스크롤)' : n.matches('figure') ? '다이어그램 (가로 스크롤)' : '표 (가로 스크롤)');
      }
    });
  }

  /* ---------- deep links into collapsed <details> ---------- */
  function revealHash() {
    var id = window.location.hash.slice(1);
    if (!id) return;
    var t;
    try { t = doc.getElementById(decodeURIComponent(id)); } catch (e) { t = doc.getElementById(id); }
    for (var d = t && t.closest ? t.closest('details') : null; d; d = d.parentElement && d.parentElement.closest('details')) d.open = true;
  }

  /* ---------- "이 페이지" rail with scroll-spy ---------- */
  function initToc() {
    var layout = doc.querySelector('.layout');
    var page = doc.querySelector('.page');
    if (!layout || !page) return;
    var heads = Array.prototype.filter.call(page.querySelectorAll('h2[id], h3[id]'), function (h) {
      return !h.closest('.related');
    });
    var h2s = heads.filter(function (h) { return h.tagName === 'H2'; });
    if (heads.length > 40) heads = h2s;
    if (heads.length < 3) return;

    function label(h) {
      var c = h.cloneNode(true);
      var a = c.querySelector('.anchor');
      if (a) a.remove();
      return c.textContent.trim();
    }

    var aside = el('nav', 'toc');
    aside.setAttribute('aria-label', '이 페이지');
    aside.appendChild(el('h2', null, '이 페이지'));
    var ol = el('ol');
    var links = {};
    heads.forEach(function (h) {
      var li = el('li', h.tagName === 'H3' ? 'toc-sub' : '');
      var a = el('a', null, label(h));
      a.href = '#' + h.id;
      li.appendChild(a);
      ol.appendChild(li);
      links[h.id] = a;
    });
    aside.appendChild(ol);
    layout.appendChild(aside);
    layout.classList.add('has-toc');

    if (!('IntersectionObserver' in window)) return;
    var current = null;
    function mark(id) {
      if (current === id) return;
      if (current && links[current]) links[current].removeAttribute('aria-current');
      current = id;
      if (id && links[id]) links[id].setAttribute('aria-current', 'location');
    }
    var visible = {};
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) { visible[en.target.id] = en.isIntersecting; });
      for (var i = 0; i < heads.length; i++) {
        if (visible[heads[i].id]) { mark(heads[i].id); return; }
      }
    }, { rootMargin: '-72px 0px -70% 0px' });
    heads.forEach(function (h) { io.observe(h); });
  }

  function init() {
    initSearch();
    initNav();
    initToc();
    initScrollRegions();
    revealHash();
    window.addEventListener('hashchange', revealHash);
  }

  if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', init);
  else init();
})();
