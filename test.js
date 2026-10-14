function splitSql(sql) {
    let parts = [];
    let d = 0, q = null, i = 0, start = 0, n = sql.length;
    let lastMerge = null;
    while(i < n) {
        let ch = sql[i];
        if(q) { if(ch === q) { if(q === "'" && sql[i+1] === "'") i++; else q = null; } i++; continue; }
        if(ch === "'" || ch === '"' || ch === '`') { q = ch; i++; continue; }
        if(ch === '[') { let j = sql.indexOf(']', i+1); i = j < 0 ? n : j+1; continue; }
        if(ch === '(') { d++; i++; continue; }
        if(ch === ')') { d = Math.max(0, d-1); i++; continue; }
        if(d === 0) {
            let mU = sql.slice(i).match(/^UNION(?:\s+ALL)?\b/i);
            if(mU) {
                parts.push({sql: sql.slice(start, i).trim(), merge: lastMerge});
                lastMerge = /ALL/i.test(mU[0]) ? 'union_all' : 'union_partial';
                i += mU[0].length;
                start = i;
                continue;
            }
        }
        i++;
    }
    parts.push({sql: sql.slice(start).trim(), merge: lastMerge});
    return parts;
}

const fs = require('fs');
const sql = fs.readFileSync('test.sql', 'utf8');
const parts = splitSql(sql);

function splitByComma(str) {
    let parts = [];
    let d = 0, q = null, start = 0;
    for(let i=0; i<str.length; i++) {
        let ch = str[i];
        if(q) { if(ch === q) { if(q === "'" && str[i+1] === "'") i++; else q = null; } continue; }
        if(ch === "'" || ch === '"' || ch === '`') { q = ch; continue; }
        if(ch === '[') { let j = str.indexOf(']', i+1); i = j < 0 ? str.length : j; continue; }
        if(ch === '(') { d++; continue; }
        if(ch === ')') { d = Math.max(0, d-1); continue; }
        if(d === 0 && ch === ',') {
            parts.push(str.slice(start, i).trim());
            start = i + 1;
        }
    }
    parts.push(str.slice(start).trim());
    return parts.filter(Boolean);
}

for(let p = 0; p < parts.length; p++) {
    const pt = parts[p];
    console.log('PART ' + p + ' MERGE: ' + pt.merge);
    console.log('SQL:', pt.sql.substring(0, 50) + '...');
    console.log('SQL END:', pt.sql.substring(pt.sql.length - 50));
    let mG = pt.sql.match(/\bGROUP\s+BY\s+([^]+?)(?:\bHAVING\b|\bORDER\s+BY\b|\bLIMIT\b|$)/i);
    if(mG && p === 0) {
        console.log('GROUP BY:', splitByComma(mG[1]));
    }
}
