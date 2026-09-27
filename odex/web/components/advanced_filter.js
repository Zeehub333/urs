/* ============================================================================
 * advanced_filter.js — نافذة التصفية المتقدمة الموحدة (تعمل دون إنترنت)
 *
 * البنية (حسب التصميم المعتمد):
 *   [إضافة عامل تصفية مخصص ✕]
 *   [مطابقة القواعد التالية: (أي من|كل)] .... [(إظهار المؤرشفة toggle)]
 *   [القيمة] [العملية] [العمود] 1 [+] [🗑]
 *   ...
 *   [قاعدة جديدة]
 *   [إلغاء] [إضافة]
 *
 * المنطق:
 *   - قائمة الأعمدة ديناميكية من columns() عند الفتح (لا أعمدة ثابتة).
 *   - قائمة العمليات تتبع نوع العمود (opsFor(kind) من المضيف أو الافتراضي).
 *   - between ← حقلا (من/إلى) بدل القيمة المفردة.
 *   - أي من → any (OR)، كل → all (AND).
 *   - الصفوف الناقصة (بلا عمود/قيمة) تُتجاهل؛ بلا قاعدة مكتملة → تنبيه وبقاء.
 *   - + تُدرج قاعدة بعد الصف، 🗑 تحذفه، «قاعدة جديدة» تُلحق بالنهاية.
 *
 * الاستخدام:
 *   AdvancedFilter.open({
 *     columns: () => [{value, label, kind}],
 *     opsFor: (kind) => [[op, label]],   // اختياري — الافتراضي بالأسفل
 *     initial: {mode:'any', rules:[], showArchived:false},
 *     title: 'إضافة عامل تصفية مخصص',
 *     onApply: ({mode, rules, showArchived}) => {},  // rules:[{column,op,value,valFrom,valTo}]
 *     onClose: () => {},
 *   })
 * ========================================================================== */
