/* ─────────────────────────────────────────────────────────────
   UniDBPicker.js — Unified DB picker for the whole designer.
   Pattern everywhere: صندوق اختيار (select menu) + زر بحث (dialog).
   - choose table / column from DB (cached fetches)
   - search in modal (dbSearchOpen)
   - select from menu in modal (results set the select)
   The wizard (rml_wizard_script.html) calls this module; it must load FIRST.
   Depends only on: document/fetch (native). wbEsc/notify are optional
   (local fallbacks included) so the module never crashes standalone.
   ───────────────────────────────────────────────────────────── */
function _upEsc(s){
    try{ if(typeof wbEsc === 'function') return wbEsc(s); }catch(e){}
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
function _upNotify(msg, kind){
    try{ if(typeof notify === 'function'){ notify(msg, kind); return; } }catch(e){}
    try{ console.log('[UniDBPicker][' + (kind || 'info') + ']', msg); }catch(e2){}
}

/* ── Low-level building blocks (kept global: the wizard calls them) ── */
var _dbPickStore = {};
try{ _dbPickStore = (typeof window !== 'undefined') ? (window._dbPickCb = window._dbPickCb || {}) : {}; }catch(e){ _dbPickStore = {}; }
let _dbPickSeq = 0;
let _dbSearchState = null;
function dbPickBtnHTML(onclick, title){
    return '<button type="button" onclick="' + onclick + '" class="shrink-0 px-2.5 py-1.5 bg-white border border-slate-200 rounded-lg text-slate-500 hover:text-indigo-700 hover:border-indigo-300" title="' + _upEsc(title || 'بحث') + '"><i class="fa-solid fa-magnifying-glass text-[11px]"></i></button>';
}
function dbPickWrapHTML(selectHTML, btnOnclick, btnTitle){
    return '<div class="flex items-stretch gap-1">' + selectHTML + dbPickBtnHTML(btnOnclick, btnTitle) + '</div>';
}
function dbSearchOpen(title, items, onPick){
    // items: [{value, label, sub}] — onPick(item)
    const id = 'dbq' + (++_dbPickSeq);
    _dbPickStore[id] = onPick;
    _dbSearchState = {id: id, title: String(title || 'بحث'), items: items || [], filtered: items || []};
    let m = document.getElementById('dbSearchModal');
    if(!m){
        m = document.createElement('div');
        m.id = 'dbSearchModal';
        m.className = 'hidden fixed inset-0 z-[95] bg-slate-900/50 backdrop-blur-sm flex items-center justify-center p-4';
        document.body.appendChild(m);
    }
    m.innerHTML = '<div class="bg-white rounded-2xl shadow-2xl w-full max-w-lg max-h-[80vh] overflow-hidden flex flex-col">'
        + '<div class="px-5 py-3 border-b border-slate-200 flex items-center justify-between bg-indigo-50 shrink-0"><h3 class="text-sm font-bold text-slate-800"><i class="fa-solid fa-magnifying-glass text-indigo-600"></i> ' + _upEsc(title || 'بحث') + '</h3><button onclick="dbSearchClose()" class="text-slate-400 hover:text-slate-600"><i class="fa-solid fa-xmark"></i></button></div>'
        + '<div class="px-4 pt-3 shrink-0"><input id="dbSearchInput" oninput="dbSearchFilter(this.value)" placeholder="ابحث…" class="wb-cell"><div id="dbSearchCount" class="text-[10px] text-slate-400 pt-1"></div></div>'
        + '<div id="dbSearchList" class="p-3 space-y-1 overflow-y-auto custom-scroll flex-1 min-h-0"></div>'
        + '<div class="px-5 py-3 bg-slate-50 border-t border-slate-200 flex items-center justify-end shrink-0"><button onclick="dbSearchClose()" class="px-4 py-1.5 bg-slate-100 rounded-xl text-xs">إغلاق</button></div>'
        + '</div>';
    m.classList.remove('hidden');
    dbSearchPaint();
    setTimeout(()=>{ const el = document.getElementById('dbSearchInput'); if(el){ el.value = ''; el.focus(); } }, 60);
}
function dbSearchClose(){ const m = document.getElementById('dbSearchModal'); if(m) m.classList.add('hidden'); _dbSearchState = null; }
function dbSearchFilter(q){
    const st = _dbSearchState;
    if(!st) return;
    q = String(q || '').trim().toLowerCase();
    st.filtered = !q ? st.items : st.items.filter(it=>((it.label || '') + ' ' + (it.value || '') + ' ' + (it.sub || '')).toLowerCase().includes(q));
    dbSearchPaint();
}
function dbSearchPaint(){
    const st = _dbSearchState;
    if(!st) return;
    const list = document.getElementById('dbSearchList');
    const cnt = document.getElementById('dbSearchCount');
    if(cnt) cnt.textContent = st.filtered.length + ' / ' + st.items.length;
    if(list) list.innerHTML = st.filtered.slice(0, 300).map((it, i)=>'<button type="button" onclick="dbSearchPick(' + i + ')" class="w-full flex items-center gap-2 px-2.5 py-1.5 hover:bg-indigo-50 border border-transparent hover:border-indigo-200 rounded-lg text-right"><span class="font-mono font-bold text-slate-800 truncate flex-1" dir="ltr">' + _upEsc(it.label) + '</span>' + (it.sub ? '<span class="text-[10px] text-slate-400 shrink-0">' + _upEsc(it.sub) + '</span>' : '') + '</button>').join('') || '<p class="text-slate-400 text-xs p-2">لا نتائج</p>';
}
function dbSearchPick(i){
    const st = _dbSearchState;
    if(!st) return;
    const it = st.filtered[i];
    if(!it) return;
    const cb = _dbPickStore[st.id];
    try{ delete _dbPickStore[st.id]; }catch(e){}
    dbSearchClose();
    if(typeof cb === 'function'){ try{ cb(it); }catch(e){ _upNotify(String((e && e.message) || e), 'error'); } }
}
function dbPickSetSelect(selectId, value, label){
    const sel = document.getElementById(selectId);
    if(!sel) return false;
    value = String(value == null ? '' : value);
    let opt = null;
    try{ opt = [...sel.options].find(o=>o.value === value); }catch(e){}
    if(!opt && value){
        opt = document.createElement('option');
        opt.value = value;
        opt.textContent = label || value;
        sel.appendChild(opt);
    }
    sel.value = value;
    try{ sel.dispatchEvent(new Event('change', {bubbles: true})); }catch(e){ try{ if(typeof sel.onchange === 'function') sel.onchange(); }catch(e2){} }
    return true;
}

/* ── High-level API: the wizard calls UniDBPicker ── */
var UniDBPicker = (function(){
    var tblCache = {};
    var colCache = {};
    function normTables(d){
        const arr = (d.tables || d.data || d.items || []);
        return arr.map(t=>(typeof t === 'string' ? t : (t.full || t.name || t.table || ''))).filter(Boolean);
    }
    function normCols(d){
        const arr = (d.columns || d.data || d.items || []);
        return arr.map(c=>(typeof c === 'string' ? {name: c, type: ''} : {name: (c.name || c.column || ''), type: (c.type || '')})).filter(c=>c.name);
    }
    // choose table from DB (cached) → Promise<[fullName]>
    function tables(conn){
        conn = String(conn || '');
        if(!conn) return Promise.resolve([]);
        if(tblCache[conn]) return Promise.resolve(tblCache[conn].slice());
        return fetch('/api/connections/' + encodeURIComponent(conn) + '/tables/')
            .then(r=>r.json()).then(d=>{
                if(d.error) throw new Error(d.error);
                const names = normTables(d);
                tblCache[conn] = names;
                return names.slice();
            });
    }
    // choose column from DB (cached) → Promise<[{name, type}]>
    function columns(conn, table){
        conn = String(conn || '');
        table = String(table || '');
        if(!conn || !table) return Promise.resolve([]);
        const key = conn + '||' + table.toLowerCase();
        if(colCache[key]) return Promise.resolve(colCache[key].slice());
        return fetch('/api/connections/' + encodeURIComponent(conn) + '/tables/' + encodeURIComponent(table) + '/columns/')
            .then(r=>r.json()).then(d=>{
                if(d.error) throw new Error(d.error);
                const cols = normCols(d);
                colCache[key] = cols;
                return cols.slice();
            });
    }
    function paintSelect(sel, opts, keepVal, emptyLabel){
        if(!sel) return;
        sel.innerHTML = '<option value="">' + _upEsc(emptyLabel || '— اختر —') + '</option>'
            + opts.map(o=>{
                const v = (typeof o === 'string') ? o : o.value;
                const l = (typeof o === 'string') ? o : (o.label || o.value);
                return '<option value="' + _upEsc(v) + '">' + _upEsc(l) + '</option>';
            }).join('');
        if(keepVal){
            const hit = opts.some(o=>((typeof o === 'string') ? o : o.value) === keepVal);
            if(hit) sel.value = keepVal;
            else { const o = document.createElement('option'); o.value = keepVal; o.textContent = keepVal; sel.appendChild(o); sel.value = keepVal; }
        }
    }
    // select from menu in modal: fill a <select> with DB tables
    function fillTableSelect(selectId, conn, keepVal){
        const sel = document.getElementById(selectId);
        if(!sel) return Promise.resolve([]);
        conn = String(conn || '');
        if(!conn){
            try{
                if(typeof wizardConnections !== 'undefined' && wizardConnections.length) conn = String(wizardConnections[0]);
            }catch(e){}
        }
        if(!conn){ sel.innerHTML = '<option value="">— اختر الاتصال أولاً —</option>'; return Promise.resolve([]); }
        sel.innerHTML = '<option value="">جاري التحميل...</option>';
        return tables(conn).then(names=>{
            const s2 = document.getElementById(selectId);
            if(s2) paintSelect(s2, names, keepVal);
            return names;
        }).catch(e=>{
            const s2 = document.getElementById(selectId);
            if(s2) s2.innerHTML = '<option value="">' + _upEsc(String((e && e.message) || e)) + '</option>';
            return [];
        });
    }
    // select from menu in modal: fill a <select> with DB columns
    function fillColumnSelect(selectId, conn, table, keepVal, withTypes){
        const sel = document.getElementById(selectId);
        if(!sel) return Promise.resolve([]);
        conn = String(conn || '');
        table = String(table || '');
        if(!conn && table){
            try{
                if(typeof dpTableConn === 'function') conn = dpTableConn(table);
                if(!conn && typeof wizardConnections !== 'undefined' && wizardConnections.length) conn = String(wizardConnections[0]);
            }catch(e){}
        }
        if(!conn || !table){ sel.innerHTML = '<option value="">— اختر الجدول أولاً —</option>'; return Promise.resolve([]); }
        sel.innerHTML = '<option value="">جاري التحميل...</option>';
        return columns(conn, table).then(cols=>{
            const s2 = document.getElementById(selectId);
            if(s2){
                const opts = withTypes
                    ? cols.map(c=>({value: c.name, label: c.name + ' (' + (c.type || '') + ')'}))
                    : cols.map(c=>c.name);
                paintSelect(s2, opts, keepVal);
            }
            return cols;
        }).catch(e=>{
            const s2 = document.getElementById(selectId);
            if(s2) s2.innerHTML = '<option value="">' + _upEsc(String((e && e.message) || e)) + '</option>';
            return [];
        });
    }
    // search in modal → pick table → onPick({value, label})
    function pickTable(opts){
        opts = opts || {};
        const conn = String(opts.conn || '');
        if(!conn){ _upNotify('اختر الاتصال أولاً', 'info'); return; }
        tables(conn).then(names=>{
            if(!names.length){ _upNotify('لا جداول', 'info'); return; }
            dbSearchOpen(opts.title || 'بحث في الجداول',
                names.map(t=>({value: t, label: t, sub: opts.sub || ''})),
                opts.onPick);
        }).catch(e=>_upNotify(String((e && e.message) || e), 'error'));
    }
    // search in modal → pick column → onPick({value, label, sub})
    function pickColumn(opts){
        opts = opts || {};
        const conn = String(opts.conn || ''), table = String(opts.table || '');
        if(!conn || !table){ _upNotify('اختر الاتصال والجدول أولاً', 'info'); return; }
        columns(conn, table).then(cols=>{
            if(!cols.length){ _upNotify('لا أعمدة', 'info'); return; }
            dbSearchOpen(opts.title || 'بحث في الأعمدة',
                cols.map(c=>({value: c.name, label: c.name, sub: c.type || table})),
                opts.onPick);
        }).catch(e=>_upNotify(String((e && e.message) || e), 'error'));
    }
    // snapshot a menu's options for dialog search (report-side selects)
    function selOptions(selectId){
        const sel = document.getElementById(selectId);
        if(!sel) return [];
        try{
            return [...sel.options].filter(o=>o.value).map(o=>({value: o.value, label: o.textContent || o.value, sub: ''}));
        }catch(e){ return []; }
    }
    return {
        tables: tables,
        columns: columns,
        fillTableSelect: fillTableSelect,
        fillColumnSelect: fillColumnSelect,
        pickTable: pickTable,
        pickColumn: pickColumn,
        selOptions: selOptions,
        searchOpen: function(t, i, cb){ return dbSearchOpen(t, i, cb); },
        setSelect: function(id, v, l){ return dbPickSetSelect(id, v, l); },
        btnHTML: function(fn, t){ return dbPickBtnHTML(fn, t); },
        wrapHTML: function(s, fn, t){ return dbPickWrapHTML(s, fn, t); }
    };
})();
