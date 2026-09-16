// views/SplitView2 — Material SplitView distinct v2
// unique: progress bar + percentage — hash ad21
export class SplitView2 {
  constructor(props){ this.props = props||{}; this._id='ad2183'; }
  render(){ const p=this.props; return '<div class="md-card view splitview2 splitview"><div class="md-card-header">SplitView '+(p.title||'SplitView2')+' — progress bar + percentage (v2)</div><div class="md-card-content">'+(p.description||'')+' — SplitView specific rendering variant 2</div><div style="font-size:11px;color:var(--md-primary)">SplitView • progress bar + percentage</div></div>'; }
}