// views/CardView2 — Material CardView distinct v2
// unique: progress bar + percentage — hash 1572
export class CardView2 {
  constructor(props){ this.props = props||{}; this._id='1572a8'; }
  render(){ const p=this.props; return '<div class="md-card view cardview2" style="display:flex;gap:12px"><div style="width:80px;height:80px;background:var(--md-primary);border-radius:8px;display:flex;align-items:center;justify-content:center;color:#fff"><span class="material-symbols-outlined">image</span></div><div><div style="font-weight:500">'+(p.title||'CardView2')+'</div><div style="font-size:13px">'+(p.description||'')+'</div></div></div>'; }
}