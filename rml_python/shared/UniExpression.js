/* ─────────────────────────────────────────────────────────────
   UniExpression.js — Unified expression component for the designer.
   Main UI: expression field + exp_expand_button + exp_inputs_button.
   - field + expand button move in as-is (caller passes its expand onclick)
   - exp_inputs_button opens a modal with a TREE of <input>s, one per
     function parameter (name + _explain + datatype), e.g.
       IF(cond, a, IF(cond, b, c))
     Nested calls render as sub-trees; +f next to each input adds a
     nested function (SQL + XSQL catalog, live from connections).
   Used in: view table columns, report columns, form computed fields,
   group-by expressions, ... (any expression input via openForId).
   Depends on: UniDBPicker.js (dbSearchOpen) — must load after it.
   ───────────────────────────────────────────────────────────── */
function _ueEsc(s){
    try{ if(typeof wbEsc === 'function') return wbEsc(s); }catch(e){}
    try{ if(typeof rptEsc === 'function') return rptEsc(s); }catch(e){}
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
function _ueNotify(msg, kind){
    try{ if(typeof notify === 'function'){ notify(msg, kind); return; } }catch(e){}
    try{ console.log('[UniExpression][' + (kind || 'info') + ']', msg); }catch(e2){}
}

var UniExpression = (function(){
    /* ── Static SQL catalog: {f, p:[{n,t,d}], d, k, tpl?} ──
       n = parameter name, t = datatype tag, d = _explain (Arabic).
       tpl with {0},{1}.. overrides default FN(a, b) serialization. */
    var UE_SQL = [
        {f:'SUM', p:[{n:'x', t:'رقم', d:'العمود أو التعبير المراد جمعه'}], d:'مجموع القيم', k:'agg'},
        {f:'AVG', p:[{n:'x', t:'رقم', d:'العمود أو التعبير المراد توسطه'}], d:'المتوسط الحسابي', k:'agg'},
        {f:'COUNT', p:[{n:'x', t:'أي', d:'عمود أو * لعد الصفوف'}], d:'عدد الصفوف غير الفارغة', k:'agg'},
        {f:'MIN', p:[{n:'x', t:'أي', d:'العمود أو التعبير'}], d:'أصغر قيمة', k:'agg'},
        {f:'MAX', p:[{n:'x', t:'أي', d:'العمود أو التعبير'}], d:'أكبر قيمة', k:'agg'},
        {f:'STDDEV', p:[{n:'x', t:'رقم', d:'العمود الرقمي'}], d:'الانحراف المعياري', k:'agg'},
        {f:'VARIANCE', p:[{n:'x', t:'رقم', d:'العمود الرقمي'}], d:'التباين', k:'agg'},
        {f:'IF', p:[{n:'cond', t:'شرط', d:'الشرط (مثال: [a] > 100)'}, {n:'a', t:'أي', d:'القيمة إذا تحقق الشرط'}, {n:'b', t:'أي', d:'القيمة إذا لم يتحقق'}], d:'شرط (يُترجم CASE WHEN)', k:'xsql'},
        {f:'DECODE', p:[{n:'expr', t:'أي', d:'التعبير المفحوص'}, {n:'v1', t:'أي', d:'القيمة الأولى للمطابقة'}, {n:'r1', t:'أي', d:'النتيجة الأولى'}, {n:'def', t:'أي', d:'الافتراضي (اختياري)'}], d:'مطابقة قيم (Oracle)', k:'cond'},
        {f:'CASE', p:[{n:'cond', t:'شرط', d:'شرط WHEN'}, {n:'a', t:'أي', d:'قيمة THEN'}, {n:'b', t:'أي', d:'قيمة ELSE'}], d:'تعبير شرطي', k:'cond', tpl:'CASE WHEN {0} THEN {1} ELSE {2} END'},
        {f:'COALESCE', p:[{n:'a', t:'أي', d:'القيمة الأولى'}, {n:'b', t:'أي', d:'البديل عند NULL'}], d:'أول قيمة غير فارغة', k:'null', variadic:true},
        {f:'NULLIF', p:[{n:'a', t:'أي', d:'التعبير'}, {n:'b', t:'أي', d:'القيمة المحولة لـ NULL'}], d:'NULL عند التساوي', k:'null'},
        {f:'NVL', p:[{n:'x', t:'أي', d:'التعبير'}, {n:'dflt', t:'أي', d:'البديل عند NULL'}], d:'بديل NULL (Oracle)', k:'null'},
        {f:'UPPER', p:[{n:'x', t:'نص', d:'النص'}], d:'أحرف كبيرة', k:'str'},
        {f:'LOWER', p:[{n:'x', t:'نص', d:'النص'}], d:'أحرف صغيرة', k:'str'},
        {f:'TRIM', p:[{n:'x', t:'نص', d:'النص'}], d:'إزالة الفراغات', k:'str'},
        {f:'LTRIM', p:[{n:'x', t:'نص', d:'النص'}], d:'إزالة فراغات البداية', k:'str'},
        {f:'RTRIM', p:[{n:'x', t:'نص', d:'النص'}], d:'إزالة فراغات النهاية', k:'str'},
        {f:'LENGTH', p:[{n:'x', t:'نص', d:'النص'}], d:'طول النص', k:'str'},
        {f:'SUBSTR', p:[{n:'x', t:'نص', d:'النص'}, {n:'from', t:'رقم', d:'موضع البداية (1=الأول)'}, {n:'len', t:'رقم', d:'الطول (اختياري)'}], d:'جزء من النص', k:'str'},
        {f:'INSTR', p:[{n:'x', t:'نص', d:'النص'}, {n:'s', t:'نص', d:'النص المطلوب'}], d:'موضع نص داخل نص', k:'str'},
        {f:'REPLACE', p:[{n:'x', t:'نص', d:'النص'}, {n:'old', t:'نص', d:'القديم'}, {n:'nw', t:'نص', d:'الجديد'}], d:'استبدال نص', k:'str'},
        {f:'CONCAT', p:[{n:'a', t:'نص', d:'الأول'}, {n:'b', t:'نص', d:'الثاني'}], d:'دمج نصين', k:'str', variadic:true},
        {f:'LPAD', p:[{n:'x', t:'نص', d:'النص'}, {n:'len', t:'رقم', d:'الطول الكلي'}, {n:'pad', t:'نص', d:'حرف التعبئة'}], d:'تعبئة يسار', k:'str'},
        {f:'RPAD', p:[{n:'x', t:'نص', d:'النص'}, {n:'len', t:'رقم', d:'الطول الكلي'}, {n:'pad', t:'نص', d:'حرف التعبئة'}], d:'تعبئة يمين', k:'str'},
        {f:'ABS', p:[{n:'x', t:'رقم', d:'الرقم'}], d:'القيمة المطلقة', k:'math'},
        {f:'ROUND', p:[{n:'x', t:'رقم', d:'الرقم'}, {n:'d', t:'رقم', d:'المنازل (اختياري)'}], d:'تقريب', k:'math'},
        {f:'CEIL', p:[{n:'x', t:'رقم', d:'الرقم'}], d:'سقف (أعلى صحيح)', k:'math'},
        {f:'FLOOR', p:[{n:'x', t:'رقم', d:'الرقم'}], d:'أرضية (أدنى صحيح)', k:'math'},
        {f:'MOD', p:[{n:'a', t:'رقم', d:'المقسوم'}, {n:'b', t:'رقم', d:'المقسوم عليه'}], d:'باقي القسمة', k:'math'},
        {f:'POWER', p:[{n:'x', t:'رقم', d:'الأساس'}, {n:'e', t:'رقم', d:'الأس'}], d:'أس', k:'math'},
        {f:'SQRT', p:[{n:'x', t:'رقم', d:'الرقم'}], d:'الجذر التربيعي', k:'math'},
        {f:'TRUNC', p:[{n:'x', t:'أي', d:'الرقم/التاريخ'}, {n:'f', t:'نص', d:'الصيغة (اختياري)'}], d:'اقتطاع', k:'math'},
        {f:'SIGN', p:[{n:'x', t:'رقم', d:'الرقم'}], d:'إشارة الرقم (1/0/1-)', k:'math'},
        {f:'GREATEST', p:[{n:'a', t:'أي', d:'الأول'}, {n:'b', t:'أي', d:'الثاني'}], d:'الأكبر', k:'math', variadic:true},
        {f:'LEAST', p:[{n:'a', t:'أي', d:'الأول'}, {n:'b', t:'أي', d:'الثاني'}], d:'الأصغر', k:'math', variadic:true},
        {f:'TO_CHAR', p:[{n:'x', t:'أي', d:'القيمة'}, {n:'fmt', t:'نص', d:'الصيغة (مثال YYYY-MM-DD)'}], d:'تحويل لنص', k:'conv'},
        {f:'TO_DATE', p:[{n:'x', t:'نص', d:'النص'}, {n:'fmt', t:'نص', d:'الصيغة'}], d:'تحويل لتاريخ', k:'conv'},
        {f:'TO_NUMBER', p:[{n:'x', t:'نص', d:'النص'}], d:'تحويل لرقم', k:'conv'},
        {f:'CAST', p:[{n:'x', t:'أي', d:'التعبير'}, {n:'type', t:'نص', d:'النوع (مثال NUMERIC)'}], d:'تحويل النوع', k:'conv', tpl:'CAST({0} AS {1})'},
        {f:'EXTRACT', p:[{n:'unit', t:'نص', d:'الوحدة YEAR/MONTH/DAY/HOUR/MINUTE'}, {n:'x', t:'تاريخ', d:'التاريخ'}], d:'استخراج جزء من تاريخ', k:'date', tpl:'EXTRACT({0} FROM {1})'},
        {f:'ADD_MONTHS', p:[{n:'x', t:'تاريخ', d:'التاريخ'}, {n:'n', t:'رقم', d:'عدد الشهور'}], d:'إضافة شهور', k:'date'},
        {f:'MONTHS_BETWEEN', p:[{n:'a', t:'تاريخ', d:'الأول'}, {n:'b', t:'تاريخ', d:'الثاني'}], d:'شهور بين تاريخين', k:'date'},
        {f:'LAST_DAY', p:[{n:'x', t:'تاريخ', d:'التاريخ'}], d:'آخر يوم في الشهر', k:'date'},
        {f:'SYSDATE', p:[], d:'التاريخ والوقت الحالي (Oracle)', k:'date'},
        {f:'CURRENT_DATE', p:[], d:'تاريخ اليوم', k:'date'}
    ];
    var catCache = {};
    // Live catalog: static SQL + XSQL/functions absolutely from connections
    function catalog(opts){
        opts = opts || {};
        const key = String(opts.connection || 'all');
        if(catCache[key] && catCache[key].items) return Promise.resolve(catCache[key].items.slice());
        const staticItems = UE_SQL.map(e=>({fn: e.f, params: (e.p || []).map(p=>({n: p.n, t: p.t, d: p.d})),
            hint: e.d || '', kind: e.k || 'sql', tpl: e.tpl || '', variadic: !!e.variadic}));
        const url = '/api/formulas/autocomplete/?' + (opts.connection ? ('connection=' + encodeURIComponent(opts.connection)) : 'all=1');
        return fetch(url).then(r=>r.json()).then(d=>{
            const seen = {};
            staticItems.forEach(e=>{ seen[e.fn.toUpperCase()] = 1; });
            const out = staticItems.slice();
            ['migrated', 'custom', 'natives', 'defaults'].forEach(bucket=>{
                (d[bucket] || []).forEach(e=>{
                    const fn = String(e.fn || '').trim();
                    if(!fn || seen[fn.toUpperCase()]) return;
                    seen[fn.toUpperCase()] = 1;
                    // tpl like NAME(|, , ) → arity from top-level commas
                    let arity = 1;
                    try{
                        const tpl = String(e.tpl || '');
                        const inner = tpl.slice(tpl.indexOf('(') + 1, tpl.lastIndexOf(')'));
                        arity = inner.trim() ? splitTop(inner).length : 0;
                    }catch(_){ arity = 1; }
                    const params = [];
                    for(let i = 0; i < arity; i++) params.push({n: 'p' + (i + 1), t: 'أي', d: String(e.hint || '')});
                    out.push({fn: fn, params: params, hint: String(e.hint || ''), kind: String(e.kind || 'db'), tpl: ''});
                });
            });
            out.sort((a, b)=>String(a.fn).localeCompare(String(b.fn)));
            catCache[key] = {items: out};
            return out.slice();
        }).catch(()=>staticItems.slice());
    }
    function catEntry(items, fn){
        fn = String(fn || '').toUpperCase();
        for(let i = 0; i < (items || []).length; i++){
            if(String(items[i].fn || '').toUpperCase() === fn) return items[i];
        }
        return null;
    }

    /* ── Pure parser: expression → tree ── */
    function splitTop(s){
        const parts = [];
        let depth = 0, cur = '', quote = null;
        for(let i = 0; i < s.length; i++){
            const c = s[i];
            if(quote){
                cur += c;
                if(c === quote){
                    if(s[i + 1] === quote){ cur += s[i + 1]; i++; }
                    else quote = null;
                }
                continue;
            }
            if(c === "'" || c === '"'){ quote = c; cur += c; continue; }
            if(c === '(' || c === '[' || c === '{'){ depth++; cur += c; continue; }
            if(c === ')' || c === ']' || c === '}'){ depth--; cur += c; continue; }
            if(c === ',' && depth === 0){ parts.push(cur); cur = ''; continue; }
            cur += c;
        }
        parts.push(cur);
        return parts;
    }
    function parseExpr(src){
        let s = String(src == null ? '' : src).trim();
        if(s.startsWith('=') && !s.startsWith('==')) s = s.slice(1).trim();
        const m = s.match(/^([A-Za-z_][A-Za-z0-9_$#]*)\s*\(/);
        if(m && s.endsWith(')')){
            // verify the first '(' matches the final ')'
            let depth = 0, quote = null, ok = false;
            const start = s.indexOf('(');
            for(let i = start; i < s.length; i++){
                const c = s[i];
                if(quote){ if(c === quote) quote = null; continue; }
                if(c === "'" || c === '"'){ quote = c; continue; }
                if(c === '(') depth++;
                if(c === ')'){ depth--; if(depth === 0 && i === s.length - 1) ok = true; }
            }
            if(ok){
                const inner = s.slice(start + 1, -1);
                const rawArgs = inner.trim() === '' ? [] : splitTop(inner);
                return {t: 'fn', fn: m[1], args: rawArgs.map(a=>parseExpr(a))};
            }
        }
        return {t: 'raw', text: s};
    }
    function serialize(node){
        if(!node) return '';
        if(node.t === 'fn'){
            const entry = catEntry(UE_SQL.map(e=>({fn: e.f, tpl: e.tpl || ''})), node.fn);
            const parts = (node.args || []).map(serialize);
            if(entry && entry.tpl){
                let out = entry.tpl;
                for(let i = 0; i < parts.length; i++) out = out.split('{' + i + '}').join(parts[i]);
                return out.replace(/\{\d+\}/g, '');
            }
            return node.fn + '(' + parts.join(', ') + ')';
        }
        return node.text || '';
    }

    /* ── Tree modal state ── */
    let T = null; // {title, get, set, root, connection, items}
    let _seq = 0;
    function nav(path){
        let n = T.root;
        for(let i = 0; i < path.length; i++){
            if(!n || n.t !== 'fn' || !n.args || !n.args[path[i]]) return null;
            n = n.args[path[i]];
        }
        return n;
    }
    function openTree(opts){
        opts = opts || {};
        T = {title: opts.title || 'محرر التعبير الشجري', get: opts.get || (()=>''), set: opts.set || (()=>{}),
             connection: opts.connection || '', items: null, root: parseExpr(opts.expr !== undefined ? opts.expr : (opts.get ? opts.get() : ''))};
        let m = document.getElementById('ueTreeModal');
        if(!m){
            m = document.createElement('div');
            m.id = 'ueTreeModal';
            m.className = 'hidden fixed inset-0 z-[85] bg-slate-900/50 backdrop-blur-sm flex items-center justify-center p-4';
            document.body.appendChild(m);
        }
        m.innerHTML = '<div class="bg-white rounded-2xl shadow-2xl w-full max-w-2xl max-h-[90vh] overflow-hidden flex flex-col" dir="rtl">'
            + '<div class="px-5 py-3 border-b border-slate-200 flex items-center justify-between bg-emerald-50 shrink-0">'
            + '<h3 class="text-sm font-bold text-slate-800"><i class="fa-solid fa-calculator text-emerald-600"></i> ' + _ueEsc(T.title) + '</h3>'
            + '<div class="flex items-center gap-1">'
            + '<button onclick="UniExpression.refreshCatalog()" title="تحديث الدوال من الاتصالات" class="text-slate-400 hover:text-emerald-700 px-2"><i class="fa-solid fa-rotate"></i></button>'
            + '<button onclick="UniExpression.closeTree()" class="text-slate-400 hover:text-slate-600"><i class="fa-solid fa-xmark"></i></button>'
            + '</div></div>'
            + '<div id="ueTreeBody" class="p-4 space-y-2 overflow-y-auto custom-scroll flex-1 min-h-0 text-xs"></div>'
            + '<div class="px-5 py-2 bg-slate-50 border-t border-slate-100 shrink-0"><div class="text-[10px] text-slate-400 font-bold mb-1">المعاينة:</div><div id="ueTreePreview" class="font-mono text-[11px] text-slate-700 break-all" dir="ltr"></div></div>'
            + '<div class="px-5 py-3 bg-slate-50 border-t border-slate-200 flex items-center justify-end gap-2 shrink-0">'
            + '<button onclick="UniExpression.closeTree()" class="px-4 py-1.5 bg-slate-100 rounded-xl text-xs">إلغاء</button>'
            + '<button onclick="UniExpression.applyTree()" class="px-4 py-1.5 bg-emerald-600 hover:bg-emerald-700 text-white rounded-xl text-xs font-bold">تطبيق على الحقل</button>'
            + '</div></div>';
        m.classList.remove('hidden');
        paintTree();
        try{ if(typeof hideExprAC === 'function') hideExprAC(); }catch(e){}
        catalog({connection: T.connection}).then(items=>{ T.items = items; paintTree(); }).catch(()=>{});
    }
    function closeTree(){ const m = document.getElementById('ueTreeModal'); if(m) m.classList.add('hidden'); T = null; try{ if(typeof hideExprAC === 'function') hideExprAC(); }catch(e){} }
    function refreshCatalog(){
        const key = String((T && T.connection) || 'all');
        delete catCache[key];
        catalog({connection: T ? T.connection : ''}).then(items=>{ if(T) T.items = items; paintTree(); _ueNotify('حُدثت الدوال من الاتصالات', 'success'); }).catch(()=>{});
    }
    function paintTree(){
        if(!T) return;
        const host = document.getElementById('ueTreeBody');
        if(host) host.innerHTML = nodeHTML(T.root, []);
        paintPreview();
    }
    function paintPreview(){
        const pv = document.getElementById('ueTreePreview');
        if(pv && T){ try{ pv.textContent = serialize(T.root) || '—'; }catch(e){} }
    }
    function entryFor(fn){
        if(T && T.items){ const e = catEntry(T.items, fn); if(e) return e; }
        for(let i = 0; i < UE_SQL.length; i++) if(UE_SQL[i].f.toUpperCase() === String(fn).toUpperCase()) return UE_SQL[i];
        return null;
    }
    function nodeHTML(node, path){
        if(!node || node.t !== 'fn'){
            const ph = 'P' + path.join('.');
            return '<div class="flex items-stretch gap-1">'
                + '<input id="ueIn_' + ph + '" value="' + _ueEsc(node ? node.text : '') + '" oninput="UniExpression._in(\'' + ph + '\', this)" dir="ltr" class="wb-cell font-mono flex-1 min-w-0" placeholder="قيمة / [عمود] / @مرجع">'
                + '<button type="button" onclick="UniExpression._addFn(\'' + ph + '\')" title="+f إضافة دالة متداخلة" class="shrink-0 px-2 py-1 bg-indigo-50 hover:bg-indigo-100 text-indigo-700 border border-indigo-200 rounded-lg font-mono font-bold text-[11px]">+f</button>'
                + '</div>';
        }
        const entry = entryFor(node.fn);
        const params = (entry && (entry.p || entry.params)) || [];
        const rows = (node.args || []).map((a, i)=>{
            const pd = params[i] || params[params.length - 1] || {n: 'p' + (i + 1), t: 'أي', d: ''};
            const ph = 'P' + path.concat([i]).join('.');
            return '<div class="flex items-start gap-1.5">'
                + '<div class="w-24 shrink-0 pt-1.5"><div class="font-mono font-bold text-indigo-900 text-[11px]" dir="ltr">' + _ueEsc(pd.n || ('p' + (i + 1))) + '</div>'
                + (pd.d ? '<div class="text-[9px] text-slate-400 leading-tight">' + _ueEsc(pd.d) + '</div>' : '')
                + (pd.t ? '<div class="text-[9px] text-slate-500 font-bold">(' + _ueEsc(pd.t) + ')</div>' : '')
                + '</div>'
                + '<div class="flex-1 min-w-0">' + nodeHTML(a, path.concat([i])) + '</div>'
                + '</div>';
        }).join('');
        const ph = 'P' + path.join('.');
        return '<div class="border border-emerald-200 bg-emerald-50/50 rounded-xl p-2 space-y-1.5">'
            + '<div class="flex items-center gap-2">'
            + '<span class="font-mono font-bold text-emerald-800 text-xs" dir="ltr">' + _ueEsc(node.fn) + '</span>'
            + '<span class="text-[9px] text-slate-400">' + _ueEsc((entry && (entry.d || entry.hint)) || '') + '</span>'
            + '<span style="margin-right:auto"></span>'
            + ((entry && entry.variadic) ? '<button type="button" onclick="UniExpression._addParam(\'' + ph + '\')" title="إضافة معامل" class="text-[10px] text-emerald-600 font-bold">+ معامل</button>' : '')
            + '<button type="button" onclick="UniExpression._clear(\'' + ph + '\')" title="تفكيك الدالة" class="text-slate-300 hover:text-rose-500"><i class="fa-solid fa-xmark text-[10px]"></i></button>'
            + '</div>'
            + (rows || '<p class="text-[10px] text-slate-400">بلا معاملات</p>')
            + '</div>';
    }
    // input typing: update raw; a completed FN(...) call becomes a sub-tree
    function _in(ph, el){
        if(!T) return;
        const path = ph === 'P' ? [] : ph.slice(1).split('.').map(Number);
        // root raw has path [] encoded as 'P'
        const node = path.length ? nav(path) : T.root;
        if(!node) return;
        const v = el.value;
        if(node.t === 'fn'){
            // typing directly on a fn header is impossible (no header input) — ignore
            paintPreview();
            return;
        }
        node.text = v;
        const parsed = parseExpr(v);
        if(parsed.t === 'fn' && v.trim().endsWith(')')){
            if(path.length){ T.root && (nav(path.slice(0, -1)).args[path[path.length - 1]] = parsed); }
            else T.root = parsed;
            paintTree();
            focusFirstEmpty();
            try{ if(typeof exprAuto === 'function') exprAuto(document.getElementById('ueTreeBody').querySelector('input:focus') || el, 'uetree:' + ph); }catch(e){}
            return;
        }
        paintPreview();
        // إكمال المراجع [] و @ — محرك المصمم إن وجد، وإلا بديل مدمج بموفر الحقول
        try{
            if(typeof exprAuto === 'function'){ exprAuto(el, 'uetree:' + ph); }
            else { ueFallbackComplete(ph, el); }
        }catch(e){}
        try{ if(typeof exprSigTip === 'function') exprSigTip(el); }catch(e){}
    }
    // بديل مدمج (لا exprAuto — مثل مصمم النماذج): [ للأعمدة و @ للمرجعية عبر window._ueFieldsProvider
    function ueFallbackComplete(ph, el){
        if(!T || !el) return;
        let pos = null;
        try{ pos = (el.selectionStart !== undefined && el.selectionStart !== null) ? el.selectionStart : (el.value || '').length; }catch(e){ pos = (el.value || '').length; }
        const cur = el.value || '';
        if(pos !== cur.length) return;
        const trig = cur.endsWith('[') ? '[' : (cur.endsWith('@') ? '@' : null);
        if(!trig) return;
        let prov = null;
        try{ prov = (typeof window._ueFieldsProvider === 'function') ? window._ueFieldsProvider() : null; }catch(e){}
        if(!prov) return;
        const items = trig === '['
            ? ((prov.fields || []).map(f=>({value: '[' + f.name + ']', label: f.name, sub: f.table || ''})))
            : ((prov.refs || []).map(r=>({value: '@' + r.name, label: '@' + r.name, sub: r.label || ''})));
        if(!items.length) return;
        if(typeof dbSearchOpen !== 'function') return;
        T._pickCtx = {ph: ph, el: el, trig: trig};
        dbSearchOpen(trig === '[' ? 'إدراج عمود [...]' : 'إدراج مرجع @', items, (it)=>{
            const ctx = (T && T._pickCtx) || {};
            const tgt = ctx.el;
            if(!tgt) return;
            let p2 = null;
            try{ p2 = (tgt.selectionStart !== undefined && tgt.selectionStart !== null) ? tgt.selectionStart : (tgt.value || '').length; }catch(e){ p2 = (tgt.value || '').length; }
            const cv = tgt.value || '';
            tgt.value = cv.slice(0, p2) + it.value + cv.slice(p2);
            try{ tgt.setSelectionRange(p2 + it.value.length, p2 + it.value.length); }catch(e){}
            try{ tgt.focus(); }catch(e){}
            _in(ctx.ph || 'P', tgt);
        });
    }
    function focusFirstEmpty(){
        try{
            const host = document.getElementById('ueTreeBody');
            if(!host) return;
            const inputs = host.querySelectorAll('input');
            for(let i = 0; i < inputs.length; i++){
                if(!inputs[i].value){ inputs[i].focus(); return; }
            }
        }catch(e){}
    }
    // +f: pick a function from the catalog (SQL + XSQL, live) → nested node
    function _addFn(ph){
        if(!T) return;
        const ensure = T.items ? Promise.resolve(T.items) : catalog({connection: T.connection}).then(items=>{ T.items = items; return items; });
        ensure.then(items=>{
            const list = (items || []).map(e=>({value: e.fn, label: e.fn + (e.tpl ? e.tpl.slice(e.fn.length) : '(' + (e.params || []).map(p=>p.n).join(', ') + ')'), sub: e.hint || e.kind || ''}));
            if(typeof dbSearchOpen !== 'function'){ _ueNotify('UniDBPicker غير محمل', 'error'); return; }
            dbSearchOpen('إضافة دالة +f', list, (it)=>{
                const entry = catEntry(T.items, it.value);
                const n = (entry && entry.params && entry.params.length) || 0;
                const args = [];
                // pre-fill from tpl arity when catalog lacks param descriptors
                for(let i = 0; i < n; i++) args.push({t: 'raw', text: ''});
                const node = {t: 'fn', fn: it.value, args: args};
                if(!ph || ph === 'P'){
                    const cur = T.root;
                    if(cur && cur.t === 'raw' && cur.text.trim()){
                        T.root = {t: 'fn', fn: it.value, args: [{t: 'raw', text: cur.text}]};
                    } else T.root = node;
                } else {
                    const path = ph.slice(1).split('.').map(Number);
                    const parent = nav(path.slice(0, -1));
                    if(parent && parent.t === 'fn') parent.args[path[path.length - 1]] = node;
                }
                paintTree();
                focusFirstEmpty();
            });
        }).catch(()=>{});
    }
    function _addParam(ph){
        if(!T) return;
        const path = ph === 'P' ? [] : ph.slice(1).split('.').map(Number);
        const node = path.length ? nav(path) : T.root;
        if(node && node.t === 'fn'){ node.args = node.args || []; node.args.push({t: 'raw', text: ''}); paintTree(); focusFirstEmpty(); }
    }
    function _clear(ph){
        if(!T) return;
        if(!ph || ph === 'P'){ T.root = {t: 'raw', text: ''}; }
        else {
            const path = ph.slice(1).split('.').map(Number);
            const parent = nav(path.slice(0, -1));
            if(parent && parent.t === 'fn') parent.args[path[path.length - 1]] = {t: 'raw', text: ''};
        }
        paintTree();
    }
    function applyTree(){
        if(!T) return;
        let v = '';
        try{ v = serialize(T.root); }catch(e){ v = ''; }
        try{ T.set(v); }catch(e){}
        closeTree();
    }
    /* Main UI: expression field + exp_expand_button + exp_inputs_button.
       inputHTML: the <input ...> as-is (caller keeps its handlers/attrs).
       expandOnclick: existing expand JS as-is (or null to hide).
       targetId: input id for the inputs-button (openForId).
       extraHTML: optional extra buttons (e.g. field picker) appended as-is. */
    function fieldHTML(inputHTML, expandOnclick, targetId, extraHTML){
        let h = '<div class="flex items-stretch gap-1">';
        h += '<div class="flex-1 min-w-0">' + inputHTML + '</div>';
        if(expandOnclick){
            h += '<button type="button" onclick="' + expandOnclick + '" title="تحرير موسع" class="shrink-0 px-2 py-1 bg-white border border-slate-200 rounded-lg text-slate-500 hover:text-indigo-700 hover:border-indigo-300"><i class="fa-solid fa-expand text-[11px]"></i></button>';
        }
        if(targetId){
            h += '<button type="button" onclick="UniExpression.openForId(\'' + String(targetId).replace(/'/g, "\\'") + '\')" title="محرر التعبير الشجري" class="shrink-0 px-2 py-1 bg-white border border-emerald-200 rounded-lg text-emerald-600 hover:text-emerald-800 hover:border-emerald-400"><i class="fa-solid fa-calculator text-[11px]"></i></button>';
        }
        if(extraHTML) h += extraHTML;
        return h + '</div>';
    }
    function openForId(id, opts){
        const el = document.getElementById(id);
        if(!el){ _ueNotify('حقل التعبير غير موجود', 'error'); return; }
        opts = opts || {};
        openTree({
            title: opts.title || 'محرر التعبير الشجري',
            connection: opts.connection || '',
            get: ()=>el.value,
            set: (v)=>{
                el.value = v;
                try{ el.dispatchEvent(new Event('input', {bubbles: true})); }catch(e){}
                try{ el.dispatchEvent(new Event('change', {bubbles: true})); }catch(e){}
                if(typeof opts.onApply === 'function'){ try{ opts.onApply(v); }catch(e){} }
            }
        });
    }
    return {
        fieldHTML: fieldHTML,
        openForId: openForId,
        openTree: openTree,
        closeTree: closeTree,
        applyTree: applyTree,
        refreshCatalog: refreshCatalog,
        parse: parseExpr,
        serialize: serialize,
        splitTop: splitTop,
        catalog: catalog,
        _in: _in,
        _addFn: _addFn,
        _addParam: _addParam,
        _clear: _clear
    };
})();
