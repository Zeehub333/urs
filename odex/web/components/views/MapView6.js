// views/MapView6 — Material MapView distinct v6
// unique: image header + title overlay — hash 2a45
export class MapView6 {
  constructor(props){ this.props = props||{}; this._id='2a4595'; }
  render(){ const p=this.props; return '<div class="md-card view mapview6 mapview"><div class="md-card-header">MapView '+(p.title||'MapView6')+' — image header + title overlay (v6)</div><div class="md-card-content">'+(p.description||'')+' — MapView specific rendering variant 6</div><div style="font-size:11px;color:var(--md-primary)">MapView • image header + title overlay</div></div>'; }
}