/* ─────────────────────────────────────────────────────────────
   UniCondition.js — Unified free-condition component for the designer.
   Single free-text WHERE field + autocomplete (fields [..], conditional
   functions IF/CASE/DECODE/..., @refs, $rules) via the host exprAuto,
   + expand-modal button + field-picker button + quick chips
   (AND/OR/NOT/IF()/CASE/CURRENT_DATE) inserted at the cursor.
   Used in: import filter section, table general-condition modal,
   ... (any free WHERE input via fieldHTML).
   Depends on: host exprAuto(input, target), openExprExpand(idx, false, kind),
   openExprBuilder(idx, false, kind) — must load before the wizard script.
   Contract: caller passes onInput JS + target/expandKind/builderKind strings;
   autocomplete picks flow back through host exprACSet(target, value).
   ───────────────────────────────────────────────────────────── */
function _ucEsc(s){
    try{ if(typeof wbEsc === 'function') return wbEsc(s); }catch(e){}
    try{ if(typeof _ueEsc === 'function') return _ueEsc(s); }catch(e){}
    try{ if(typeof rptEsc === 'function') return rptEsc(s); }catch(e){}
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

var UniCondition = (function(){
    /* Insert text at the cursor of a field, then re-fire input so the
       caller state + autocomplete stay in sync. */
    function insertAt(id, text){
        const el = document.getElementById(id);
        if(!el) return;
        text = String(text == null ? '' : text);
        try{
            const s = (typeof el.selectionStart === 'number') ? el.selectionStart : (el.value || '').length;
            const e = (typeof el.selectionEnd === 'number') ? el.selectionEnd : s;
            el.value = (el.value || '').slice(0, s) + text + (el.value || '').slice(e);
            const cp = s + text.length;
            try{ el.focus(); }catch(_){}
            try{ el.setSelectionRange(cp, cp); }catch(_){}
            el.dispatchEvent(new Event('input', {bubbles: true}));
        }catch(err){
            el.value = (el.value || '') + text;
        }
    }
    /* Programmatic sync (autocomplete picks / expand-modal saves). */
    function setValue(id, v){
        const el = document.getElementById(id);
        if(!el) return;
        el.value = String(v == null ? '' : v);
    }
    /* Quick chips: boolean glue + conditional-expression templates. */
    function chipRow(inputId){
        const id = String(inputId).replace(/'/g, "\\'");
        const chip = (t, title)=>{
            return '<button type="button" onclick="UniCondition.insertAt(\'' + id + '\', \'' + t.replace(/'/g, "\\'") + '\')" title="' + _ucEsc(title) + '" class="px-1.5 py-0.5 bg-white border border-slate-200 rounded-lg text-slate-500 hover:text-indigo-700 hover:border-indigo-300 font-mono text-[10px]" dir="ltr">' + _ucEsc(t.trim()) + '</button>';
        };
        return '<div class="flex items-center gap-1 flex-wrap pt-1">'
            + chip(' AND ', 'إضافة AND')
            + chip(' OR ', 'إضافة OR')
            + chip(' NOT ', 'إضافة NOT')
            + chip('IF(, , )', 'تعبير شرطي IF (يُترجم CASE WHEN)')
            + chip('CASE WHEN  THEN  ELSE  END', 'تعبير شرطي CASE')
            + chip('CURRENT_DATE', 'تاريخ اليوم الديناميكي')
            + '</div>';
    }
    /* Column chips of ONE table: click inserts [table.col] at the cursor. */
    function colChips(inputId, table, cols){
        const id = String(inputId).replace(/'/g, "\\'");
        const tb = String(table || '').trim();
        const list = (cols || []).filter(c=>c && String(c.name || '').trim());
        if(!tb || !list.length) return '';
        return '<div class="flex items-center gap-1 flex-wrap pt-1">'
            + '<span class="text-[10px] text-slate-400 font-bold">أعمدة ' + _ucEsc(tb) + ':</span>'
            + list.map(c=>{
                const cn = String(c.name).trim();
                const tok = '[' + tb + '.' + cn + ']';
                const hint = cn + (((c && c.type) || '') ? ' • ' + c.type : '');
                return '<button type="button" onclick="UniCondition.insertAt(\'' + id + '\', \'' + tok.replace(/'/g, "\\'") + '\')" title="' + _ucEsc(hint) + '" class="px-1.5 py-0.5 bg-white border border-slate-200 rounded-lg text-indigo-700 hover:text-indigo-900 hover:border-indigo-300 font-mono text-[10px]" dir="ltr">' + _ucEsc(cn) + '</button>';
            }).join('')
            + '</div>';
    }
    /* fieldHTML(inputId, opts): single free-condition field.
       opts: {value, placeholder, onInput (JS with `this`), target,
              expandKind, builderKind, chips (bool), cols ([{name,type}]),
              table (short name for cols), cls} */
    function fieldHTML(inputId, opts){
        opts = opts || {};
        const id = String(inputId);
        const idQ = id.replace(/'/g, "\\'");
        const target = String(opts.target || ('tcond:' + id));
        const tgtQ = target.replace(/'/g, "\\'");
        const expandKind = String(opts.expandKind || target).replace(/'/g, "\\'");
        const builderKind = String(opts.builderKind || target).replace(/'/g, "\\'");
        const onInput = String(opts.onInput || '');
        const cls = opts.cls || 'flex-1 min-w-0 bg-white border border-slate-200 rounded-lg px-2 py-1.5 text-xs font-mono focus:outline-none focus:border-amber-500';
        let h = '<div class="flex gap-1">'
            + '<input id="' + _ucEsc(id) + '" value="' + _ucEsc(opts.value || '').replace(/"/g, '&quot;') + '"'
            + ' oninput="' + onInput.replace(/"/g, '&quot;') + ';exprAuto(this,\'' + tgtQ + '\')"'
            + ' placeholder="' + _ucEsc(opts.placeholder || '[DATE_COL] >= CURRENT_DATE AND [AMT] > 100') + '"'
            + ' autocomplete="off" class="' + cls + '" dir="ltr">'
            + '<button type="button" onclick="openExprExpand(0, false, \'' + expandKind + '\')" title="تحرير موسع" class="px-2.5 rounded-lg bg-violet-50 hover:bg-violet-100 text-violet-700 shrink-0"><i class="fa-solid fa-expand text-[11px]"></i></button>'
            + '<button type="button" onclick="openExprBuilder(0, false, \'' + builderKind + '\')" title="اختيار الاتصال ← الجدول ← الحقل" class="px-2.5 rounded-lg bg-indigo-50 hover:bg-indigo-100 text-indigo-700 shrink-0"><i class="fa-solid fa-magnifying-glass text-[11px]"></i></button>'
            + '</div>';
        if(opts.chips) h += chipRow(idQ);
        if(opts.cols) h += colChips(idQ, opts.table, opts.cols);
        return h;
    }
    return {
        fieldHTML: fieldHTML,
        chipRow: chipRow,
        colChips: colChips,
        insertAt: insertAt,
        setValue: setValue
    };
})();
