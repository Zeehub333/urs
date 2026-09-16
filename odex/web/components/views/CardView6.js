// views/CardView6 — Material CardView distinct v6
// unique: image header + title overlay — hash e1f6
export class CardView6 {
  constructor(props){ this.props = props||{}; this._id='e1f6d6'; }
  render(){ const p=this.props; return '<div class="md-card view cardview6" style="background:var(--md-primary);color:#fff"><div style="font-weight:500">'+(p.title||'CardView6')+'</div><div style="opacity:.8">'+(p.description||'')+'</div><div style="margin-top:8px;font-size:24px">'+(p.count||0)+'</div></div>'; }
}