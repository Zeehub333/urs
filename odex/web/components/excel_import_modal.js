/* ============================================================================
 * excel_import_modal.js — نافذة الاستيراد الجماعي واستخراج البيانات من ملف إكسل
 * ========================================================================== */
(function (global) {
    'use strict';

    function esc(s) {
        return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function open(opts) {
        opts = opts || {};
        var columns = opts.columns || [];
        var initialColumn = opts.initialColumn || '';
        var onApply = opts.onApply || function () {};
        var endpoint = opts.importEndpoint || '/api/search/import-excel/';

        var existing = document.getElementById('fmlkXlModal');
        if (existing) existing.remove();

        var md = document.createElement('div');
        md.id = 'fmlkXlModal';
        md.className = 'fixed inset-0 z-[200] flex items-center justify-center p-4 bg-slate-900/50 backdrop-blur-sm animate-fade-in';
        md.setAttribute('dir', 'rtl');

        md.innerHTML = `
            <div class="bg-white rounded-2xl shadow-2xl w-full max-w-2xl overflow-hidden flex flex-col max-h-[90vh]">
                <!-- Header -->
                <div class="flex items-center justify-between px-6 py-4 border-b border-slate-100 bg-slate-50/50">
                    <div class="flex items-center gap-3">
                        <div class="w-10 h-10 rounded-xl bg-emerald-100 text-emerald-600 flex items-center justify-center text-lg shadow-sm">
                            <span class="relative inline-flex items-center"><i class="fa-solid fa-file-excel"></i><i class="fa-solid fa-magnifying-glass absolute -bottom-1 -left-1 text-[8px] bg-white rounded-full ring-1 ring-emerald-300 p-[1.5px]"></i></span>
                        </div>
                        <div>
                            <h3 class="font-bold text-slate-800 text-base">البحث الجماعي واستخراج البيانات من ملف إكسل</h3>
                            <p class="text-xs text-slate-500">قم بررفع ملف إكسل واختيار العمود لاستخراج القيم وتحويلها لبحث متعدد بمنطق (أو)</p>
                        </div>
                    </div>
                    <button type="button" id="xlClose" class="w-8 h-8 rounded-full hover:bg-slate-200/60 text-slate-400 hover:text-slate-600 flex items-center justify-center transition">
                        <i class="fa-solid fa-xmark"></i>
                    </button>
                </div>

                <!-- Body -->
                <div class="p-6 overflow-y-auto custom-scroll space-y-6 flex-1 text-xs">
                    <!-- 1. العمود المراد البحث فيه -->
                    <div class="space-y-2">
                        <label class="font-bold text-slate-700 flex items-center gap-1.5">
                            <i class="fa-regular fa-square-check text-indigo-600"></i> 1. العمود المراد البحث فيه داخل الجدول:
                        </label>
                        <select id="xlTargetCol" class="w-full bg-slate-50 border border-slate-200 rounded-xl px-3 py-2.5 text-slate-700 focus:outline-none focus:border-indigo-500 font-bold">
                            <option value="">-- اختر عمود الجدول المستهدف --</option>
                            ${columns.map(c => {
                                var val = String(c.alias || c.name || '');
                                var lbl = c.alias ? `${c.alias} (${c.name})` : c.name;
                                return `<option value="${esc(val)}" ${val === initialColumn ? 'selected' : ''}>${esc(lbl)}</option>`;
                            }).join('')}
                        </select>
                    </div>

                    <!-- 2. اختيار ملف الإكسل -->
                    <div class="space-y-2">
                        <label class="font-bold text-slate-700 flex items-center gap-1.5">
                            <i class="fa-solid fa-upload text-emerald-600"></i> 2. اختيار ملف الإكسل (Excel / CSV):
                        </label>
                        <div id="xlDropZone" class="border-2 border-dashed border-emerald-300 rounded-2xl bg-emerald-50/30 hover:bg-emerald-50/60 transition p-6 text-center cursor-pointer flex flex-col items-center justify-center gap-2">
                            <div class="w-12 h-12 rounded-full bg-emerald-100 text-emerald-600 flex items-center justify-center text-xl shadow-inner">
                                <i class="fa-solid fa-cloud-arrow-up"></i>
                            </div>
                            <div>
                                <span id="xlFileName" class="font-bold text-slate-700 text-sm">انقر هنا لاختيار ملف إكسل أو سحبه وإفلاته</span>
                                <p class="text-[11px] text-slate-400 mt-0.5">يدعم ملفات: xlsx, .xls, .csv, .txt</p>
                            </div>
                            <input type="file" id="xlFileInput" accept=".xlsx,.xlsm,.xls,.csv,.txt" class="hidden">
                        </div>
                    </div>

                    <!-- 3. عمود الإكسل المراد استخراج القيم منه -->
                    <div class="space-y-2">
                        <label class="font-bold text-slate-700 flex items-center gap-1.5">
                            <i class="fa-solid fa-list-ol text-amber-600"></i> 3. عمود الإكسل المراد استخراج القيم منه للبحث:
                        </label>
                        <select id="xlExcelCol" disabled class="w-full bg-slate-100 border border-slate-200 rounded-xl px-3 py-2.5 text-slate-400 focus:outline-none cursor-not-allowed">
                            <option value="">-- قم برفع الملف أولاً لتحديد أعمدة الإكسل --</option>
                        </select>
                    </div>

                    <!-- 4. القيم المستخرجة الفريدة -->
                    <div id="xlPreviewBox" class="hidden space-y-3 bg-slate-50 border border-slate-200 rounded-2xl p-4">
                        <div class="flex flex-wrap items-center justify-between gap-3">
                            <div class="flex items-center gap-2 font-bold text-slate-700">
                                <i class="fa-solid fa-circle-check text-emerald-600"></i>
                                <span id="xlCountLbl">القيم المستخرجة الفريدة (0 قيمة):</span>
                            </div>
                            <div class="flex items-center gap-2">
                                <span class="text-slate-500">الفاصل بين القيم:</span>
                                <select id="xlSeparator" class="bg-white border border-slate-200 rounded-lg px-2 py-1 text-slate-700 focus:outline-none">
                                    <option value=", ">، (فاصلة عربية / مسافة)</option>
                                    <option value=",">, (فاصلة إنجليزية)</option>
                                    <option value="\n">سطر جديد (Newline)</option>
                                    <option value=" ">مسافة (Space)</option>
                                    <option value=";">؛ (فاصلة منقوطة)</option>
                                </select>
                            </div>
                        </div>
                        <textarea id="xlTextArea" rows="4" readonly class="w-full bg-white border border-slate-200 rounded-xl p-3 font-mono text-slate-700 text-xs focus:outline-none custom-scroll resize-none" dir="ltr" placeholder="ستظهر القيم هنا..."></textarea>
                        <p class="text-[11px] text-slate-400 flex items-center gap-1">
                            <i class="fa-solid fa-lightbulb text-amber-500"></i> سيتم تطبيق البحث بمنطق (أو OR) على هذه القيم تلقائياً داخل التقرير / الجدول.
                        </p>
                    </div>
                </div>

                <!-- Footer -->
                <div class="flex items-center justify-between px-6 py-4 border-t border-slate-100 bg-slate-50/50">
                    <button type="button" id="xlCopyBtn" disabled class="px-4 py-2 bg-white hover:bg-slate-100 border border-slate-200 text-slate-700 rounded-xl font-bold flex items-center gap-2 shadow-sm transition disabled:opacity-50 disabled:cursor-not-allowed">
                        <i class="fa-regular fa-copy text-slate-500"></i> نسخ القيم فقط
                    </button>
                    <div class="flex items-center gap-2">
                        <button type="button" id="xlCancelBtn" class="px-4 py-2 bg-slate-200 hover:bg-slate-300 text-slate-700 rounded-xl font-bold transition">
                            إلغاء الأمر
                        </button>
                        <button type="button" id="xlSubmitBtn" disabled class="px-5 py-2 bg-emerald-600 hover:bg-emerald-700 text-white rounded-xl font-bold flex items-center gap-2 shadow-md transition disabled:opacity-50 disabled:cursor-not-allowed">
                            <i class="fa-solid fa-magnifying-glass"></i> تطبيق البحث الجماعي
                        </button>
                    </div>
                </div>
            </div>
        `;

        document.body.appendChild(md);

        var fileInput = md.querySelector('#xlFileInput');
        var dropZone = md.querySelector('#xlDropZone');
        var fileNameLbl = md.querySelector('#xlFileName');
        var excelColSel = md.querySelector('#xlExcelCol');
        var targetColSel = md.querySelector('#xlTargetCol');
        var previewBox = md.querySelector('#xlPreviewBox');
        var countLbl = md.querySelector('#xlCountLbl');
        var textArea = md.querySelector('#xlTextArea');
        var separatorSel = md.querySelector('#xlSeparator');
        var copyBtn = md.querySelector('#xlCopyBtn');
        var submitBtn = md.querySelector('#xlSubmitBtn');
        var closeBtn = md.querySelector('#xlClose');
        var cancelBtn = md.querySelector('#xlCancelBtn');

        var parsedData = { columns: [], valuesByIdx: {}, fileMeta: {} };
        var currentFile = null;

        function closeModal() {
            md.remove();
        }

        closeBtn.onclick = closeModal;
        cancelBtn.onclick = closeModal;
        md.onclick = function (e) { if (e.target === md) closeModal(); };

        dropZone.onclick = function () { fileInput.click(); };
        dropZone.ondragover = function (e) { e.preventDefault(); dropZone.classList.add('border-emerald-500', 'bg-emerald-50'); };
        dropZone.ondragleave = function () { dropZone.classList.remove('border-emerald-500', 'bg-emerald-50'); };
        dropZone.ondrop = function (e) {
            e.preventDefault();
            dropZone.classList.remove('border-emerald-500', 'bg-emerald-50');
            if (e.dataTransfer.files && e.dataTransfer.files[0]) {
                handleFile(e.dataTransfer.files[0]);
            }
        };
        fileInput.onchange = function () {
            if (fileInput.files && fileInput.files[0]) {
                handleFile(fileInput.files[0]);
            }
        };

        function handleFile(file, colIdx) {
            if (file) currentFile = file;
            else file = currentFile;
            if (!file) return;
            fileNameLbl.textContent = file.name;
            var fd = new FormData();
            fd.append('file', file);
            if (colIdx) fd.append('column_index', String(colIdx));

            fetch(endpoint, { method: 'POST', body: fd })
                .then(r => r.json().then(j => ({ ok: r.ok, j })))
                .then(res => {
                    if (!res.ok || res.j.error) throw new Error(res.j.error || 'فشل قراءة الملف');
                    var idx = res.j.column_index;
                    parsedData.valuesByIdx[idx] = res.j.values || [];
                    parsedData.fileMeta[idx] = { truncated: !!res.j.truncated, filename: res.j.filename || file.name };
                    var cols = res.j.columns || [];
                    parsedData.columns = cols;

                    // الخطوة 3: أعمدة الملف الفعلية (العنوان + عينة + العدد)
                    excelColSel.innerHTML = cols.map(function (c) {
                        return `<option value="${c.index}" ${c.index === idx ? 'selected' : ''}>📊 العمود #${c.index}: ${esc(c.header)} — (${esc(c.sample)}…) [${c.count}]</option>`;
                    }).join('');
                    excelColSel.disabled = false;
                    excelColSel.className = 'w-full bg-slate-50 border border-slate-200 rounded-xl px-3 py-2.5 text-slate-700 focus:outline-none focus:border-indigo-500 font-bold';

                    updatePreview(idx);
                })
                .catch(err => {
                    try { if (typeof notify === 'function') notify('خطأ في استيراد الملف: ' + err.message, 'error'); else alert(err.message); } catch (e) {}
                });
        }

        function currentIdx() {
            var v = parseInt(excelColSel.value, 10);
            return isNaN(v) ? null : v;
        }

        function updatePreview(idx) {
            var vals = (idx != null && parsedData.valuesByIdx[idx]) || [];
            if (!vals.length) {
                previewBox.classList.add('hidden');
                submitBtn.disabled = true;
                copyBtn.disabled = true;
                return;
            }
            previewBox.classList.remove('hidden');
            var meta = parsedData.fileMeta[idx] || {};
            countLbl.textContent = `القيم المستخرجة الفريدة (${vals.length} قيمة${meta.truncated ? ' — الأولى فقط' : ''}):`;

            var sep = separatorSel.value;
            if (sep === '\\n') sep = '\n';

            textArea.value = vals.join(sep);
            copyBtn.disabled = false;
            submitBtn.disabled = !targetColSel.value;
        }

        excelColSel.onchange = function () {
            var idx = currentIdx();
            if (idx == null) return;
            // القيم مخزنة من الاستجابة؛ غير المخزن يُجلب بإعادة الرفع مع column_index
            if (parsedData.valuesByIdx[idx]) updatePreview(idx);
            else handleFile(null, idx);
        };

        separatorSel.onchange = function () {
            var idx = currentIdx();
            if (idx != null) updatePreview(idx);
        };

        targetColSel.onchange = function () {
            var idx = currentIdx();
            submitBtn.disabled = !targetColSel.value || idx == null || !(parsedData.valuesByIdx[idx] || []).length;
        };

        copyBtn.onclick = function () {
            textArea.select();
            try {
                document.execCommand('copy');
                if (typeof notify === 'function') notify('تم نسخ القيم بنجاح ✓', 'success');
            } catch (e) {}
        };

        submitBtn.onclick = function () {
            var colKey = targetColSel.value;
            var idx = currentIdx();
            var vals = (idx != null && parsedData.valuesByIdx[idx]) || [];
            if (!colKey || !vals.length) return;
            var meta = parsedData.fileMeta[idx] || {};

            onApply({
                column: colKey,
                values: vals,
                count: vals.length,
                truncated: !!meta.truncated,
                filename: meta.filename || fileNameLbl.textContent
            });
            closeModal();
        };
    }

    global.ExcelImportModal = { open: open };
})(typeof window !== 'undefined' ? window : this);
