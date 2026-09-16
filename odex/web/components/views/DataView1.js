// views/DataView1 — Material DataView distinct v1
// unique: title + description + count badge — hash 0da7
export class DataView1 {
  constructor(props){ this.props = props||{}; this._id='0da7e1'; }
  render(){ const p=this.props; return '<div class="md-card view dataview1"><div class="md-card-header">'+(p.title||'DataView1')+'</div><div class="md-card-content">'+(p.description||'')+'</div><div style="font-size:0.75rem;color:var(--md-primary)">count: '+(p.count||0)+'<span style="margin-left:8px;background:var(--md-primary);color:#fff;padding:2px 6px;border-radius:999px">'+(p.badge||'NEW')+'</span></div></div>'; }
}