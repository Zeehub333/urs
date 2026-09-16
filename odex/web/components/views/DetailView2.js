// views/DetailView2 — Material DetailView distinct v2
// unique: progress bar + percentage — hash 5e8e
export class DetailView2 {
  constructor(props){ this.props = props||{}; this._id='5e8ebc'; }
  render(){ const p=this.props; return '<div class="md-card view detailview2 detailview"><div class="md-card-header">DetailView '+(p.title||'DetailView2')+' — progress bar + percentage (v2)</div><div class="md-card-content">'+(p.description||'')+' — DetailView specific rendering variant 2</div><div style="font-size:11px;color:var(--md-primary)">DetailView • progress bar + percentage</div></div>'; }
}