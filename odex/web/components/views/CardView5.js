// views/CardView5 — Material CardView distinct v5
// unique: vertical timeline with dots — hash dfc0
export class CardView5 {
  constructor(props){ this.props = props||{}; this._id='dfc0d0'; }
  render(){ const p=this.props; return '<div class="md-card view cardview5"><div class="md-card-header">'+(p.title||'CardView5')+' <span style="font-size:11px;background:var(--md-secondary);color:#fff;padding:2px 6px;border-radius:999px">NEW</span></div><div style="font-size:13px">'+(p.description||'')+'</div></div>'; }
}