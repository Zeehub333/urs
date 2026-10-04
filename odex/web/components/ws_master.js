/* Master password gate — shared by manager (تعديل/إعدادات/add) and settings save.
 * Usage: wsMasterEnsure(actionFn) — ALWAYS prompts (every press); a successful
 * check stamps the session so the follow-through works (edit entry, design save).
 */
async function wsMasterStatus() {
    const res = await fetch('/api/master/status/');
    return res.json();
}
function wsMasterClose() {
    const m = document.getElementById('wsMasterModal');
    if (m) m.remove();
    window._wsMasterCb = null;
}
function wsMasterOpen(firstSet, cb) {
    window._wsMasterCb = (typeof cb === 'function') ? cb : null;
    let m = document.getElementById('wsMasterModal');
    if (m) m.remove();
    m = document.createElement('div');
    m.id = 'wsMasterModal';
    m.className = 'fixed inset-0 z-[90] bg-slate-900/50 backdrop-blur-sm flex items-center justify-center p-4';
    m.setAttribute('onclick', 'if(event.target===this)wsMasterClose()');
    const pwRow = (id, label, ph) =>
        '<div><label class="block font-semibold text-slate-700 mb-1">' + label + '</label>' +
        '<input id="' + id + '" type="password" dir="ltr" autocomplete="new-password" placeholder="' + ph + '" ' +
        'class="w-full bg-slate-50 border border-slate-200 rounded-xl px-3 py-2 font-mono"></div>';
    m.innerHTML = '<div class="bg-white rounded-2xl shadow-2xl w-full max-w-sm overflow-hidden">'
        + '<div class="px-5 py-3 border-b border-slate-200 flex items-center justify-between bg-amber-50">'
        + '<h3 class="text-sm font-bold text-slate-800"><i class="fa-solid fa-key text-amber-600"></i> '
        + (firstSet ? 'تعيين كلمة مرور الماستر' : 'كلمة مرور الماستر') + '</h3>'
        + '<button onclick="wsMasterClose()" class="text-slate-400 hover:text-slate-600"><i class="fa-solid fa-xmark"></i></button></div>'
        + '<div class="p-5 space-y-3 text-xs">'
        + (firstSet
            ? '<p class="text-[11px] text-slate-500">لم تُعيَّن بعد — أدخلها لأول مرة (6 أحرف فأكثر) وستُحفظ مشفرة.</p>' + pwRow('wsMasterPw1', 'كلمة المرور الجديدة', '') + pwRow('wsMasterPw2', 'تأكيد كلمة المرور', '')
            : '<p class="text-[11px] text-slate-500">هذا الإجراء (تعديل / إعدادات / إضافة) يتطلب كلمة مرور الماستر.</p>' + pwRow('wsMasterPw1', 'كلمة المرور', ''))
        + '<p id="wsMasterMsg" class="text-[11px] text-rose-600 font-bold"></p>'
        + '</div>'
        + '<div class="px-5 py-3 bg-slate-50 border-t border-slate-200 flex items-center justify-end gap-2">'
        + '<button onclick="wsMasterClose()" class="px-4 py-1.5 bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-xl text-xs">إلغاء</button>'
        + '<button onclick="wsMasterSubmit(' + (firstSet ? 'true' : 'false') + ')" class="px-4 py-1.5 bg-amber-600 hover:bg-amber-700 text-white rounded-xl text-xs font-bold">تأكيد</button>'
        + '</div></div>';
    document.body.appendChild(m);
    setTimeout(() => { const el = document.getElementById('wsMasterPw1'); if (el) el.focus(); }, 100);
}
async function wsMasterSubmit(firstSet) {
    const msg = document.getElementById('wsMasterMsg');
    const v = (id) => { const el = document.getElementById(id); return el ? el.value : ''; };
    let body;
    if (firstSet) {
        body = {new_password: v('wsMasterPw1'), confirm_password: v('wsMasterPw2')};
    } else {
        body = {password: v('wsMasterPw1')};
    }
    if (firstSet && String(body.new_password || '').length < 6) {
        if (msg) msg.textContent = '6 أحرف فأكثر';
        return;
    }
    try {
        const res = await fetch('/api/master/auth/', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
        const d = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(d.error || 'فشل التحقق');
        const cb = window._wsMasterCb;
        wsMasterClose();
        if (cb) cb();
    } catch (e) {
        if (msg) msg.textContent = String((e && e.message) || e).slice(0, 200);
    }
}
async function wsMasterEnsure(action) {
    // المطالبة في كل ضغطة (تعديل / إعدادات / إضافة) — بلا تجاوز بجلسة سابقة.
    // نجاح التحقق يثبت الجلسة ليتابع التدفق (دخول وضع التعديل وحفظ التصميم).
    try {
        const st = await wsMasterStatus();
        wsMasterOpen(!(st && st.set), action);
    } catch (e) {
        wsMasterOpen(false, action);
    }
}
