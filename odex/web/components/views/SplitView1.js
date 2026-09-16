// views/SplitView1 — Material SplitView distinct v1
// unique: title + description + count badge — hash fc04
export class SplitView1 {
  constructor(props){ this.props = props||{}; this._id='fc042d'; }
  render(){ const p=this.props; return '<div class="md-card view splitview1 splitview"><div class="md-card-header">SplitView '+(p.title||'SplitView1')+' — title + description + count badge (v1)</div><div class="md-card-content">'+(p.description||'')+' — SplitView specific rendering variant 1</div><div style="font-size:11px;color:var(--md-primary)">SplitView • title + description + count badge</div></div>'; }
}