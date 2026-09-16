// grids/TreeGrid2 — Material TreeGrid distinct v2
export class TreeGrid2 {
  constructor(props){ this.props=props||{}; }
  render(){ const p=this.props; var items=p.items||[]; var cols=p.cols||3; return '<div class="md-card grid treegrid2"><div style="font-size:11px;color:var(--md-primary);margin-bottom:8px">TreeGrid — editable</div><div style="display:grid;grid-template-columns:repeat('+cols+',1fr);gap:8px">'+items.map(i=>'<div class="md-card" style="padding:8px">'+JSON.stringify(i).slice(0,30)+'</div>').join('')+'</div></div>'; }
}