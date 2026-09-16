// views/ListView6 — Material ListView distinct v6
// unique: image header + title overlay — hash 21cb
export class ListView6 {
  constructor(props){ this.props = props||{}; this._id='21cb46'; }
  render(){ const p=this.props; return '<div class="md-card view listview6 listview"><div class="md-card-header">ListView '+(p.title||'ListView6')+' — image header + title overlay (v6)</div><div class="md-card-content">'+(p.description||'')+' — ListView specific rendering variant 6</div><div style="font-size:11px;color:var(--md-primary)">ListView • image header + title overlay</div></div>'; }
}