/* ============================================================================
 * search_engine.js — محرك بحث منفصل بواجهة موحدة للمشغلات (يعمل دون إنترنت)
 *
 * الواجهة (حسب المواصفة):
 *   [مربع البحث] [زر بحث] [استيراد من إكسل] [تصفية متقدمة]
 *   - الكتابة تعرض قائمة الأعمدة المطابقة (اختيار العمود، لا بحث فوري).
 *   - البحث في اسم_العمود المحدد من القائمة المسدلة فقط.
 *   - لا بحث فوري: التنفيذ بزر Enter أو زر البحث فقط → يبني استعلام WHERE.
 *
 * الاستخدام:
 *   SearchEngine.mount(el, {
 *     columns: () => [{name, alias}],   // مزود حي للأعمدة
 *     onSearch: ({column, text}) => {}, // Enter/زر البحث
 *     onAdvanced: () => {},             // زر التصفية المتقدمة (نافذة المضيف)
 *     importEndpoint: '/api/search/import-excel/',
 *     onImportValues: ({column, values, count, truncated}) => {},
 *     persistKey: 'se_col_<app>_<rml>', // حفظ العمود المحدد (اختياري)
 *     placeholder: '...', showAdvanced: true, showImport: true,
 *     searchLabel: 'بحث',
 *   }) → api {getColumn, setColumn, getText, setText, refreshColumns, focus, destroy}
 * ========================================================================== */
