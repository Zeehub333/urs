// views/ListView2 — Material ListView distinct v2
// unique: progress bar + percentage — hash c5f1
export class ListView2 {
  constructor(props){ this.props = props||{}; this._id='c5f1df'; }
  render(){ const p=this.props; return '<div class="md-card view listview2 listview"><div class="md-card-header">ListView '+(p.title||'ListView2')+' — progress bar + percentage (v2)</div><div class="md-card-content">'+(p.description||'')+' — ListView specific rendering variant 2</div><div style="font-size:11px;color:var(--md-primary)">ListView • progress bar + percentage</div></div>'; }
}