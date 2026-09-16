// views/DetailView6 — Material DetailView distinct v6
// unique: image header + title overlay — hash 3927
export class DetailView6 {
  constructor(props){ this.props = props||{}; this._id='392703'; }
  render(){ const p=this.props; return '<div class="md-card view detailview6 detailview"><div class="md-card-header">DetailView '+(p.title||'DetailView6')+' — image header + title overlay (v6)</div><div class="md-card-content">'+(p.description||'')+' — DetailView specific rendering variant 6</div><div style="font-size:11px;color:var(--md-primary)">DetailView • image header + title overlay</div></div>'; }
}