(function (global) {
    'use strict';

    var uidSeq = 0;

    function esc(s) {
        return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function colLabel(c) {
        c = c || {};
        var a = String(c.alias || '').trim(), n = String(c.name || '').trim();
        if (a && n && a !== n) return a + ' (' + n + ')';
        return a || n || '';
    }

    function mount(el, opts) {
        opts = opts || {};
        if (!el) return null;
        var uid = 'se' + (++uidSeq) + '_';
        var columnsFn = opts.columns || function () { return []; };
        var onSearch = opts.onSearch || function () {};
        var onAdvanced = opts.onAdvanced || function () {};
        var onImportValues = opts.onImportValues || function () {};
        var importEndpoint = opts.importEndpoint || '/api/search/import-excel/';
        var persistKey = opts.persistKey || '';
        var showAdvanced = opts.showAdvanced !== false;
        var showImport = opts.showImport !== false;

        var state = { column: '', text: '', cols: [], hi: -1, open: false, busy: false };

        try {
            if (persistKey) state.column = localStorage.getItem(persistKey) || '';
        } catch (e) {}

        el.innerHTML =
            '<div class="se-wrap flex items-center gap-1.5 w-full" dir="rtl">' +
            '<div class="se-box relative flex-1 flex items-center gap-1 bg-slate-50 border border-slate-200 rounded-lg px-2 py-1 focus-within:border-indigo-500 focus-within:ring-1 focus-within:ring-indigo-500 transition min-w-0">' +
            '<i class="fa-solid fa-search text-slate-400 text-xs shrink-0"></i>' +
            '<input id="' + uid + 'q" type="text" autocomplete="off" spellcheck="false" class="flex-1 min-w-[80px] bg-transparent border-none text-xs focus:outline-none text-slate-700 py-1" placeholder="' + esc(opts.placeholder || 'ابحث… (اكتب لعرض الأعمدة، ثم Enter)') + '">' +
            '<button id="' + uid + 'colbtn" type="button" title="العمود المحدد — اضغط لعرض القائمة" class="shrink-0 max-w-[150px] truncate text-[11px] font-bold px-2 py-1 rounded-lg bg-white border border-slate-200 text-slate-600 hover:border-indigo-400 hover:text-indigo-700"></button>' +
            '<div id="' + uid + 'pop" class="hidden absolute top-full mt-1 right-0 left-0 z-[120] bg-white border border-slate-200 rounded-xl shadow-xl overflow-hidden text-xs max-h-[240px] overflow-y-auto custom-scroll"></div>' +
            '</div>' +
            '<button id="' + uid + 'go" type="button" title="بحث (Enter)" class="shrink-0 px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg text-xs font-bold whitespace-nowrap"><i class="fa-solid fa-magnifying-glass"></i> ' + esc(opts.searchLabel || 'بحث') + '</button>' +
            (showImport ? '<button id="' + uid + 'xl" type="button" title="استيراد قيم البحث من ملف إكسل (عمود واحد → IN)" class="shrink-0 px-2.5 py-1.5 bg-emerald-50 hover:bg-emerald-100 text-emerald-700 border border-emerald-200 rounded-lg text-xs font-bold whitespace-nowrap"><i class="fa-solid fa-file-excel"></i> إكسل</button>' : '') +
            '<input id="' + uid + 'file" type="file" accept=".xlsx,.xlsm" class="hidden">' +
            (showAdvanced ? '<button id="' + uid + 'adv" type="button" title="التصفية المتقدمة" class="shrink-0 px-2.5 py-1.5 bg-slate-100 hover:bg-slate-200 text-slate-600 rounded-lg text-xs font-bold whitespace-nowrap"><i class="fa-solid fa-sliders"></i></button>' : '') +
            '</div>';

        var q = el.querySelector('#' + uid + 'q');
        var colBtn = el.querySelector('#' + uid + 'colbtn');
        var pop = el.querySelector('#' + uid + 'pop');
        var goBtn = el.querySelector('#' + uid + 'go');
        var xlBtn = el.querySelector('#' + uid + 'xl');
        var fileInp = el.querySelector('#' + uid + 'file');
        var advBtn = el.querySelector('#' + uid + 'adv');

        function saveCol() {
            try {
                if (!persistKey) return;
                if (state.column) localStorage.setItem(persistKey, state.column);
                else localStorage.removeItem(persistKey);
            } catch (e) {}
        }

        function paintColBtn() {
            var c = state.cols.filter(function (x) {
                return String(x.alias || x.name || '') === state.column;
            })[0];
            colBtn.textContent = c ? colLabel(c) : 'كل الأعمدة';
            colBtn.classList.toggle('text-indigo-700', !!c);
            colBtn.classList.toggle('border-indigo-300', !!c);
            colBtn.title = c ? ('البحث في: ' + colLabel(c) + ' — اضغط لتغيير العمود') : 'كل الأعمدة — اضغط لاختيار عمود (يُبسّط WHERE)';
        }

        function refreshColumns() {
            var list = [];
            try { list = columnsFn() || []; } catch (e) { list = []; }
            state.cols = list.filter(function (c) {
                return String((c && (c.alias || c.name)) || '').trim() !== '';
            });
            if (state.column && !state.cols.some(function (c) {
                return String(c.alias || c.name || '') === state.column;
            })) { state.column = ''; saveCol(); }
            paintColBtn();
        }

        function filteredCols() {
            var t = (q.value || '').trim().toLowerCase();
            if (!t) return state.cols.slice(0, 60);
            return state.cols.filter(function (c) {
                return colLabel(c).toLowerCase().indexOf(t) >= 0;
            }).slice(0, 60);
        }

        function paintPop() {
            var items = filteredCols();
            state.hi = -1;
            if (!items.length) {
                pop.innerHTML = '<div class="px-3 py-2.5 text-slate-400 text-center">لا أعمدة مطابقة</div>';
            } else {
                pop.innerHTML = '<div class="px-3 py-1.5 text-[10px] font-bold text-slate-400 bg-slate-50 border-b border-slate-100">اختر العمود — ثم Enter للبحث فيه</div>' +
                    items.map(function (c, i) {
                        var key = esc(String(c.alias || c.name || ''));
                        var sel = (String(c.alias || c.name || '') === state.column) ? ' <i class="fa-solid fa-check text-emerald-500"></i>' : '';
                        return '<div data-sei="' + i + '" class="px-3 py-1.5 cursor-pointer hover:bg-indigo-50 text-slate-700 flex items-center justify-between gap-2" dir="auto"><span class="truncate">' + esc(colLabel(c)) + '</span>' + sel + '</div>';
                    }).join('');
                Array.prototype.forEach.call(pop.querySelectorAll('[data-sei]'), function (d) {
                    d.onmousedown = function (ev) {
                        ev.preventDefault();
                        pickCol(items[parseInt(d.getAttribute('data-sei'), 10)]);
                    };
                });
            }
            pop.dataset.items = JSON.stringify(items.map(function (c) { return c.alias || c.name; }));
        }

        function openPop() {
            refreshColumns();
            paintPop();
            state.open = true;
            pop.classList.remove('hidden');
        }

        function closePop() {
            state.open = false;
            pop.classList.add('hidden');
        }

        function pickCol(c) {
            if (!c) return;
            state.column = String(c.alias || c.name || '');
            saveCol();
            paintColBtn();
            closePop();
            // جاهزية لقيمة البحث: إبقاء النص إن كان قيمة، وإلا تنظيف اسم العمود المكتوب
            var cur = (q.value || '').trim();
            var lbl = colLabel(c).toLowerCase();
            if (cur && (cur.toLowerCase() === lbl || lbl.indexOf(cur.toLowerCase()) === 0)) q.value = '';
            try {
                q.placeholder = 'القيمة في «' + colLabel(c) + '» ثم Enter…';
                q.focus();
            } catch (e) {}
        }

        function submit() {
            var text = (q.value || '').trim();
            closePop();
            if (!text) {
                try { q.focus(); } catch (e) {}
                return;
            }
            onSearch({ column: state.column, text: text });
        }

        function doImport(file) {
            if (!file || state.busy) return;
            state.busy = true;
            var done = function () { state.busy = false; try { fileInp.value = ''; } catch (e) {} };
            try {
                var fd = new FormData();
                fd.append('file', file);
                if (state.column) fd.append('column', state.column);
                fetch(importEndpoint, { method: 'POST', body: fd }).then(function (r) {
                    return r.json().then(function (j) { return { ok: r.ok, j: j }; });
                }).then(function (o) {
                    done();
                    if (!o.ok || !o.j || o.j.error) throw new Error((o.j && o.j.error) || ('HTTP ' + o.status));
                    onImportValues({
                        column: state.column,
                        values: o.j.values || [],
                        count: o.j.count || 0,
                        truncated: !!o.j.truncated,
                        filename: o.j.filename || file.name
                    });
                }).catch(function (e) {
                    done();
                    try {
                        if (typeof notify === 'function') notify('فشل استيراد الإكسل: ' + (e.message || e), 'error');
                        else alert('فشل استيراد الإكسل: ' + (e.message || e));
                    } catch (_e) {}
                });
            } catch (e) { done(); }
        }

        // — events: لا بحث فوري أبداً — الكتابة تُرشّح قائمة الأعمدة فقط —
        q.addEventListener('input', function () {
            if (!state.open) openPop();
            else { refreshColumns(); paintPop(); }
        });
        q.addEventListener('focus', function () { openPop(); });
        q.addEventListener('click', function () { if (!state.open) openPop(); });
        q.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') { e.preventDefault(); submit(); }
            else if (e.key === 'Escape') { closePop(); }
            else if (e.key === 'ArrowDown' && !state.open) { e.preventDefault(); openPop(); }
        });
        colBtn.addEventListener('click', function () {
            if (state.open) closePop();
            else openPop();
        });
        goBtn.addEventListener('click', submit);
        if (advBtn) advBtn.addEventListener('click', function () { closePop(); onAdvanced(); });
        if (xlBtn && fileInp) {
            xlBtn.addEventListener('click', function () { fileInp.click(); });
            fileInp.addEventListener('change', function () {
                if (fileInp.files && fileInp.files[0]) doImport(fileInp.files[0]);
            });
        }
        function onDocDown(e) {
            if (!el.contains(e.target)) closePop();
        }
        document.addEventListener('mousedown', onDocDown);

        refreshColumns();

        return {
            getColumn: function () { return state.column; },
            setColumn: function (v) {
                state.column = String(v || '');
                saveCol();
                refreshColumns();
            },
            getText: function () { return q.value || ''; },
            setText: function (v) { q.value = String(v == null ? '' : v); },
            refreshColumns: refreshColumns,
            focus: function () { try { q.focus(); } catch (e) {} },
            destroy: function () {
                try { document.removeEventListener('mousedown', onDocDown); } catch (e) {}
                try { el.innerHTML = ''; } catch (e) {}
            }
        };
    }

    global.SearchEngine = { mount: mount };
})(typeof window !== 'undefined' ? window : this);