(function (global) {
    'use strict';

    // العمليات الافتراضية لكل نوع — نفس قوائم المشغلات (عربي)
    var DEFAULT_OPS = {
        text: [['contains', 'يحتوي على'], ['equals', 'يساوي'], ['startswith', 'يبدأ بـ'], ['endswith', 'ينتهي بـ'], ['not_contains', 'لا يحتوي على'], ['not_equals', 'لا يساوي']],
        number: [['equals', 'يساوي'], ['not_equals', 'لا يساوي'], ['gt', 'أكبر من'], ['gte', 'أكبر من أو يساوي'], ['lt', 'أصغر من'], ['lte', 'أصغر من أو يساوي'], ['between', 'بين (رقمين أو تاريخين)']],
        date: [['lt', 'قبل'], ['gt', 'بعد'], ['between', 'بين تاريخين'], ['equals', 'في يوم'], ['inmonth', 'في شهر'], ['inyear', 'في سنة'], ['since', 'منذ']],
        datetime: [['lt', 'قبل'], ['gt', 'بعد'], ['between', 'بين تاريخين'], ['equals', 'في يوم'], ['inmonth', 'في شهر'], ['inyear', 'في سنة'], ['since', 'منذ']]
    };

    function opsForKind(kind, custom) {
        try {
            if (typeof custom === 'function') {
                var r = custom(kind);
                if (r && r.length) return r;
            }
        } catch (e) {}
        var k = String(kind || 'text').toLowerCase();
        if (k === 'list' || k === 'status') k = 'text';
        if (k === 'time') k = 'number';
        return DEFAULT_OPS[k] || DEFAULT_OPS.text;
    }

    function esc(s) {
        return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    // تنقية الصفوف: إسقاط الناقص، وتوحيد الشكل — خالصة للاختبار
    function cleanRules(rows) {
        var out = [];
        (rows || []).forEach(function (r) {
            if (!r) return;
            var col = String(r.column || '').trim();
            if (!col) return;
            var op = String(r.op || 'equals').trim() || 'equals';
            if (op === 'between') {
                var a = String(r.valFrom ?? '').trim(), b = String(r.valTo ?? '').trim();
                if (!a || !b) return;
                out.push({ column: col, op: op, valFrom: a, valTo: b });
            } else {
                var v = String(r.value ?? '').trim();
                if (!v) return;
                out.push({ column: col, op: op, value: v });
            }
        });
        return out;
    }

    var overlay = null;
    var st = null; // {opts, mode, rows:[{column,op,value,valFrom,valTo}], showArchived}

    function el(id) { return document.getElementById(id); }

    function colKind(value) {
        var hit = (st.cols || []).filter(function (c) { return String(c.value) === String(value); })[0];
        return (hit && hit.kind) || 'text';
    }

    function opsHtml(kind, cur) {
        return opsForKind(kind, st.opts.opsFor).map(function (p) {
            return '<option value="' + esc(p[0]) + '"' + (p[0] === cur ? ' selected' : '') + '>' + esc(p[1]) + '</option>';
        }).join('');
    }

    function colsHtml(cur) {
        var opts = (st.cols || []).map(function (c) {
            return '<option value="' + esc(c.value) + '"' + (String(c.value) === String(cur) ? ' selected' : '') + '>' + esc(c.label || c.value) + '</option>';
        }).join('');
        return '<option value="">— العمود —</option>' + opts;
    }

    function rowHtml(r, i) {
        var kind = r.column ? colKind(r.column) : 'text';
        var isBetween = (r.op === 'between');
        var valCell = isBetween
            ? '<div class="flex-1 grid grid-cols-2 gap-1.5 min-w-0">'
            + '<input data-af="from" data-i="' + i + '" value="' + esc(r.valFrom || '') + '" placeholder="من" class="w-full min-w-0 bg-white border border-slate-300 rounded-lg px-3 py-2 text-sm text-slate-700 focus:outline-none focus:border-indigo-500">'
            + '<input data-af="to" data-i="' + i + '" value="' + esc(r.valTo || '') + '" placeholder="إلى" class="w-full min-w-0 bg-white border border-slate-300 rounded-lg px-3 py-2 text-sm text-slate-700 focus:outline-none focus:border-indigo-500">'
            + '</div>'
            : '<input data-af="val" data-i="' + i + '" value="' + esc(r.value || '') + '" placeholder="القيمة" class="flex-1 min-w-0 bg-white border border-slate-300 rounded-lg px-3 py-2 text-sm text-slate-700 focus:outline-none focus:border-indigo-500">';
        return '<div class="flex items-center gap-2 py-2 border-b border-dashed border-slate-100" data-row="' + i + '">'
            + valCell
            + '<select data-af="op" data-i="' + i + '" class="w-36 shrink-0 bg-white border border-slate-300 rounded-lg px-2 py-2 text-sm text-slate-700 focus:outline-none focus:border-indigo-500 cursor-pointer">' + opsHtml(kind, r.op || 'contains') + '</select>'
            + '<select data-af="col" data-i="' + i + '" class="w-44 shrink-0 bg-white border border-slate-300 rounded-lg px-2 py-2 text-sm text-slate-700 focus:outline-none focus:border-indigo-500 cursor-pointer">' + colsHtml(r.column) + '</select>'
            + '<span class="w-6 text-center text-sm text-slate-500 shrink-0">' + (i + 1) + '</span>'
            + '<button data-act="add" data-i="' + i + '" title="قاعدة بعد هذا الصف" class="shrink-0 w-7 h-7 rounded-lg text-slate-400 hover:text-indigo-600 hover:bg-indigo-50 text-base leading-none">+</button>'
            + '<button data-act="del" data-i="' + i + '" title="حذف القاعدة" class="shrink-0 w-7 h-7 rounded-lg text-slate-400 hover:text-rose-600 hover:bg-rose-50"><i class="fa-solid fa-trash text-sm"></i></button>'
            + '</div>';
    }

    function paint() {
        if (!overlay) return;
        var box = el('afRules');
        if (box) {
            box.innerHTML = st.rows.length
                ? st.rows.map(function (r, i) { return rowHtml(r, i); }).join('')
                : '<div class="py-6 text-center text-sm text-slate-400">لا قواعد — اضغط «قاعدة جديدة»</div>';
        }
        var ms = el('afMode');
        if (ms) ms.value = st.mode;
        var tg = el('afArch');
        if (tg) {
            tg.checked = !!st.showArchived;
            var lbl = el('afArchLbl');
            if (lbl) lbl.classList.toggle('text-slate-700', !!st.showArchived);
        }
    }

    function readRow(i) {
        var q = function (k) {
            var n = overlay.querySelector('[data-af="' + k + '"][data-i="' + i + '"]');
            return n ? n.value : '';
        };
        st.rows[i] = { column: q('col'), op: q('op') || 'contains', value: q('val'), valFrom: q('from'), valTo: q('to') };
    }

    function readAll() {
        for (var i = 0; i < st.rows.length; i++) readRow(i);
    }

    function blankRow(after) {
        return { column: (after && after.column) || '', op: 'contains', value: '', valFrom: '', valTo: '' };
    }

    function open(opts) {
        opts = opts || {};
        close(true);
        var init = opts.initial || {};
        var cols = [];
        try { cols = (typeof opts.columns === 'function' ? opts.columns() : (opts.columns || [])) || []; } catch (e) { cols = []; }
        st = {
            opts: opts,
            cols: cols.filter(function (c) { return c && String(c.value || '').trim() !== ''; }),
            mode: (init.mode === 'all' ? 'all' : 'any'),
            rows: Array.isArray(init.rules) && init.rules.length
                ? init.rules.map(function (r) {
                    return { column: r.column || '', op: r.op || 'contains', value: r.value || '', valFrom: r.valFrom || '', valTo: r.valTo || '' };
                })
                : [blankRow()],
            showArchived: !!init.showArchived
        };

        overlay = document.createElement('div');
        overlay.id = 'afOverlay';
        overlay.className = 'fixed inset-0 z-[200] bg-slate-900/50 backdrop-blur-sm overflow-y-auto p-4';
        overlay.innerHTML =
            '<div class="bg-white rounded-xl shadow-2xl w-full max-w-3xl mx-auto overflow-hidden flex flex-col max-h-[92vh]" dir="rtl">'
            + '<div class="px-6 py-4 border-b border-slate-200 flex items-center justify-between gap-2">'
            + '<h3 class="text-lg font-bold text-slate-800">' + esc(opts.title || 'إضافة عامل تصفية مخصص') + '</h3>'
            + '<button data-act="close" title="إغلاق" class="text-slate-400 hover:text-slate-600 text-xl leading-none px-1">✕</button>'
            + '</div>'
            + '<div class="px-6 py-4 flex items-center justify-between gap-3 flex-wrap">'
            + '<label class="inline-flex items-center gap-2 text-sm text-slate-500 cursor-pointer select-none">'
            + '<input id="afArch" type="checkbox" class="sr-only">'
            + '<span id="afArchTrack" class="w-10 h-6 rounded-full bg-slate-200 relative transition inline-block">'
            + '<span id="afArchDot" class="absolute top-0.5 right-0.5 w-5 h-5 rounded-full bg-white shadow transition-all"></span>'
            + '</span><span id="afArchLbl">إظهار المؤرشفة</span></label>'
            + '<div class="flex items-center gap-2 text-sm text-slate-600">'
            + '<span>مطابقة القواعد التالية:</span>'
            + '<select id="afMode" class="bg-white border border-slate-300 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:border-indigo-500 cursor-pointer">'
            + '<option value="any">أي من</option><option value="all">كل</option>'
            + '</select></div>'
            + '</div>'
            + '<div class="px-6 overflow-y-auto custom-scroll min-h-0"><div id="afRules"></div>'
            + '<button data-act="new" class="mt-1 mb-3 text-sm font-bold text-emerald-600 hover:text-emerald-700">قاعدة جديدة</button>'
            + '</div>'
            + '<div class="px-6 py-4 border-t border-slate-200 flex items-center gap-2 bg-slate-50">'
            + '<button data-act="apply" class="px-8 py-2 bg-emerald-600 hover:bg-emerald-700 text-white rounded-lg text-sm font-bold">إضافة</button>'
            + '<button data-act="close" class="px-8 py-2 bg-white border border-slate-300 hover:bg-slate-100 text-slate-600 rounded-lg text-sm">إلغاء</button>'
            + '</div></div>';

        overlay.addEventListener('mousedown', function (e) { if (e.target === overlay) close(); });
        overlay.addEventListener('click', function (e) {
            var b = e.target.closest ? e.target.closest('[data-act]') : null;
            if (!b) return;
            var act = b.getAttribute('data-act');
            var i = parseInt(b.getAttribute('data-i') || '-1', 10);
            if (act === 'close') close();
            else if (act === 'new') { readAll(); st.rows.push(blankRow()); paint(); focusLast(); }
            else if (act === 'add' && i >= 0) { readAll(); st.rows.splice(i + 1, 0, blankRow(st.rows[i])); paint(); }
            else if (act === 'del' && i >= 0) { readAll(); st.rows.splice(i, 1); if (!st.rows.length) st.rows.push(blankRow()); paint(); }
            else if (act === 'apply') apply();
        });
        overlay.addEventListener('change', function (e) {
            var t = e.target;
            if (!t || !t.getAttribute) return;
            if (t.id === 'afMode') { st.mode = (t.value === 'all' ? 'all' : 'any'); return; }
            if (t.id === 'afArch') { st.showArchived = !!t.checked; paintToggle(); return; }
            var k = t.getAttribute('data-af'), i = parseInt(t.getAttribute('data-i') || '-1', 10);
            if ((k === 'col' || k === 'op') && i >= 0) {
                readRow(i);
                if (k === 'col') {
                    // نوع جديد ← عملياته الافتراضية مع إبقاء العملية إن صلحت
                    var ops = opsForKind(colKind(st.rows[i].column), st.opts.opsFor).map(function (p) { return p[0]; });
                    if (ops.indexOf(st.rows[i].op) < 0) st.rows[i].op = ops[0] || 'contains';
                    if (st.rows[i].op !== 'between') { st.rows[i].valFrom = ''; st.rows[i].valTo = ''; }
                }
                paint();
            }
        });
        overlay.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') close();
            if (e.key === 'Enter' && e.target && e.target.tagName === 'INPUT') { e.preventDefault(); apply(); }
        });

        document.body.appendChild(overlay);
        try {
            if (typeof lockScroll === 'function') lockScroll();
        } catch (e) {}
        paint();
        paintToggle();
        setTimeout(function () { try { focusLast(); } catch (e) {} }, 60);
    }

    function paintToggle() {
        var track = el('afArchTrack'), dot = el('afArchDot');
        if (!track || !dot) return;
        var on = !!(st && st.showArchived);
        track.classList.toggle('bg-emerald-500', on);
        track.classList.toggle('bg-slate-200', !on);
        dot.style.transform = on ? 'translateX(-16px)' : 'translateX(0)';
    }

    function focusLast() {
        if (!overlay) return;
        var ins = overlay.querySelectorAll('#afRules input[data-af="val"], #afRules input[data-af="from"]');
        if (ins.length) ins[ins.length - 1].focus();
    }

    function apply() {
        if (!st) return;
        readAll();
        var rules = cleanRules(st.rows);
        if (!rules.length) {
            try {
                if (typeof notify === 'function') notify('أكمل قاعدة واحدة على الأقل (عمود + قيمة)', 'info');
            } catch (e) {}
            return;
        }
        var out = { mode: st.mode, rules: rules, showArchived: !!st.showArchived };
        var cb = st.opts.onApply;
        close(true);
        try { cb(out); } catch (e) {}
    }

    function close(silent) {
        if (overlay) {
            try { overlay.remove(); } catch (e) {
                try { overlay.parentNode.removeChild(overlay); } catch (_e) {}
            }
            overlay = null;
        }
        var cb = st && st.opts ? st.opts.onClose : null;
        st = null;
        try {
            if (typeof unlockScroll === 'function') unlockScroll();
        } catch (e) {}
        if (!silent && typeof cb === 'function') { try { cb(); } catch (e) {} }
    }

    global.AdvancedFilter = {
        open: open,
        close: close,
        _opsFor: function (k, c) { return opsForKind(k, c); },
        _clean: cleanRules
    };
})(typeof window !== 'undefined' ? window : this);
